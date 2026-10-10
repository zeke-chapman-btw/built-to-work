import math
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import OfflineRegistrationEnvelope, SyncOutboxItem


def build_sync_payload(item):
    payload = dict(item.payload or {})
    return {
        "operation_key": item.operation_key,
        "operation_type": item.operation_type,
        "trailer_identity": item.preparation.trailer.identity,
        "event_id": str(item.preparation.event_id),
        "preparation_id": str(item.preparation_id),
        "payload": payload,
    }


@transaction.atomic
def claim_sync_item(item_id):
    item = SyncOutboxItem.objects.select_for_update().select_related("preparation__trailer").get(pk=item_id)
    if item.status not in (SyncOutboxItem.Status.PENDING, SyncOutboxItem.Status.FAILED):
        return None
    if item.next_attempt_at and item.next_attempt_at > timezone.now():
        return None
    item.status = SyncOutboxItem.Status.IN_FLIGHT
    item.attempts += 1
    item.save(update_fields=["status", "attempts", "updated_at"])
    return item


@transaction.atomic
def record_sync_failure(item_id, error, *, max_delay_seconds=3600):
    item = SyncOutboxItem.objects.select_for_update().get(pk=item_id)
    delay = min(max_delay_seconds, 2 ** min(item.attempts, 10))
    item.status = SyncOutboxItem.Status.FAILED
    item.last_error = str(error)[:2000]
    item.next_attempt_at = timezone.now() + timedelta(seconds=delay)
    item.save(update_fields=["status", "last_error", "next_attempt_at", "updated_at"])
    return item


@transaction.atomic
def acknowledge_sync(item_id, *, status=OfflineRegistrationEnvelope.Status.SYNCED, error=""):
    item = SyncOutboxItem.objects.select_for_update().get(pk=item_id)
    item.status = SyncOutboxItem.Status.SENT
    item.accepted_at = timezone.now()
    item.last_error = error
    item.save(update_fields=["status", "accepted_at", "last_error", "updated_at"])
    envelopes = OfflineRegistrationEnvelope.objects.filter(preparation=item.preparation, payload__operation_key=item.operation_key)
    envelopes.update(status=status, synced_at=timezone.now())
    return item


import secrets
from django.contrib.auth.hashers import check_password, make_password
from .models import TrailerInstance


def provision_trailer_credential(trailer):
    raw = secrets.token_urlsafe(32)
    trailer.credential_hash = make_password(raw)
    trailer.credential_revoked_at = None
    trailer.credential_rotated_at = timezone.now()
    trailer.save(update_fields=["credential_hash", "credential_revoked_at", "credential_rotated_at", "updated_at"] if hasattr(trailer, "updated_at") else ["credential_hash", "credential_revoked_at", "credential_rotated_at"])
    return raw


def rotate_trailer_credential(trailer):
    return provision_trailer_credential(trailer)


def revoke_trailer_credential(trailer):
    trailer.credential_revoked_at = timezone.now()
    trailer.save(update_fields=["credential_revoked_at"])


def authenticate_trailer(identity, raw_credential):
    trailer = TrailerInstance.objects.filter(identity=identity, is_active=True, credential_revoked_at__isnull=True).first()
    if not trailer or not getattr(trailer, "credential_hash", "") or not check_password(raw_credential, trailer.credential_hash):
        return None
    return trailer


