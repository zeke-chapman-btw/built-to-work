"""One-to-one texting with participants, and routing replies to the right person."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.events import sms_providers

from . import store
from .models import ConnectionSetting, TextMessage, TextOptOut

ROUTE_WINDOW = timedelta(days=30)
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP", "YES"}
WEBHOOK_URL = "https://a.eztexting.com/v1/webhooks/subscriptions"


class TextError(Exception):
    pass


def phone_key(value):
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else ""


def opted_out(phone):
    key = phone_key(phone)
    return bool(key) and TextOptOut.objects.filter(phone=key).exists()


def reply_settings():
    row = ConnectionSetting.objects.filter(key="replies").first()
    return row, (row.values if row else {})


def default_owner():
    _row, values = reply_settings()
    owner_id = values.get("owner_id")
    if not owner_id:
        return None
    return get_user_model().objects.filter(pk=owner_id, is_active=True, is_staff=True).first()


def ready():
    return sms_providers.provider_name() == "eztexting" and sms_providers.is_configured()


def send_to_participant(*, actor, participant, body, replies_to, operation_id):
    """Send one text now and record who replies should go to."""
    key = phone_key(participant.contact_phone)
    if not key:
        raise TextError("This person has no mobile number on file.")
    if opted_out(key):
        raise TextError("This number replied STOP, so it can't be texted until they reply START.")
    if not ready():
        raise TextError("Texting isn't set up yet (Settings → Connections).")
    try:
        with transaction.atomic():
            row = TextMessage.objects.create(direction=TextMessage.Direction.OUT, phone=key, participant=participant,
                                             body=body, sent_by=actor, replies_to=replies_to or actor,
                                             state=TextMessage.State.SENT, operation_id=operation_id)
    except IntegrityError:
        return TextMessage.objects.get(operation_id=operation_id)  # A double click: already sent.
    try:
        reference = sms_providers.send_sms(key, body, idempotency_key=f"text-{row.pk}")
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as exc:
        row.state, row.error = TextMessage.State.FAILED, str(exc)[:240]
        row.save(update_fields=["state", "error"])
        raise TextError(f"The text was not sent: {exc}")
    row.provider_reference = reference[:160]
    row.save(update_fields=["provider_reference"])
    _log(row, actor, direction="outbound", summary=body[:120])
    return row


def _log(row, actor, *, direction, summary):
    """Add the text to the person's touchpoints. History trouble never undoes a send."""
    if not (row.participant_id and actor):
        return
    from apps.crm.services import record_app_message
    try:
        interaction = record_app_message(actor=actor, participant_id=row.participant_id, activity_type="sms",
            summary=summary or "Text message", notes=row.body, provider="eztexting",
            reference=row.provider_reference or f"text-{row.pk}", operation_id=_operation(row), direction=direction)
    except Exception:
        return
    row.interaction = interaction
    row.save(update_fields=["interaction"])


def _operation(row):
    import uuid
    return row.operation_id or uuid.uuid5(uuid.NAMESPACE_URL, f"btw-text-{row.pk}")


def route(phone):
    """Who an incoming text from ``phone`` goes to."""
    recent = (TextMessage.objects.filter(direction=TextMessage.Direction.OUT, phone=phone, state=TextMessage.State.SENT,
                                         created_at__gte=timezone.now() - ROUTE_WINDOW, replies_to__is_active=True)
              .select_related("replies_to").order_by("-created_at").first())
    return recent.replies_to if recent else default_owner()


def _participant_for(phone):
    from apps.participants.models import Participant
    matches = [p for p in Participant.objects.filter(contact_phone__endswith=phone, archived_at__isnull=True)[:5]
               if phone_key(p.contact_phone) == phone]
    recent = TextMessage.objects.filter(phone=phone, participant__isnull=False).order_by("-created_at").first()
    if recent and any(p.pk == recent.participant_id for p in matches):
        return recent.participant
    return matches[0] if len(matches) == 1 else None


def receive(payload):
    """Handle one incoming text from EZ Texting. Returns the TextMessage (or None)."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    phone = phone_key(str(data.get("fromNumber") or data.get("from") or ""))
    body = str(data.get("message") or "").strip()[:1600]
    if not phone:
        return None
    word = re.sub(r"[^A-Z]", "", body.upper())
    if data.get("optOut") or word in STOP_WORDS:
        TextOptOut.objects.get_or_create(phone=phone, defaults={"note": "Replied STOP"})
    elif word in START_WORDS and TextOptOut.objects.filter(phone=phone).exists():
        TextOptOut.objects.filter(phone=phone).delete()
    owner = route(phone)
    row = TextMessage.objects.create(direction=TextMessage.Direction.IN, phone=phone, participant=_participant_for(phone),
                                     body=body or "(empty message)", replies_to=owner, state=TextMessage.State.NEW,
                                     provider_reference=str(data.get("id") or "")[:160])
    _log(row, owner, direction="inbound", summary=("Text reply: " + body)[:120])
    _nudge(row)
    return row


def _nudge(row):
    """Email the owner that a reply came in (only once site email is set up)."""
    owner = row.replies_to
    if not (owner and owner.email):
        return
    from django.core.mail import send_mail
    from django.urls import reverse
    who = f"{row.participant.first_name} {row.participant.last_name}" if row.participant else f"(…{row.phone[-4:]})"
    base = getattr(settings, "PARTICIPANT_PORTAL_BASE_URL", "") or ""
    link = (base.rstrip("/") + reverse("messaging:texts")) if base else ""
    try:
        send_mail(f"Text reply from {who}", f"{who} replied:\n\n{row.body}\n\n{('Open: ' + link) if link else ''}",
                  None, [owner.email], fail_silently=True)
    except Exception:
        pass


def webhook_secret():
    row, _values = reply_settings()
    return store.secret(row) if row and row.secret_encrypted else ""


def verify_signature(raw_body, header, secret):
    if not secret:
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    candidates = {base64.b64encode(digest).decode(), digest.hex()}
    value = (header or "").strip()
    value = value.split("=", 1)[1] if value.lower().startswith("sha256=") else value
    return any(hmac.compare_digest(value, c) for c in candidates)


def turn_on_replies(*, actor, callback_url):
    """Ask EZ Texting to forward incoming texts here, signed with a shared secret."""
    row, values = reply_settings()
    secret_value = (store.secret(row) if row and row.secret_encrypted else "") or secrets.token_urlsafe(24)
    path_token = values.get("path_token") or secrets.token_urlsafe(16)
    url = callback_url(path_token)
    username, password, _source = sms_providers.ez_credentials()
    if not username:
        raise TextError("Set up texting first.")
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    from urllib import error, request
    req = request.Request(WEBHOOK_URL, method="POST", data=json.dumps(
        {"type": "inbound_text.received", "callbackUrl": url, "secret": secret_value}).encode(),
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=15):
            pass
    except error.HTTPError as exc:
        raise TextError(f"EZ Texting didn't accept the reply forwarding (error {exc.code}).") from None
    except (error.URLError, TimeoutError):
        raise TextError("EZ Texting could not be reached.") from None
    store.save(key="replies", actor=actor, new_secret=secret_value,
               values=dict(values, path_token=path_token, forwarding=True, forwarding_at=timezone.now().isoformat()))
    return url


def set_owner(*, actor, owner):
    _row, values = reply_settings()
    store.save(key="replies", actor=actor, values=dict(values, owner_id=str(owner.pk) if owner else None))
