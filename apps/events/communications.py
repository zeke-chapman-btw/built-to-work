"""Durable ticket delivery and operational alert queues.

Provider calls occur outside database transactions. A claimed row is marked uncertain
before sending, so a worker crash cannot silently cause an automatic duplicate.
"""
from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import re
import socket
from urllib import error, request
from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from apps.participants.models import ConsentAcceptance
from .models import (
    EventRegistration, OperationalIncident, OperationalNotificationDelivery,
    OperationalNotificationRule, QrTicket, TicketDelivery,
)

MAX_ATTEMPTS = 6


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ticket_destination(registration, channel):
    participant = registration.participant
    if channel == TicketDelivery.Channel.EMAIL:
        return (participant.contact_email or "").strip().lower()
    return re.sub(r"[^0-9+]", "", participant.contact_phone or "")


def queue_ticket_delivery(registration):
    """Queue only explicitly opted-in channels backed by accepted consent."""
    registration = EventRegistration.objects.select_related("participant").get(pk=registration.pk)
    if registration.status != EventRegistration.Status.ACTIVE or not registration.ticket_communication_opt_in_at:
        return []
    if not ConsentAcceptance.objects.filter(
        participant_id=registration.participant_id,
        submission__event_id=registration.event_id,
        submission__status="submitted",
        document__is_approved=True,
    ).exists():
        return []
    ticket = QrTicket.objects.filter(registration=registration, is_current=True, ticket_number__isnull=False).first()
    if not ticket:
        return []
    queued = []
    for channel, opted in ((TicketDelivery.Channel.EMAIL, registration.ticket_email_opt_in), (TicketDelivery.Channel.SMS, registration.ticket_sms_opt_in)):
        if not opted:
            continue
        destination = _ticket_destination(registration, channel)
        if not destination:
            continue
        digest = _hash(destination)
        delivery, _ = TicketDelivery.objects.get_or_create(
            registration=registration, channel=channel, destination_hash=digest,
            defaults={"idempotency_key": f"ticket-{ticket.pk}-{channel}"},
        )
        queued.append(delivery)
    return queued


def emit_incident(*, kind, fingerprint, summary, event=None):
    """Create one open incident and one delivery per configured recipient."""
    if kind not in OperationalNotificationRule.Kind.values:
        raise ValueError("Unknown operational incident kind")
    key = _hash(f"{event.pk if event else 'global'}:{kind}:{fingerprint}")
    with transaction.atomic():
        try:
            incident, created = OperationalIncident.objects.get_or_create(
                fingerprint=key, status=OperationalIncident.Status.OPEN,
                defaults={"event": event, "kind": kind, "summary": summary[:240]},
            )
        except IntegrityError:
            incident = OperationalIncident.objects.get(fingerprint=key, status=OperationalIncident.Status.OPEN)
            created = False
        if not created:
            incident.occurrences += 1
            incident.save(update_fields=["occurrences", "last_seen_at"])
            return incident
        rule = OperationalNotificationRule.objects.filter(event=event, kind=kind).first() if event else None
        if rule is None:
            rule = OperationalNotificationRule.objects.filter(event__isnull=True, kind=kind).first()
        if not rule or not rule.enabled:
            return incident
        for channel in rule.channels:
            for recipient in rule.recipients:
                recipient = recipient.strip()
                if channel == "email" and "@" not in recipient:
                    continue
                if channel == "sms" and not re.fullmatch(r"\+?[0-9]{10,15}", recipient):
                    continue
                OperationalNotificationDelivery.objects.get_or_create(
                    incident=incident, channel=channel, destination=recipient,
                    defaults={"idempotency_key": f"incident-{incident.pk}-{channel}-{_hash(recipient)[:12]}"},
                )
        return incident


def _provider_send(channel, destination, *, subject, body, idempotency_key, attachment=None):
    """Provider settings are deployment-owned; no credentials enter model fields."""
    if channel == "email":
        sender = getattr(settings, "BTW_TICKET_EMAIL_FROM", "")
        if not sender:
            raise ConnectionError("Email delivery is not configured")
        message = EmailMessage(subject, body, sender, [destination], headers={"Message-ID": f"<{idempotency_key}@btw.local>"})
        if attachment is not None:
            message.attach("built-to-work-ticket.png", attachment, "image/png")
        if message.send(fail_silently=False) != 1:
            raise ConnectionError("Email provider did not accept the message")
        return idempotency_key
    endpoint = getattr(settings, "BTW_SMS_PROVIDER_URL", "")
    token = getattr(settings, "BTW_SMS_PROVIDER_TOKEN", "")
    if not endpoint or not token:
        raise ConnectionError("SMS delivery is not configured")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("SMS provider endpoint must use HTTPS without embedded credentials")
    payload = json.dumps({"to": destination, "message": body, "idempotency_key": idempotency_key}).encode("utf-8")
    req = request.Request(endpoint, data=payload, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }, method="POST")
    try:
        with request.urlopen(req, timeout=5) as response:
            if response.status not in (200, 201, 202):
                raise RuntimeError("SMS provider did not accept the message")
            return response.headers.get("X-Message-ID", idempotency_key)[:160]
    except error.HTTPError as exc:
        if exc.code == 429:
            raise ConnectionError("SMS provider rate limited the request") from None
        if exc.code >= 500:
            raise TimeoutError("SMS provider acceptance is uncertain") from None
        raise ValueError("SMS provider rejected the message") from None
    except error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise TimeoutError("SMS response timed out") from None
        raise ConnectionError("SMS provider unavailable") from None


