"""Google sign-in and Gmail sending for "Connect your Gmail".

Uses Google's documented OAuth 2.0 web-server flow with the narrow ``gmail.send``
scope. Only when an administrator turns on "Email replies" does the app also ask
for ``gmail.readonly``, and then it only opens the conversations it started.
"""
from __future__ import annotations

import base64
import json
import secrets
import socket
from urllib import error, parse, request

from django.conf import settings
from django.utils import timezone

from . import crypto

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
THREAD_URL = "https://gmail.googleapis.com/gmail/v1/users/me/threads/"
SCOPES = "openid email https://www.googleapis.com/auth/gmail.send"
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class GmailError(Exception):
    """Sending failed; ``reconnect`` means the person must connect again."""

    def __init__(self, message, *, reconnect=False, uncertain=False):
        super().__init__(message)
        self.reconnect = reconnect
        self.uncertain = uncertain


def configured():
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def encrypt(token):
    return crypto.encrypt(token, "gmail-token")


def decrypt(value):
    return crypto.decrypt(value, "gmail-token")


def new_state():
    return secrets.token_urlsafe(32)


def authorization_url(*, redirect_uri, state, login_hint="", read=False):
    params = {"client_id": settings.GOOGLE_OAUTH_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code",
              "scope": SCOPES + (" " + READ_SCOPE if read else ""), "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
              "state": state}
    if login_hint:
        params["login_hint"] = login_hint
    if settings.GOOGLE_OAUTH_ALLOWED_DOMAIN:
        params["hd"] = settings.GOOGLE_OAUTH_ALLOWED_DOMAIN
    return AUTH_URL + "?" + parse.urlencode(params)


def _post_form(url, fields):
    req = request.Request(url, data=parse.urlencode(fields).encode("utf-8"),
                          headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with request.urlopen(req, timeout=10) as response:
            return json.loads(response.read() or b"{}")
    except error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        detail = body.get("error", f"HTTP {exc.code}")
        if body.get("error_description"):
            detail += f" ({body['error_description'][:120]})"
        raise GmailError(detail, reconnect=body.get("error") == "invalid_grant") from None
    except (error.URLError, socket.timeout, TimeoutError):
        raise GmailError("Google could not be reached") from None


def _id_token_claims(id_token):
    # The token came straight from Google's token endpoint over HTTPS, so its claims
    # can be read without separately verifying the signature (Google's guidance).
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


def exchange_code(*, code, redirect_uri):
    """Return ``(email, refresh_token, scopes)`` or raise ``GmailError``."""
    data = _post_form(TOKEN_URL, {"code": code, "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                                  "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                                  "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    claims = _id_token_claims(data.get("id_token", ""))
    email = (claims.get("email") or "").lower()
    if not email or not claims.get("email_verified"):
        raise GmailError("Google did not confirm an email address")
    domain = settings.GOOGLE_OAUTH_ALLOWED_DOMAIN
    if domain and email.split("@")[-1] != domain:
        raise GmailError(f"Use your @{domain} Google account")
    scopes = data.get("scope", "")
    if "gmail.send" not in scopes:
        raise GmailError("Permission to send email was not granted")
    refresh = data.get("refresh_token", "")
    if not refresh:
        raise GmailError("Google did not return a lasting permission; please try again")
    return email, refresh, scopes


def access_token(connection):
    refresh = decrypt(connection.refresh_token_encrypted)
    if not refresh:
        raise GmailError("Connection needs to be set up again", reconnect=True)
    data = _post_form(TOKEN_URL, {"refresh_token": refresh, "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                                  "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET, "grant_type": "refresh_token"})
    token = data.get("access_token", "")
    if not token:
        raise GmailError("Google did not return access")
    return token


def send(connection, message, *, result=None):
    """Send a ``django.core.mail.EmailMessage`` as the connected person; return Gmail's id."""
    if not configured():
        raise GmailError("Gmail connection is not set up on this site")
    if not connection.active:
        raise GmailError("Connect your Gmail again", reconnect=True)
    token = access_token(connection)
    raw = base64.urlsafe_b64encode(message.message().as_bytes()).decode("ascii")
    req = request.Request(SEND_URL, data=json.dumps({"raw": raw}).encode("utf-8"), method="POST",
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read() or b"{}")
    except error.HTTPError as exc:
        if exc.code in (401, 403):
            raise GmailError("Google refused the send; connect your Gmail again", reconnect=True) from None
        if exc.code >= 500:
            raise GmailError("Gmail did not confirm the send", uncertain=True) from None
        raise GmailError("Gmail rejected the message") from None
    except (error.URLError, socket.timeout, TimeoutError):
        raise GmailError("Gmail did not confirm the send", uncertain=True) from None
    reference = data.get("id", "")
    if result is not None:
        result["thread_id"] = data.get("threadId", "")
    if not reference:
        raise GmailError("Gmail did not confirm the send", uncertain=True)
    connection.last_sent_at = timezone.now()
    connection.last_error = ""
    connection.save(update_fields=["last_sent_at", "last_error"])
    return reference


def thread(connection, thread_id, *, token=None):
    """One Gmail conversation the app started (needs read access)."""
    token = token or access_token(connection)
    req = request.Request(THREAD_URL + parse.quote(thread_id, safe="") + "?format=full",
                          headers={"Authorization": f"Bearer {token}"})
    try:
        with request.urlopen(req, timeout=20) as response:
            return json.loads(response.read() or b"{}")
    except error.HTTPError as exc:
        if exc.code == 404:
            return {}
        if exc.code in (401, 403):
            raise GmailError("Google refused to share replies; connect your Gmail again", reconnect=True) from None
        raise GmailError(f"Gmail error {exc.code}") from None
    except (error.URLError, socket.timeout, TimeoutError):
        raise GmailError("Gmail could not be reached") from None


def revoke(connection):
    token = decrypt(connection.refresh_token_encrypted)
    if token:
        try:
            _post_form(REVOKE_URL, {"token": token})
        except GmailError:
            pass  # Clearing our copy still stops all future sends.
