from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
import hashlib
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Event, OfflineEventPreparation, OfflineTicketRange, SyncOutboxItem, CentralEventDownload, EventRegistration

@dataclass(frozen=True)
class Readiness:
    ready: bool
    failures: tuple[str, ...]

class TicketRangeExhausted(ValidationError):
    """No reserved primary or backup ticket remains."""


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
    if preparation.consent_document_id and (not preparation.consent_document.original_pdf or not preparation.consent_document.original_pdf.storage.exists(preparation.consent_document.original_pdf.name)):
        failures.append("approved consent PDF is not locally available")
    download = CentralEventDownload.objects.filter(event_id=event.pk, trailer_identity=preparation.trailer.identity).first()
    if download:
        from .downsync import configuration_issues
        if download.status == CentralEventDownload.Status.READY:
            failures.extend(configuration_issues(preparation, download.config))
        if download.central_event_updated_at and download.config.get("event", {}).get("updated_at"):
            from .downsync import when
            if download.central_event_updated_at > when(download.config["event"]["updated_at"]):
                failures.append("central Event configuration has newer changes awaiting download")
        if download.status != CentralEventDownload.Status.READY or not download.last_success_at:
            failures.append("central event download is incomplete")
        local_count = EventRegistration.objects.filter(event=event, status="active", source__in=("online", "public")).count()
        if download.central_registration_count is not None and local_count < download.central_registration_count:
            failures.append("central pre-registrations are not fully downloaded")
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
        raise TicketRangeExhausted("All offline ticket ranges are exhausted.")

def queue_sync(preparation, *, operation_key, operation_type, payload):
    with transaction.atomic():
        item, created=SyncOutboxItem.objects.get_or_create(operation_key=operation_key, defaults={"preparation":preparation,"operation_type":operation_type,"payload":payload})
        item = SyncOutboxItem.objects.select_for_update().get(pk=item.pk)
        if not created and (item.preparation_id != preparation.pk or item.operation_type != operation_type or item.payload != payload):
            raise ValidationError("This operation key is already associated with different event data.")
        if not created and item.status in (SyncOutboxItem.Status.FAILED, SyncOutboxItem.Status.PENDING):
            item.status=SyncOutboxItem.Status.PENDING; item.next_attempt_at=timezone.now(); item.save(update_fields=("status","next_attempt_at","updated_at"))
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
    form_version = RegistrationFormVersion.objects.filter(status=RegistrationFormVersion.Status.PUBLISHED, form__is_smart_intake=False).order_by("-version").first()
    if not form_version:
        raise ValidationError("No published registration form is available.")
    answers = data.get("answers") or {}
    from .models import EventRegistrationQuestion
    questions = EventRegistrationQuestion.objects.filter(event=preparation.event, is_active=True).order_by("position", "pk")
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
        from apps.participants.identity import find_or_create_participant
        resolution = find_or_create_participant(first_name=first_name, last_name=last_name, email=email, phone=phone, identity_uuid=data.get("participant_identity") or data.get("identity_uuid"))
        if resolution.participant is None:
            raise ValidationError("Participant identity needs staff review before registration.")
        participant = resolution.participant
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
        registration.ticket_email_opt_in = bool(data.get("ticket_email_opt_in"))
        registration.ticket_sms_opt_in = bool(data.get("ticket_sms_opt_in"))
        if registration.ticket_email_opt_in or registration.ticket_sms_opt_in:
            registration.ticket_communication_opt_in_at = timezone.now()
        registration.save(update_fields=["ticket_email_opt_in", "ticket_sms_opt_in", "ticket_communication_opt_in_at"])
        number = reserve_ticket_number(preparation)
        ticket = QrTicket.objects.get(registration=registration, is_current=True)
        ticket.ticket_number = number
        ticket.expires_at = timezone.now() + timedelta(days=30)
        ticket.issued_by = actor
        ticket.save(update_fields=["ticket_number", "expires_at", "issued_by"])
        acceptance = ConsentAcceptance.objects.get(participant=participant, submission=submission, document=document)
        payload = {
            "event_id": str(preparation.event_id),
            "preparation_id": str(preparation.pk), "participant_identity": str(participant.identity_uuid),
            "participant": {
                "id": str(participant.pk), "identity_uuid": str(participant.identity_uuid),
                "kind": participant.kind, "first_name": participant.first_name,
                "last_name": participant.last_name, "preferred_name": participant.preferred_name,
                "contact_email": participant.contact_email, "contact_phone": participant.contact_phone,
                "created_at": participant.created_at.isoformat(),
            },
            "registration": {
                "id": str(registration.pk), "registered_at": registration.registered_at.isoformat(),
                "group_id": str(group.pk) if group else "", "group_name": group.name if group else "",
                "custom_answers": registration.custom_answers,
            },
            "ticket": {
                "id": str(ticket.pk), "token": str(ticket.token), "ticket_number": ticket.ticket_number,
                "issued_at": ticket.issued_at.isoformat(), "expires_at": ticket.expires_at.isoformat(),
            },
            "ticket_number": number,
            "submission": {
                "id": str(submission.pk), "form_version_id": str(form_version.pk),
                "form_version": form_version.version, "answers": submission.answers,
                "answer_snapshot": submission.answer_snapshot,
                "submitted_at": submission.submitted_at.isoformat(),
                "age_classification": submission.age_classification,
            },
            "consent": {
                "acceptance_id": str(acceptance.pk), "document_id": str(document.pk),
                "document_key": document.key, "document_version": document.version,
                "content_hash": document.content_hash, "accepted_at": acceptance.accepted_at.isoformat(),
                "metadata": acceptance.metadata,
            },
        }
        payload.update({
            "participant_identity": str(participant.identity_uuid),
            "participant": {
                "identity_uuid": str(participant.identity_uuid),
                "first_name": participant.first_name, "last_name": participant.last_name,
                "preferred_name": participant.preferred_name,
                "contact_email": participant.contact_email, "contact_phone": participant.contact_phone,
                "city": participant.city, "state": participant.state, "postal_code": participant.postal_code,
            },
            "registration_id": str(registration.pk),
            "registered_at": registration.registered_at.isoformat(),
            "group_id": str(registration.group_id) if registration.group_id else None,
            "group_name": group.name if group else "",
            "custom_answers": registration.custom_answers or {},
            "ticket_number": number,
            "ticket": {
                "ticket_id": str(ticket.pk), "token": str(ticket.token),
                "ticket_number": number, "is_current": ticket.is_current,
                "issued_at": ticket.issued_at.isoformat(), "expires_at": ticket.expires_at.isoformat(),
                "revoked_at": ticket.revoked_at.isoformat() if ticket.revoked_at else None,
                "reissue_reason": ticket.reissue_reason,
            },
        })
        envelope = OfflineRegistrationEnvelope.objects.create(
            preparation=preparation, participant_identity=participant.identity_uuid, registration_id=registration.id,
            ticket_number=number, consent_document_version=document.version, consent_hash=document.content_hash,
            group_name=group.name if group else "", custom_answers=dict(data), payload=payload,
        )
        queue_sync(preparation=preparation, operation_key=f"registration:{envelope.id}", operation_type="registration.created", payload=envelope.payload)
        from .communications import queue_ticket_delivery
        queue_ticket_delivery(registration)
        return participant, registration, ticket, envelope
