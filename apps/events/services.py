from dataclasses import dataclass
from datetime import timedelta
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import AuditLog
from .models import Attendance, Event, EventGroup, EventRegistration, QrTicket
from .ticketing import allocate_ticket_number

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


def _authorized_override(actor, reason):
    if not reason:
        return False
    if not getattr(actor, "is_authenticated", False) or not actor.has_perm("events.change_eventregistration"):
        raise ValidationError("An authorized staff member must provide an override reason.")
    return True


def _registration_window_error(event, group, now):
    if event.registration_opens_at and now < event.registration_opens_at:
        return "Registration has not opened for this event."
    if event.registration_closes_at and now > event.registration_closes_at:
        return "Registration has closed for this event."
    deadline = group.registration_deadline_at if group and group.registration_deadline_at else event.registration_deadline_at
    if deadline and now > deadline:
        return "The registration deadline has passed."
    return None


def _capacity_error(event, group):
    if event.registration_capacity is not None and EventRegistration.objects.filter(event=event, status=EventRegistration.Status.ACTIVE).count() >= event.registration_capacity:
        return "This event is at capacity."
    if group and group.capacity is not None and EventRegistration.objects.filter(group=group, status=EventRegistration.Status.ACTIVE).count() >= group.capacity:
        return "This group is at capacity."
    return None


def register_participant(*, event, participant, actor, source=EventRegistration.Source.STAFF,
                         group=None, custom_answers=None, override_reason=""):
    if getattr(participant, "archived_at", None):
        raise ValidationError("An archived participant cannot be registered.")
    if participant.kind != "person":
        raise ValidationError("The BTW test identity cannot receive an official Event registration.")
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event.pk)
        if event.status in (Event.Status.CANCELLED, Event.Status.ARCHIVED, Event.Status.COMPLETED):
            raise ValidationError("This event is not accepting registrations.")
        existing = EventRegistration.objects.filter(event=event, participant=participant, status=EventRegistration.Status.ACTIVE).first()
        if existing:
            return existing, False
        if group is not None:
            group = EventGroup.objects.select_for_update().get(pk=group.pk)
            if group.event_id != event.pk or not group.is_active:
                raise ValidationError("Choose an active group for this event.")
        if event.group_mode == "disabled" and group is not None:
            raise ValidationError("Group selection is disabled for this event.")
        if event.group_mode == "required" and group is None and source == EventRegistration.Source.PUBLIC:
            raise ValidationError("Choose a group to continue.")
        override = _authorized_override(actor, override_reason)
        window_error = _registration_window_error(event, group, timezone.now())
        capacity_error = _capacity_error(event, group)
        if (window_error or capacity_error) and not override:
            raise ValidationError(window_error or capacity_error)
        try:
            with transaction.atomic():
                registration = EventRegistration.objects.create(
                    event=event, participant=participant, source=source, created_by=actor,
                    group=group, custom_answers=custom_answers or {},
                )
        except IntegrityError:
            registration = EventRegistration.objects.get(event=event, participant=participant, status=EventRegistration.Status.ACTIVE)
            return registration, False
        issue_ticket(registration=registration, actor=actor)
        audit_action(actor=actor, action="event.registration.created", instance=registration, source=source,
                     new_data={"event_id": str(event.pk), "participant_id": str(participant.pk), "source": source})
        if override:
            audit_action(actor=actor, action="event.registration.override", instance=registration,
                         reason=override_reason, new_data={"window": window_error or "", "capacity": capacity_error or ""})
        return registration, True


def transfer_registration_group(*, registration, destination, actor, override_reason=""):
    if not getattr(actor, "is_authenticated", False) or not actor.has_perm("events.change_eventregistration"):
        raise ValidationError("You are not authorized to change group assignments.")
    with transaction.atomic():
        registration = EventRegistration.objects.select_for_update().select_related("event").get(pk=registration.pk)
        Event.objects.select_for_update().get(pk=registration.event_id)
        if registration.status != EventRegistration.Status.ACTIVE:
            raise ValidationError("Only active registrations can change groups.")
        if destination is not None:
            destination = EventGroup.objects.select_for_update().get(pk=destination.pk)
            if destination.event_id != registration.event_id or not destination.is_active:
                raise ValidationError("Choose an active group for this event.")
        if registration.group_id == (destination.pk if destination else None):
            return registration, False
        if registration.event.group_mode == "required" and destination is None:
            raise ValidationError("This event requires a group assignment.")
        if destination and destination.capacity is not None and destination.registration_count >= destination.capacity:
            if not _authorized_override(actor, override_reason):
                raise ValidationError("This group is at capacity.")
        prior = str(registration.group_id) if registration.group_id else ""
        registration.group = destination
        registration.full_clean()
        registration.save(update_fields=("group", "updated_at"))
        audit_action(actor=actor, action="event.registration.group_changed", instance=registration,
                     old_data={"group_id": prior}, new_data={"group_id": str(destination.pk) if destination else ""},
                     reason=override_reason)
        return registration, True


def correct_registration_answers(*, registration, answers, actor, reason):
    if not getattr(actor, "is_authenticated", False) or not actor.has_perm("events.change_eventregistration"):
        raise ValidationError("You are not authorized to correct registration answers.")
    if not reason.strip():
        raise ValidationError("A correction reason is required.")
    with transaction.atomic():
        registration = EventRegistration.objects.select_for_update().get(pk=registration.pk)
        allowed = set(registration.event.registration_questions.values_list("key", flat=True))
        if not set(answers).issubset(allowed):
            raise ValidationError("Unknown Event registration question.")
        previous = dict(registration.custom_answers)
        registration.custom_answers = {**previous, **answers}
        registration.save(update_fields=("custom_answers", "updated_at"))
        audit_action(actor=actor, action="event.registration.answers_corrected", instance=registration,
                     old_data={"keys": sorted(previous)}, new_data={"keys": sorted(registration.custom_answers)}, reason=reason)
        return registration


def _ticket_expiry(registration):
    return registration.event.end_at + TICKET_GRACE


def issue_ticket(*, registration, actor=None):
    """Idempotently return the registration's current ticket, issuing it once if absent."""
    with transaction.atomic():
        registration = EventRegistration.objects.select_for_update().select_related("event").get(pk=registration.pk)
        if registration.participant.kind != "person":
            raise ValidationError("The BTW test identity cannot receive an official ticket.")
        current = QrTicket.objects.filter(registration=registration, is_current=True).first()
        if current:
            return current
        ticket = QrTicket.objects.create(registration=registration, ticket_number=allocate_ticket_number(), issued_by=actor, expires_at=_ticket_expiry(registration))
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
        replacement = QrTicket.objects.create(registration=registration, ticket_number=allocate_ticket_number(), issued_by=actor, expires_at=_ticket_expiry(registration), reissue_reason=reason[:240])
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
    raw_token = str(token).strip()
    if raw_token.lower().startswith("tel:"):
        raw_token = raw_token[4:]
    ticket_qs = QrTicket.objects.select_related("registration__event", "registration__participant")
    try:
        token_uuid = uuid.UUID(raw_token)
    except (TypeError, ValueError, AttributeError):
        ticket = ticket_qs.filter(ticket_number=raw_token, is_current=True).first()
    else:
        ticket = ticket_qs.filter(token=token_uuid).first()
    if ticket is None:
        return TicketResolution("not_found")
    registration = ticket.registration
    if registration.participant.kind != "person":
        return TicketResolution("registration_inactive", ticket)
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
        if registration.participant.kind != "person":
            raise ValidationError("The BTW test identity cannot be checked in officially.")
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
