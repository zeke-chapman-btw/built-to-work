"""Text-message providers.

Credentials come only from deployment settings (Render environment), never from
model fields or code. Each sender returns the provider's message reference and
raises the same error types the delivery queue already understands:

* ``ValueError``      - the message or number was rejected; do not retry.
* ``ConnectionError`` - not configured, rate limited or unreachable; retry later.
* ``TimeoutError``    - the provider may or may not have accepted it; staff review.
"""
from __future__ import annotations

import base64
import json
import re
import socket
from urllib import error, request
from urllib.parse import urlsplit

from django.conf import settings

EZTEXTING_MESSAGES_URL = "https://a.eztexting.com/v1/messages"


def provider_name():
    """``eztexting`` when EZ Texting sign-in settings exist, else the generic endpoint."""
    chosen = (getattr(settings, "BTW_SMS_PROVIDER", "") or "").strip().lower()
    if chosen:
        return chosen
    if getattr(settings, "EZTEXTING_USERNAME", "") and getattr(settings, "EZTEXTING_PASSWORD", ""):
        return "eztexting"
    return "generic"


def is_configured():
    name = provider_name()
    if name == "eztexting":
        return bool(getattr(settings, "EZTEXTING_USERNAME", "") and getattr(settings, "EZTEXTING_PASSWORD", ""))
    return bool(getattr(settings, "BTW_SMS_PROVIDER_URL", "") and getattr(settings, "BTW_SMS_PROVIDER_TOKEN", ""))


def us_number(value):
    """Return an 11-digit US number (``1XXXXXXXXXX``) or raise ``ValueError``."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) != 11 or not digits.startswith("1"):
        raise ValueError("Mobile number must be a 10-digit US number")
    return digits


def send_sms(destination, body, *, idempotency_key):
    name = provider_name()
    if name == "eztexting":
        return _send_eztexting(destination, body)
    if name == "generic":
        return _send_generic(destination, body, idempotency_key=idempotency_key)
    raise ConnectionError("Unknown SMS provider setting")


def _post(url, payload, headers, *, timeout):
    req = request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={
        "Content-Type": "application/json", "Accept": "application/json", **headers,
    }, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, response.headers, response.read()
    except error.HTTPError as exc:
        if exc.code == 429:
            raise ConnectionError("SMS provider rate limited the request") from None
        if exc.code in (401, 403):
            raise ConnectionError("SMS provider sign-in was refused; check the texting settings") from None
        if exc.code >= 500:
            raise TimeoutError("SMS provider acceptance is uncertain") from None
        raise ValueError("SMS provider rejected the message") from None
    except error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise TimeoutError("SMS response timed out") from None
        raise ConnectionError("SMS provider unavailable") from None
    except (TimeoutError, socket.timeout):
        raise TimeoutError("SMS response timed out") from None


def _send_eztexting(destination, body):
    username = getattr(settings, "EZTEXTING_USERNAME", "")
    password = getattr(settings, "EZTEXTING_PASSWORD", "")
    if not username or not password:
        raise ConnectionError("SMS delivery is not configured")
    number = us_number(destination)
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    status, _headers, raw = _post(EZTEXTING_MESSAGES_URL, {"toNumbers": [number], "message": body},
                                  {"Authorization": f"Basic {token}"}, timeout=10)
    if status not in (200, 201, 202):
        raise TimeoutError("SMS provider acceptance is uncertain")
    try:
        reference = str(json.loads(raw or b"{}").get("id") or "")
    except (ValueError, AttributeError):
        reference = ""
    # Accepted by EZ Texting is not the same as delivered to the phone.
    return (f"eztexting:{reference}" if reference else "eztexting:accepted")[:160]


def _send_generic(destination, body, *, idempotency_key):
    endpoint = getattr(settings, "BTW_SMS_PROVIDER_URL", "")
    token = getattr(settings, "BTW_SMS_PROVIDER_TOKEN", "")
    if not endpoint or not token:
        raise ConnectionError("SMS delivery is not configured")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("SMS provider endpoint must use HTTPS without embedded credentials")
    status, headers, _raw = _post(endpoint, {"to": destination, "message": body, "idempotency_key": idempotency_key},
                                  {"Authorization": f"Bearer {token}", "Idempotency-Key": idempotency_key}, timeout=5)
    if status not in (200, 201, 202):
        raise RuntimeError("SMS provider did not accept the message")
    return headers.get("X-Message-ID", idempotency_key)[:160]
