"""Ask EZ Texting whether texts it accepted were actually delivered.

EZ Texting answers per message through its message report's delivery responses
(statuses: delivered, bounced, queued, not_sent). Each of our texts goes to one
number, so a message with any "delivered" response was delivered, and one with a
"bounced" or "not_sent" response was not.
"""
from __future__ import annotations

import base64
import json
import socket
from datetime import timedelta
from urllib import error, parse, request

from django.conf import settings
from django.utils import timezone

from apps.events import sms_providers

from .models import TextDeliveryResult

REPORT_URL = "https://a.eztexting.com/v1/message-reports/{id}/responses/delivery"
GIVE_UP_AFTER = timedelta(days=3)
RECHECK_EVERY = timedelta(minutes=10)


def ez_message_id(reference):
    if not reference or not reference.startswith("eztexting:"):
        return ""
    value = reference.split(":", 1)[1]
    return value if value and value != "accepted" else ""


def _count(payload):
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    for key in ("totalElements", "total", "count"):
        if isinstance(payload.get(key), int):
            return payload[key]
    for key in ("content", "items", "data", "responses"):
        if isinstance(payload.get(key), list):
            return len(payload[key])
    return 0


def _responses(message_id, status):
    username, password, _source = sms_providers.ez_credentials()
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    url = REPORT_URL.format(id=parse.quote(message_id, safe="")) + "?" + parse.urlencode({"filters[status][eq]": status, "size": 10})
    req = request.Request(url, headers={"Authorization": f"Basic {token}", "Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=10) as response:
            return _count(json.loads(response.read() or b"{}"))
    except error.HTTPError as exc:
        if exc.code == 404:
            return 0  # Report not ready yet.
        raise ConnectionError(f"EZ Texting report unavailable ({exc.code})") from None
    except (error.URLError, socket.timeout, TimeoutError, ValueError):
        raise ConnectionError("EZ Texting report unavailable") from None


def check(reference, *, now=None):
    """Update and return the TextDeliveryResult for one sent text (or None if not checkable)."""
    message_id = ez_message_id(reference)
    if not message_id:
        return None
    now = now or timezone.now()
    row, _ = TextDeliveryResult.objects.get_or_create(provider_reference=reference[:160])
    if row.result != TextDeliveryResult.Result.PENDING:
        return row
    if row.checked_at and now - row.checked_at < RECHECK_EVERY:
        return row
    if now - row.first_seen_at > GIVE_UP_AFTER:
        row.detail = "No answer from the carrier after 3 days"
        row.save(update_fields=["detail"])
        return row
    row.checks += 1
    row.checked_at = now
    try:
        if _responses(message_id, "delivered"):
            row.result, row.detail = TextDeliveryResult.Result.DELIVERED, ""
        elif _responses(message_id, "bounced"):
            row.result, row.detail = TextDeliveryResult.Result.FAILED, "The carrier bounced it"
        elif _responses(message_id, "not_sent"):
            row.result, row.detail = TextDeliveryResult.Result.FAILED, "EZ Texting did not send it (opted out or blocked)"
    except ConnectionError as exc:
        row.detail = str(exc)[:160]
    row.save()
    return row


def check_recent(limit=50):
    """Check texts sent in the last 3 days that don't have a final answer yet."""
    if sms_providers.provider_name() != "eztexting" or not sms_providers.is_configured():
        return 0
    from apps.events.models import OperationalNotificationDelivery, TicketDelivery
    since = timezone.now() - GIVE_UP_AFTER
    final = TextDeliveryResult.objects.exclude(result=TextDeliveryResult.Result.PENDING).values("provider_reference")
    refs = []
    for model in (TicketDelivery, OperationalNotificationDelivery):
        refs += list(model.objects.filter(channel="sms", status="sent", sent_at__gte=since,
                                          provider_reference__startswith="eztexting:")
                     .exclude(provider_reference__in=final).values_list("provider_reference", flat=True)[:limit])
    checked = 0
    for reference in refs[:limit]:
        if check(reference):
            checked += 1
    return checked
