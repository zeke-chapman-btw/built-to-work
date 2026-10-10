from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
import hashlib
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Event, OfflineEventPreparation, OfflineTicketRange, SyncOutboxItem

@dataclass(frozen=True)
class Readiness:
    ready: bool
    failures: tuple[str, ...]

def check_offline_readiness(preparation):
    failures=[]
    preparation=OfflineEventPreparation.objects.select_related("event", "consent_document").prefetch_related("ticket_ranges").get(pk=preparation.pk)
    event=preparation.event
    if not event.registration_questions.filter(is_active=True).exists() and event.group_mode == "required":
        pass
    if event.status in (Event.Status.CANCELLED, Event.Status.ARCHIVED): failures.append("event is not available")
    if not preparation.consent_document_id or not preparation.consent_document.is_approved: failures.append("approved consent document is not locally available")
    if not preparation.consent_hash or (preparation.consent_document_id and preparation.consent_hash != preparation.consent_document.content_hash): failures.append("consent integrity hash does not match")
    if not preparation.sync_identity: failures.append("synchronization identity is missing")
    ranges={item.kind:item for item in preparation.ticket_ranges.all()}
    if OfflineTicketRange.Kind.PRIMARY not in ranges: failures.append("primary ticket range is missing")
    if OfflineTicketRange.Kind.BACKUP not in ranges: failures.append("backup ticket range is missing")
    if any(item.start_number > item.end_number or item.next_number > item.end_number + 1 for item in ranges.values()): failures.append("ticket range is invalid")
    return Readiness(not failures, tuple(failures))

def activate_offline(preparation, actor):
    if not getattr(actor, "is_authenticated", False) or not actor.is_staff:
        raise ValidationError("Staff authorization is required to activate offline registration.")
    with transaction.atomic():
        preparation=OfflineEventPreparation.objects.select_for_update().get(pk=preparation.pk)
        readiness=check_offline_readiness(preparation)
        preparation.last_readiness_check_at=timezone.now()
        if not readiness.ready:
            preparation.status=OfflineEventPreparation.Status.BLOCKED
            preparation.save(update_fields=("status", "last_readiness_check_at", "updated_at"))
            raise ValidationError("Offline readiness failed: " + "; ".join(readiness.failures))
        preparation.status=OfflineEventPreparation.Status.ACTIVE
        preparation.activated_at=timezone.now()
        preparation.save(update_fields=("status", "activated_at", "last_readiness_check_at", "updated_at"))
        return preparation

def reserve_ticket_number(preparation):
    with transaction.atomic():
        preparation=OfflineEventPreparation.objects.select_for_update().get(pk=preparation.pk)
        if preparation.status != OfflineEventPreparation.Status.ACTIVE: raise ValidationError("Offline preparation is not active.")
        ranges=list(preparation.ticket_ranges.select_for_update().order_by("kind"))
        for kind in (OfflineTicketRange.Kind.PRIMARY, OfflineTicketRange.Kind.BACKUP):
            item=next((entry for entry in ranges if entry.kind == kind and not entry.is_exhausted), None)
            if item is None: continue
            if item.next_number > item.end_number:
                item.is_exhausted=True; item.save(update_fields=("is_exhausted",))
                continue
            value=item.next_number; item.next_number += 1
            if item.next_number > item.end_number: item.is_exhausted=True
            item.save(update_fields=("next_number", "is_exhausted"))
            return f"{value:010d}"
        raise ValidationError("All offline ticket ranges are exhausted.")

def queue_sync(preparation, *, operation_key, operation_type, payload):
    with transaction.atomic():
        item, created=SyncOutboxItem.objects.get_or_create(preparation=preparation, operation_key=operation_key, defaults={"operation_type":operation_type,"payload":payload})
        if not created and item.status in (SyncOutboxItem.Status.FAILED, SyncOutboxItem.Status.PENDING):
            item.payload=payload; item.status=SyncOutboxItem.Status.PENDING; item.next_attempt_at=timezone.now(); item.save(update_fields=("payload","status","next_attempt_at","updated_at"))
        return item, created

def claim_ready(limit=50):
    now=timezone.now()
    with transaction.atomic():
        items=list(SyncOutboxItem.objects.select_for_update(skip_locked=True).filter(status=SyncOutboxItem.Status.PENDING,next_attempt_at__lte=now).order_by("created_at")[:limit])
        for item in items:
            item.status=SyncOutboxItem.Status.IN_FLIGHT; item.attempts += 1; item.save(update_fields=("status","attempts","updated_at"))
        return items

