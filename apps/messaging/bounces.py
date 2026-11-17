"""Bounced automatic email: read the "couldn't deliver" notices that land in noreply@,
flag the address, and stop sending to it until staff fix it.

Uses the same Google sign-in saved for automatic email (IMAP over SSL to Gmail).
Only bounce notices are opened; they are left marked as read.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import socket
from email.utils import getaddresses, parseaddr

from django.utils import timezone

from . import store

log = logging.getLogger(__name__)
GMAIL_HOSTS = {"smtp.gmail.com", "smtp-relay.gmail.com"}
ADDRESS = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


def normalize(address):
    return (address or "").strip().lower()


def bounced(address):
    from .models import EmailBounce
    address = normalize(address)
    if not address:
        return False
    try:
        return EmailBounce.objects.filter(address=address, cleared_at__isnull=True).exists()
    except Exception:  # a failed check never blocks an email
        return False


def record(address, reason, *, when=None):
    from .models import EmailBounce
    address = normalize(address)
    if not ADDRESS.fullmatch(address):
        return None
    row, created = EmailBounce.objects.get_or_create(address=address, defaults={"reason": reason[:240],
                                                                                  "bounced_at": when or timezone.now()})
    if not created:
        row.reason, row.bounced_at, row.cleared_at, row.cleared_by = reason[:240], when or timezone.now(), None, None
        row.save(update_fields=["reason", "bounced_at", "cleared_at", "cleared_by"])
    return row


def clear(address, actor):
    from .models import EmailBounce
    return EmailBounce.objects.filter(address=normalize(address), cleared_at__isnull=True).update(
        cleared_at=timezone.now(), cleared_by=actor)


def _text(message):
    parts = []
    for part in message.walk():
        if part.get_content_type() in ("text/plain", "message/delivery-status"):
            payload = part.get_payload(decode=True)
            if payload is None and part.is_multipart():
                payload = "\n".join(str(p) for p in part.get_payload()).encode()
            if payload:
                parts.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
    return "\n".join(parts)


def parse(raw, *, own_address=""):
    """``(failed addresses, reason)`` from one bounce notice, or ``([], "")`` if it isn't one."""
    message = email.message_from_bytes(raw)
    sender = normalize(parseaddr(message.get("From", ""))[1])
    subject = message.get("Subject", "")
    if not (sender.startswith(("mailer-daemon@", "postmaster@")) or "delivery status notification" in subject.lower()
            or "undeliverable" in subject.lower() or "undelivered" in subject.lower()):
        return [], ""
    failed = [normalize(a) for _, a in getaddresses(message.get_all("X-Failed-Recipients", []))]
    body = _text(message)
    if not failed:
        failed = [normalize(m) for m in re.findall(r"(?im)^final-recipient:\s*rfc822;\s*(\S+)", body)]
    if not failed:
        match = re.search(r"(?is)(?:wasn't delivered to|couldn't be delivered to|could not be delivered to)\s+<?(" + ADDRESS.pattern + ")", body)
        failed = [normalize(match.group(1))] if match else []
    own = normalize(own_address)
    failed = sorted({a for a in failed if ADDRESS.fullmatch(a) and a != own and not a.startswith("mailer-daemon@")})
    reason = ""
    for pattern in (r"(?im)^diagnostic-code:\s*(.+)$", r"(?is)(address not found.{0,200}?)\n\n", r"(?im)^status:\s*(5\.\S+)"):
        match = re.search(pattern, body)
        if match:
            reason = re.sub(r"\s+", " ", match.group(1)).strip()[:200]
            break
    return failed, reason or "The receiving mail server rejected the message."


def check_inbox(limit=50):
    """Read new bounce notices in the noreply@ mailbox. Returns how many addresses were flagged."""
    config = store.email_settings()
    host = (config.get("host") or "").lower()
    if not config.get("source") or host not in GMAIL_HOSTS or not config.get("username") or not config.get("password"):
        return 0
    flagged = 0
    try:
        box = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20)
    except (OSError, socket.timeout) as exc:
        log.warning("BOUNCE_CHECK_UNREACHABLE %s", type(exc).__name__)
        return 0
    try:
        box.login(config["username"], config["password"])
        box.select("INBOX")
        status, data = box.search(None, '(UNSEEN OR FROM "mailer-daemon" SUBJECT "Delivery Status Notification")')
        ids = (data[0].split() if status == "OK" and data else [])[-limit:]
        for message_id in ids:
            status, fetched = box.fetch(message_id, "(RFC822)")
            if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                continue
            failed, reason = parse(fetched[0][1], own_address=config["username"])
            for address in failed:
                if record(address, reason):
                    flagged += 1
    except imaplib.IMAP4.error as exc:
        log.warning("BOUNCE_CHECK_FAILED %s", str(exc)[:120])
    except (OSError, socket.timeout) as exc:
        log.warning("BOUNCE_CHECK_FAILED %s", type(exc).__name__)
    finally:
        try:
            box.logout()
        except Exception:
            pass
    return flagged