def _ticket_message(delivery):
    registration = delivery.registration
    ticket = QrTicket.objects.filter(registration=registration, is_current=True, ticket_number__regex=r"^[0-9]{10}$").first()
    if not ticket:
        raise ValueError("Current ticket is unavailable")
    if registration.status != EventRegistration.Status.ACTIVE:
        raise ValueError("Registration is inactive")
    if not registration.ticket_communication_opt_in_at:
        raise ValueError("Communication opt-in is absent")
    if delivery.channel == "email" and not registration.ticket_email_opt_in:
        raise ValueError("Email opt-in was withdrawn")
    if delivery.channel == "sms" and not registration.ticket_sms_opt_in:
        raise ValueError("SMS opt-in was withdrawn")
    if not ConsentAcceptance.objects.filter(
        participant_id=registration.participant_id,
        submission__event_id=registration.event_id,
        submission__status="submitted",
        document__is_approved=True,
    ).exists():
        raise ValueError("Accepted consent is absent")
    destination = _ticket_destination(registration, delivery.channel)
    if not destination or _hash(destination) != delivery.destination_hash:
        raise ValueError("Contact details changed; staff review required")
    # The ten digits remain the QR payload; messaging never issues another ticket.
    return destination, f"Built to Work ticket for {registration.event.name}", f"Your Built to Work ticket number is {ticket.ticket_number}. Present your ticket at the event.", ticket


def _claim(model, pk):
    with transaction.atomic():
        row = model.objects.select_for_update().get(pk=pk)
        if row.status not in (row.Status.QUEUED, row.Status.FAILED):
            return None
        if row.status == row.Status.FAILED and row.next_attempt_at is None:
            return None
        if row.next_attempt_at and row.next_attempt_at > timezone.now():
            return None
        if row.attempts >= MAX_ATTEMPTS:
            return None
        row.status = row.Status.UNCERTAIN
        if not row.idempotency_key:
            row.idempotency_key = f"delivery-{row.pk}"
        row.attempts += 1
        if isinstance(row, TicketDelivery):
            row.last_attempt_at = timezone.now()
            row.save(update_fields=["status", "attempts", "last_attempt_at", "idempotency_key"])
        else:
            row.save(update_fields=["status", "attempts", "idempotency_key"])
        return row


def _finish(row, *, reference=None, failure=None, retry=False, uncertain=False):
    with transaction.atomic():
        current = type(row).objects.select_for_update().get(pk=row.pk)
        if current.status != current.Status.UNCERTAIN:
            return current
        if reference:
            current.status = current.Status.SENT
            current.provider_reference = reference[:160]
            current.sent_at = timezone.now()
            current.last_error = ""
            fields = ["status", "provider_reference", "sent_at", "last_error"]
        elif failure:
            current.status = current.Status.UNCERTAIN if uncertain else current.Status.FAILED
            current.last_error = str(failure)[:240]
            current.next_attempt_at = timezone.now() + timedelta(seconds=min(3600, 2 ** min(current.attempts, 10))) if retry and current.attempts < MAX_ATTEMPTS else None
            fields = ["status", "last_error", "next_attempt_at"]
        else:
            return current
        current.save(update_fields=fields)
        return current


def deliver_ticket(pk):
    row = _claim(TicketDelivery, pk)
    if row is None:
        return None
    try:
        destination, subject, body, ticket = _ticket_message(row)
        attachment = None
        if row.channel == TicketDelivery.Channel.EMAIL:
            from .ticket_renderer import ticket_png_bytes
            attachment = ticket_png_bytes(ticket)
        reference = _provider_send(row.channel, destination, subject=subject, body=body,
                                   idempotency_key=row.idempotency_key, attachment=attachment)
    except TimeoutError:
        return _finish(row, failure="Provider response timed out", uncertain=True)
    except (ConnectionError, OSError) as exc:
        return _finish(row, failure=exc, retry=True)
    except ValueError as exc:
        return _finish(row, failure=exc, retry=False)
    except Exception as exc:
        return _finish(row, failure=type(exc).__name__, uncertain=True)
    return _finish(row, reference=reference)


def deliver_alert(pk):
    row = _claim(OperationalNotificationDelivery, pk)
    if row is None:
        return None
    try:
        reference = _provider_send(row.channel, row.destination,
            subject=f"Built to Work alert: {row.incident.get_kind_display()}",
            body=row.incident.summary, idempotency_key=row.idempotency_key)
    except TimeoutError:
        return _finish(row, failure="Provider response timed out", uncertain=True)
    except (ConnectionError, OSError) as exc:
        return _finish(row, failure=exc, retry=True)
    except ValueError as exc:
        return _finish(row, failure=exc, retry=False)
    except Exception as exc:
        return _finish(row, failure=type(exc).__name__, uncertain=True)
    return _finish(row, reference=reference)


def due_deliveries(model):
    now = timezone.now()
    return model.objects.filter(attempts__lt=MAX_ATTEMPTS).filter(
        models.Q(status=model.Status.QUEUED) |
        models.Q(status=model.Status.FAILED, next_attempt_at__lte=now)
    ).order_by("created_at").values_list("pk", flat=True)