def mark_sync_sent(item):
    item.status=SyncOutboxItem.Status.SENT; item.accepted_at=timezone.now(); item.save(update_fields=("status","accepted_at","updated_at")); return item

def mark_sync_failed(item, error, retry_seconds=60):
    item.status=SyncOutboxItem.Status.PENDING; item.last_error=str(error)[:2000]; item.next_attempt_at=timezone.now()+timedelta(seconds=min(max(retry_seconds,1),3600)); item.save(update_fields=("status","last_error","next_attempt_at","updated_at")); return item

def destination_hash(value):
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def create_offline_registration(*, preparation, actor, data):
    """Create a durable local registration and queue it for later sync."""
    from datetime import timedelta
    from django.utils import timezone
    from apps.participants.models import (
        Participant, ConsentAcceptance, ConsentDocumentVersion, RegistrationSubmission,
        RegistrationFormVersion,
    )
    from .models import EventRegistration, QrTicket, OfflineRegistrationEnvelope
    from .services import register_participant
    if preparation.status != "active":
        raise ValidationError("This trailer event is not active.")
    document = preparation.consent_document
    if not document or not document.is_approved:
        raise ValidationError("An approved consent document is required.")
    form_version = RegistrationFormVersion.objects.filter(status=RegistrationFormVersion.Status.PUBLISHED).order_by("-version").first()
    if not form_version:
        raise ValidationError("No published registration form is available.")
    answers = data.get("answers") or {}
    from .models import EventRegistrationQuestion
    questions = EventRegistrationQuestion.objects.filter(event=preparation.event, is_active=True).order_by("position", "created_at")
    for question in questions:
        value = answers.get(question.key, "")
        if question.is_required and (value is None or (isinstance(value, str) and not value.strip())):
            raise ValidationError(f"{question.label} is required.")
        if value not in (None, "") and question.field_type in {"single_choice", "yes_no"}:
            allowed = {str(option.get("value", option.get("label", ""))) if isinstance(option, dict) else str(option) for option in question.options}
            if question.field_type == "yes_no":
                allowed = {"yes", "no"}
            if str(value) not in allowed:
                raise ValidationError(f"Choose a valid answer for {question.label}.")
    first_name = str(data.get("first_name", "")).strip()
    last_name = str(data.get("last_name", "")).strip()
    if not first_name or not last_name:
        raise ValidationError("First and last name are required.")
    email = str(data.get("contact_email", data.get("email", ""))).strip()
    phone = str(data.get("contact_phone", data.get("phone", ""))).strip()
    with transaction.atomic():
        participant = Participant.objects.create(first_name=first_name, last_name=last_name, contact_email=email, contact_phone=phone)
        submission = RegistrationSubmission.objects.create(
            form_version=form_version, participant=participant, event=preparation.event,
            status=RegistrationSubmission.Status.SUBMITTED, answers=dict(data),
            answer_snapshot=form_version.snapshot, source="offline", submitted_at=timezone.now(),
        )
        ConsentAcceptance.objects.create(participant=participant, submission=submission, document=document, metadata={"source": "offline", "trailer": preparation.trailer.identity})
        group = None
        group_id = data.get("group")
        if group_id:
            group = preparation.event.groups.filter(pk=group_id, is_active=True).first()
        registration, created = register_participant(event=preparation.event, participant=participant, actor=actor, source=EventRegistration.Source.STAFF, group=group, custom_answers=dict(data))
        if not created:
            raise ValidationError("This participant is already registered.")
        number = reserve_ticket_number(preparation)
        ticket = QrTicket.objects.create(registration=registration, ticket_number=number, expires_at=timezone.now() + timedelta(days=30), issued_by=actor)
        envelope = OfflineRegistrationEnvelope.objects.create(
            preparation=preparation, participant_identity=participant.identity_uuid, registration_id=registration.id,
            ticket_number=number, consent_document_version=document.version, consent_hash=document.content_hash,
            group_name=group.name if group else "", custom_answers=dict(data), payload={"event_id": str(preparation.event_id), "ticket_number": number},
        )
        queue_sync(preparation=preparation, operation_key=f"registration:{envelope.id}", operation_type="registration.created", payload=envelope.payload)
        return participant, registration, ticket, envelope