def import_registration_if_safe(*, event, payload, participant=None, preparation=None):
    """Import one trailer registration without overwriting central identity data."""
    import uuid
    import re
    from django.db.models import Q
    import re
    from django.db.models import Q
    from datetime import datetime
    from django.db import transaction
    from django.utils import timezone
    from apps.events.models import (
        EventRegistration, QrTicket, OfflineRegistrationEnvelope,
        ParticipantMatchReview,
    )
    from apps.participants.models import (
        Participant, RegistrationSubmission, RegistrationFormVersion,
        ConsentDocumentVersion, ConsentAcceptance,
    )

    try:
        participant_data = payload.get("participant") or {}
        identity = uuid.UUID(str(payload.get("participant_identity") or participant_data.get("identity_uuid")))
        registration_id = uuid.UUID(str(payload.get("registration_id")))
        submission_data = payload.get("submission") or {}
        submission_id = int(submission_data.get("submission_id") or submission_data.get("id"))
        form_version_id = int(submission_data.get("form_version_id"))
        consent_data = payload.get("consent") or {}
        consent_id = int(consent_data.get("acceptance_id"))
        document_id = int(consent_data.get("document_id"))
        ticket_data = payload.get("ticket") or {}
        ticket_id = uuid.UUID(str(ticket_data.get("ticket_id") or ticket_data.get("id")))
        ticket_token = uuid.UUID(str(ticket_data.get("token")))
    except (TypeError, ValueError, AttributeError) as exc:
        return None, f"registration envelope has invalid stable identifier: {exc}"

    number = str(ticket_data.get("ticket_number") or payload.get("ticket_number") or "")
    if not re.fullmatch(r"\d{10}", number):
        return None, "ticket number must contain exactly 10 digits"
    if str(payload.get("event_id")) != str(event.pk):
        return None, "event reference does not match the authorized event"

    form_version = RegistrationFormVersion.objects.filter(pk=form_version_id).first()
    document = ConsentDocumentVersion.objects.filter(
        pk=document_id, key=consent_data.get("document_key"),
        version=consent_data.get("document_version"),
        content_hash=consent_data.get("content_hash"), is_approved=True,
    ).first()
    if not form_version:
        return None, "referenced registration form version is not installed centrally"
    if not document:
        return None, "referenced approved consent version or content hash is not installed centrally"

    group = None
    group_id = payload.get("group_id")
    group_name = str(payload.get("group_name") or "").strip()
    if group_id:
        group = event.groups.filter(pk=group_id).first()
        if group is None:
            return None, "group is not configured for this event"
    elif group_name:
        group = event.groups.filter(name=group_name).first()
        if group is None:
            return None, "group is not configured for this event"

    try:
        issued_at = _sync_datetime(ticket_data.get("issued_at"))
        expires_at = _sync_datetime(ticket_data.get("expires_at"))
        accepted_at = _sync_datetime(consent_data.get("accepted_at"))
        submitted_at = _sync_datetime(submission_data.get("submitted_at"))
    except (TypeError, ValueError):
        return None, "registration envelope contains an invalid timestamp"
    if expires_at is None or accepted_at is None or submitted_at is None:
        return None, "ticket expiry, consent time, and submission time are required"

    with transaction.atomic():
        existing_participant = Participant.objects.filter(identity_uuid=identity).first()
        if participant and participant.identity_uuid != identity:
            return None, "participant identity does not match the envelope"
        if existing_participant and existing_participant.archived_at:
            return None, "participant identity is archived centrally"
        participant = existing_participant or participant
        if participant is None:
            email = str(participant_data.get("contact_email") or "").strip()
            phone = str(participant_data.get("contact_phone") or "").strip()
            possible = Participant.objects.filter(archived_at__isnull=True).filter(
                Q(contact_email__iexact=email) if email else Q(pk__isnull=True)
            )
            if phone:
                possible = possible | Participant.objects.filter(archived_at__isnull=True, contact_phone=phone)
            candidate = possible.exclude(identity_uuid=identity).first()
            if candidate:
                if preparation:
                    envelope, _ = OfflineRegistrationEnvelope.objects.get_or_create(
                        registration_id=registration_id,
                        defaults={
                            "preparation": preparation, "participant_identity": identity,
                            "ticket_number": number,
                            "consent_document_version": document.version,
                            "consent_hash": document.content_hash,
                            "group_name": group.name if group else "",
                            "custom_answers": payload.get("custom_answers") or {},
                            "payload": payload,
                        },
                    )
                    ParticipantMatchReview.objects.get_or_create(
                        envelope=envelope, status=ParticipantMatchReview.Status.PENDING,
                        defaults={"candidate_participant": candidate,
                                  "evidence": {"email_match": bool(email and candidate.contact_email.lower() == email.lower()),
                                               "phone_match": bool(phone and candidate.contact_phone == phone)}},
                    )
                return None, "possible participant match requires authorized review; no records were merged"

        existing = EventRegistration.objects.filter(pk=registration_id).first()
        if existing and (existing.event_id != event.pk or existing.participant_id != getattr(participant, "pk", None)):
            return None, "registration identity belongs to another event or participant"
        if existing and QrTicket.objects.filter(registration=existing, is_current=True, ticket_number=number).exists():
            return existing, ""

        if participant is None:
            participant = Participant.objects.create(
                identity_uuid=identity,
                first_name=str(participant_data.get("first_name") or "").strip(),
                last_name=str(participant_data.get("last_name") or "").strip(),
                preferred_name=str(participant_data.get("preferred_name") or "").strip(),
                contact_email=str(participant_data.get("contact_email") or "").strip(),
                contact_phone=str(participant_data.get("contact_phone") or "").strip(),
                city=str(participant_data.get("city") or "").strip(),
                state=str(participant_data.get("state") or "").strip(),
                postal_code=str(participant_data.get("postal_code") or "").strip(),
            )
        if not participant.first_name or not participant.last_name:
            return None, "participant name is required for a new central participant"

        if existing is None:
            registration = EventRegistration.objects.create(
                id=registration_id, event=event, participant=participant, group=group,
                custom_answers=payload.get("custom_answers") or {}, source="offline_sync",
                registered_at=_sync_datetime(payload.get("registered_at")) or submitted_at,
            )
        else:
            registration = existing

        submission, _ = RegistrationSubmission.objects.get_or_create(
            pk=submission_id,
            defaults={
                "form_version": form_version, "participant": participant, "event": event,
                "status": RegistrationSubmission.Status.SUBMITTED,
                "answers": submission_data.get("answers") or {},
                "answer_snapshot": submission_data.get("answer_snapshot") or form_version.snapshot,
                "age_classification": submission_data.get("age_classification") or "",
                "submitted_at": submitted_at, "source": "offline",
                "idempotency_key": submission_data.get("idempotency_key") or f"offline:{registration_id}",
            },
        )
        if submission.participant_id != participant.pk or submission.event_id != event.pk:
            return None, "submission identity belongs to another participant or event"

        acceptance, _ = ConsentAcceptance.objects.get_or_create(
            pk=consent_id,
            defaults={"participant": participant, "submission": submission,
                      "document": document, "accepted_at": accepted_at,
                      "metadata": consent_data.get("metadata") or {"source": "offline_sync"}},
        )
        if acceptance.participant_id != participant.pk or acceptance.document_id != document.pk:
            return None, "consent acceptance identity conflicts with existing evidence"

        if QrTicket.objects.filter(ticket_number=number).exclude(registration=registration).exists():
            transaction.set_rollback(True)
            return None, "ticket number is already allocated to another central registration"
        if QrTicket.objects.filter(pk=ticket_id).exclude(registration=registration).exists():
            transaction.set_rollback(True)
            return None, "ticket identity conflicts with another central registration"

        ticket = QrTicket.objects.filter(registration=registration).first()
        ticket_values = {
            "ticket_number": number,
            "token": ticket_token,
            "is_current": bool(ticket_data.get("is_current", True)),
            "issued_at": issued_at or submitted_at,
            "expires_at": expires_at,
            "revoked_at": _sync_datetime(ticket_data.get("revoked_at")),
            "reissue_reason": str(ticket_data.get("reissue_reason") or ""),
        }
        if ticket is None:
            ticket = QrTicket.objects.create(pk=ticket_id, registration=registration, **ticket_values)
        else:
            QrTicket.objects.filter(pk=ticket.pk).update(id=ticket_id, **ticket_values)
            ticket = QrTicket.objects.get(pk=ticket_id)
        return registration, ""


def _sync_datetime(value):
    if not value:
        return None
    from datetime import datetime
    from django.utils import timezone
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
