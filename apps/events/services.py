from dataclasses import dataclass
from datetime import timedelta
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import AuditLog
from .models import Attendance, Event, EventRegistration, QrTicket

TICKET_GRACE = timedelta(hours=24)


def audit_action(*, actor, action, instance, source="staff", old_data=None, new_data=None, reason=""):
    """Record operational metadata only; never include QR tokens or participant contact data."""
    AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        content_type=ContentType.objects.get_for_model(instance),
        object_id=str(instance.pk),
        source=source,
        old_data=old_data or {},
        new_data=new_data or {},
        reason=reason,
    )


def register_participant(*, event, participant, actor, source=EventRegistration.Source.STAFF):
    if event.status in (Event.Status.CANCELLED, Event.Status.ARCHIVED, Event.Status.COMPLETED):
        raise ValidationError("This event is not accepting registrations.")
    if getattr(participant, "archived_at", None):
        raise ValidationError("An archived participant cannot be registered.")
    existing = EventRegistration.objects.filter(event=event, participant=participant, status=EventRegistration.Status.ACTIVE).first()
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            registration = EventRegistration.objects.create(
                event=event, participant=participant, source=source, created_by=actor
            )
    except IntegrityError:
        registration = EventRegistration.objects.get(event=event, participant=participant, status=EventRegistration.Status.ACTIVE)
        return registration, False
    issue_ticket(registration=registration, actor=actor)
    audit_action(actor=actor, action="event.registration.created", instance=registration, source=source, new_data={"event_id": str(event.pk), "participant_id": str(participant.pk), "source": source})
    return registration, True


def _ticket_expiry(registration):
    return registration.event.end_at + TICKET_GRACE


def issue_ticket(*, registration, actor=None):
    """Idempotently return the registration's current ticket, issuing it once if absent."""
    with transaction.atomic():
        registration = EventRegistration.objects.select_for_update().select_related("event").get(pk=registration.pk)
        current = QrTicket.objects.filter(registration=registration, is_current=True).first()
        if current:
            return current
        ticket = QrTicket.objects.create(registration=registration, issued_by=actor, expires_at=_ticket_expiry(registration))
        audit_action(actor=actor, action="event.ticket.issued", instance=ticket, new_data={"registration_id": str(registration.pk), "expires_at": ticket.expires_at.isoformat()})
        return ticket


def reissue_ticket(*, registration, actor, reason="", expected_ticket_id=None):
    """Replace the current ticket. A submitted old ticket id makes double-posts idempotent."""
    with transaction.atomic():
        registration = EventRegistration.objects.select_for_update().select_related("event").get(pk=registration.pk)
        current = QrTicket.objects.filter(registration=registration, is_current=True).first()
        if expected_ticket_id and current and str(current.pk) != str(expected_ticket_id):
            return current, False
        if current is None:
            return issue_ticket(registration=registration, actor=actor), True
        now = timezone.now()
        current.is_current = False
        current.revoked_at = now
        current.reissue_reason = reason[:240]
        current.save(update_fields=("is_current", "revoked_at", "reissue_reason"))
        replacement = QrTicket.objects.create(registration=registration, issued_by=actor, expires_at=_ticket_expiry(registration), reissue_reason=reason[:240])
        current.superseded_by = replacement
        current.save(update_fields=("superseded_by",))
        audit_action(actor=actor, action="event.ticket.reissued", instance=replacement, new_data={"registration_id": str(registration.pk), "replaces_ticket_id": str(current.pk), "expires_at": replacement.expires_at.isoformat()}, reason=reason)
        return replacement, True


@dataclass(frozen=True)
class TicketResolution:
    status: str
    ticket: QrTicket | None = None
    attendance: Attendance | None = None


def resolve_ticket(token, *, expected_event=None):
    try:
        token = uuid.UUID(str(token))
    except (TypeError, ValueError, AttributeError):
        return TicketResolution("not_found")
    ticket = QrTicket.objects.select_related("registration__event", "registration__participant").filter(token=token).first()
    if ticket is None:
        return TicketResolution("not_found")
    registration = ticket.registration
    if not ticket.is_current or ticket.revoked_at is not None:
        return TicketResolution("revoked", ticket)
    if registration.status != EventRegistration.Status.ACTIVE:
        return TicketResolution("registration_inactive", ticket)
    if timezone.now() > ticket.expires_at:
        return TicketResolution("expired", ticket)
    if expected_event is not None and str(ticket.registration.event_id) != str(expected_event.pk):
        return TicketResolution("wrong_event", ticket)
    attendance = Attendance.objects.filter(registration=registration, is_void=False).first()
    if attendance:
        return TicketResolution("already_checked_in", ticket, attendance)
    return TicketResolution("valid", ticket)


def check_in_registration(*, registration, actor=None, source=Attendance.Source.STAFF):
    """Create at most one active attendance row; repeated requests return the original."""
    with transaction.atomic():
        registration = EventRegistration.objects.select_for_update().select_related("event", "participant").get(pk=registration.pk)
        event = registration.event
        if registration.status != EventRegistration.Status.ACTIVE:
            raise ValidationError("This registration is no longer active.")
        if event.status != Event.Status.UPCOMING or timezone.now() > event.end_at:
            raise ValidationError("Check-in is closed for this event.")
        existing = Attendance.objects.filter(registration=registration, is_void=False).first()
        if existing:
            return existing, False
        try:
            with transaction.atomic():
                attendance = Attendance.objects.create(
                    event=event, registration=registration, participant=registration.participant,
                    source=source, checked_in_by=actor,
                )
        except IntegrityError:
            return Attendance.objects.get(registration=registration, is_void=False), False
        audit_action(actor=actor, action="event.attendance.checked_in", instance=attendance, source=source, new_data={"event_id": str(event.pk), "registration_id": str(registration.pk), "participant_id": str(registration.participant_id), "source": source, "checked_in_at": attendance.checked_in_at.isoformat()})
        return attendance, True


def void_registration(*, registration, actor, reason):
    with transaction.atomic():
        registration = EventRegistration.objects.select_for_update().get(pk=registration.pk)
        if Attendance.objects.filter(registration=registration, is_void=False).exists():
            raise ValidationError("Void attendance before voiding this registration.")
        if registration.status == EventRegistration.Status.VOID:
            return False
        registration.status = EventRegistration.Status.VOID
        registration.save(update_fields=("status", "updated_at"))
        QrTicket.objects.filter(registration=registration, is_current=True).update(is_current=False, revoked_at=timezone.now(), reissue_reason=reason[:240])
        audit_action(actor=actor, action="event.registration.voided", instance=registration, old_data={"status": EventRegistration.Status.ACTIVE}, new_data={"status": EventRegistration.Status.VOID}, reason=reason)
        return True
