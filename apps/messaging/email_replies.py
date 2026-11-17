"""Copy replies to app-sent emails from the sender's Gmail into the person's activity.

Off unless an administrator turns on "Email replies" in Connections AND the staff
member reconnected Gmail with read access and left "copy replies" on. Only the
conversations the app started are opened.
"""
from __future__ import annotations

import base64
import re
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import parseaddr

from django.utils import timezone

from . import google, store
from .models import ConnectionSetting, EmailReply, GmailConnection, SentEmail

WINDOW = timedelta(days=60)
RECHECK = timedelta(minutes=10)


def site_enabled():
    row = ConnectionSetting.objects.filter(key="gmail_replies").first()
    return bool(row and row.values.get("enabled"))


def set_enabled(*, actor, enabled):
    row = ConnectionSetting.objects.filter(key="gmail_replies").first()
    values = dict(row.values if row else {}, enabled=bool(enabled))
    store.save(key="gmail_replies", actor=actor, values=values)


def _header(message, name):
    for item in message.get("payload", {}).get("headers", []):
        if item.get("name", "").lower() == name.lower():
            return item.get("value", "")
    return ""


def _decode(data):
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def _text(part):
    """Plain text of a message part (falls back to stripped HTML)."""
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data", "")
    if mime == "text/plain" and data:
        return _decode(data)
    found_html = ""
    for child in part.get("parts", []) or []:
        text = _text(child)
        if text and child.get("mimeType", "").startswith("text/plain"):
            return text
        if text and not found_html:
            found_html = text
    if not found_html and mime == "text/html" and data:
        html = _decode(data)
        html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
        html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
        return re.sub(r"<[^>]+>", "", html)
    return found_html


def strip_quoted(text):
    """Keep the new part of a reply, not the quoted earlier email."""
    lines = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if re.match(r"^\s*On .+wrote:\s*$", line) or line.strip().startswith("-----Original Message"):
            break
        if line.startswith(">"):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:4000]


def check_sent(row, connection, token):
    data = google.thread(connection, row.thread_id, token=token)
    own = connection.google_email.lower()
    added = 0
    for message in data.get("messages", []):
        sender = parseaddr(_header(message, "From"))[1].lower()
        if not sender or sender == own or message.get("id") == row.provider_reference:
            continue
        when = datetime.fromtimestamp(int(message.get("internalDate", "0")) / 1000, tz=dt_timezone.utc)
        if row.sent_at and when < row.sent_at:
            continue
        reply, created = EmailReply.objects.get_or_create(gmail_message_id=message["id"][:80], defaults={
            "sent_email": row, "from_address": sender[:254], "subject": _header(message, "Subject")[:200],
            "body": strip_quoted(_text(message.get("payload", {}))) or message.get("snippet", "")[:500],
            "received_at": when})
        if not created:
            continue
        added += 1
        if row.participant_id:
            from apps.crm.services import record_app_message
            try:
                reply.interaction = record_app_message(
                    actor=row.sender, participant_id=row.participant_id, activity_type="email",
                    summary=("Email reply: " + (reply.subject or row.subject))[:120], notes=reply.body,
                    provider="gmail", reference=f"gmail:{reply.gmail_message_id}",
                    operation_id=uuid.uuid5(uuid.NAMESPACE_URL, f"btw-gmail-reply-{reply.gmail_message_id}"),
                    direction="inbound")
                reply.save(update_fields=["interaction"])
            except Exception:
                pass
    return added


def check_recent(limit=40):
    """Look for new replies in recently sent app emails. Returns how many were added."""
    if not (site_enabled() and google.configured()):
        return 0
    now = timezone.now()
    connections = {c.user_id: c for c in GmailConnection.objects.filter(track_replies=True, needs_reconnect=False)
                   .exclude(refresh_token_encrypted="") if c.can_read}
    if not connections:
        return 0
    rows = (SentEmail.objects.filter(status=SentEmail.Status.SENT, sender_id__in=connections.keys(),
                                     sent_at__gte=now - WINDOW).exclude(thread_id="")
            .exclude(replies_checked_at__gt=now - RECHECK).order_by("replies_checked_at")[:limit])
    tokens, added = {}, 0
    for row in rows:
        connection = connections[row.sender_id]
        try:
            if row.sender_id not in tokens:
                tokens[row.sender_id] = google.access_token(connection)
            added += check_sent(row, connection, tokens[row.sender_id])
        except google.GmailError as exc:
            if exc.reconnect:
                connection.needs_reconnect, connection.last_error = True, str(exc)[:240]
                connection.save(update_fields=["needs_reconnect", "last_error"])
                connections.pop(row.sender_id, None)
            continue
        finally:
            SentEmail.objects.filter(pk=row.pk).update(replies_checked_at=now)
    return added
