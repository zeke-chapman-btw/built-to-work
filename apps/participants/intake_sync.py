"""Portable smart-intake import through the existing authenticated trailer outbox."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import uuid
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from apps.core.audit import record_audit
from apps.events.models import (EventRegistration, QrTicket, ParticipantMatchReview,
                               ParticipantIdentityLink)
from .models import (Participant, RegistrationFormVersion, RegistrationSubmission, ConsentDocumentVersion,
                     ConsentAcceptance)
from .intake_contracts import validate_answers, canonical_answers, canonical_json, contract_hash, age_on
from .intake_identity import resolve_intake_identity
from .intake_lifecycle import verify_published
from .intake_projection import project_intake
from .intake_trailer import TRANSPORT_SCHEMA
from .identity import canonical_id, fresh_identity_context
from .workspace_services import initial_acquisition


def _when(value):
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or timezone.is_naive(parsed):
        raise ValidationError("Portable intake requires an explicit timezone-aware timestamp.")
    return parsed


def _source_person(identity, answers, occurred_on):
    person = Participant(identity_uuid=identity, first_name=answers["first_name"],
        last_name=answers["last_name"], preferred_name=answers.get("preferred_name") or "",
        contact_email=answers["email"], contact_phone=answers["phone"],
        date_of_birth=date.fromisoformat(answers["dob"]),
        age_classification="minor" if age_on(date.fromisoformat(answers["dob"]), occurred_on) < 18 else "adult")
    person.full_clean()
    person.save()
    return person


@transaction.atomic
def import_intake(*, event, preparation, payload):
    if not preparation or preparation.event_id != event.pk:
        raise ValidationError("A scoped trailer preparation is required.")
    if not isinstance(payload, dict) or payload.get("schema") != TRANSPORT_SCHEMA:
        raise ValidationError("Unsupported smart-intake synchronization envelope.")
    received = deepcopy(payload)
    expected_hash = received.pop("envelope_hash", "")
    if expected_hash != contract_hash(received) or payload.get("event_id") != str(event.pk):
        raise ValidationError("Smart-intake envelope integrity or event scope failed.")
    try:
        operation = uuid.UUID(payload["operation_id"])
        identity = uuid.UUID(payload["source_identity"])
        form_ref = payload["form"]
        stable_id = uuid.UUID(form_ref["stable_id"])
        submitted_at = _when(payload["submitted_at"])
        consent_data = payload["consent"]
        accepted_at = _when(consent_data["accepted_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("Portable intake identifiers are invalid.") from exc
    version = RegistrationFormVersion.objects.select_related("form").filter(stable_id=stable_id).first()
    if (not version or version.form.slug != form_ref.get("slug") or version.version != form_ref.get("version")
            or version.content_hash != form_ref.get("hash")):
        raise ValidationError("The exact approved portable form version is not installed centrally.")
    verify_published(version, allow_retired=True)
    if submitted_at > timezone.now() + timedelta(minutes=5) or accepted_at > submitted_at:
        raise ValidationError("Portable intake timestamps are inconsistent.")
    occurred_on = timezone.localtime(submitted_at, ZoneInfo(event.timezone_name)).date()
    result = validate_answers(version.snapshot, payload.get("answers"), occurred_on=occurred_on)
    answers = result["answers"]
    raw_hash = hashlib.sha256(canonical_json(answers).encode()).hexdigest()
    document = ConsentDocumentVersion.objects.filter(key=consent_data.get("key"),
        version=consent_data.get("version"), content_hash=consent_data.get("hash"), is_approved=True).first()
    if (document is None or hashlib.sha256(document.body.encode()).hexdigest() != consent_data.get("text_hash")
            or not document.body.strip() or (document.effective_at and document.effective_at > accepted_at)):
        raise ValidationError("The exact approved consent evidence is unavailable or inconsistent.")
    from .models import IntakeContactLock
    lock_key = hashlib.sha256(("intake-operation:" + str(operation)).encode()).hexdigest()
    IntakeContactLock.objects.get_or_create(key=lock_key)
    IntakeContactLock.objects.select_for_update(of=("self",)).get(key=lock_key)
    prior = RegistrationSubmission.objects.select_for_update(of=("self",)).filter(intake_operation_id=operation).first()
    if prior:
        if (prior.event_id != event.pk or prior.form_version_id != version.pk or prior.answers != answers
                or prior.participant.identity_uuid != identity):
            raise ValidationError("An intake operation UUID conflicts with retained evidence.")
        review = ParticipantMatchReview.objects.filter(source_submission=prior).first()
        # A later trailer review message may carry registration evidence. It never
        # authorizes a new central identity decision by itself.
        if payload.get("registration"):
            if review is None:
                review = ParticipantMatchReview.objects.create(source_submission=prior,
                    source_participant=prior.participant, candidate_participant=prior.participant,
                    status="resolved", reviewed_at=timezone.now(), candidates=[prior.participant_id],
                    created_at=timezone.now(), updated_at=timezone.now(),
                    evidence={"automatic_retained_identity": True})
            evidence = dict(review.evidence, offline_registration=payload["registration"],
                            offline_preparation=str(preparation.pk))
            if review.status not in {"open", "later"}:
                # Retain the central identity decision. A later authenticated device
                # message may supply a ticket, but may never change that decision.
                target = Participant.objects.get(pk=canonical_id(prior.participant_id))
                try:
                    with transaction.atomic(), fresh_identity_context():
                        registration = import_registration(preparation=preparation,
                            payload=payload["registration"], source=prior.participant, target=target)
                    evidence.update(registration_status="registered", registration_id=str(registration.pk))
                    evidence.pop("registration_error", None)
                except (ValidationError, IntegrityError):
                    evidence.update(registration_status="held",
                        registration_error="Registration evidence requires staff review.")
            review.evidence = evidence
            review.save(update_fields=["evidence"])
        return prior, bool(review and review.status in {"open", "later"})
    known_source = Participant.objects.select_for_update(of=("self",)).filter(identity_uuid=identity).first()
    mapped = canonical_answers(version.snapshot, answers)
    resolution = resolve_intake_identity(answers=mapped, occurred_on=occurred_on,
                                        provisional_hint=known_source, new_identity=identity)
    target = resolution.participant
    source = known_source or target
    auto_alias = known_source is None and target.identity_uuid != identity and not resolution.review_required
    if auto_alias:
        # This is a newly imported source UUID, never an established record merge.
        source = _source_person(identity, mapped, occurred_on)
    metadata = deepcopy(payload.get("metadata") or {})
    if not isinstance(metadata, dict):
        raise ValidationError("Portable intake provenance must be an object.")
    metadata.update(payload_hash=raw_hash, answer_states=result["states"],
        provisional_source=resolution.review_required and known_source is None,
        created_participant=resolution.created and known_source is None,
        form_version_uuid=str(stable_id), contract_hash=version.content_hash,
        occurred_on=occurred_on.isoformat(), timezone=event.timezone_name,
        offline_origin={"trailer_identity": preparation.trailer.identity,
            "source_actor_uuid": payload.get("source_actor_uuid"), "envelope_hash": expected_hash,
            "original_metadata": deepcopy(payload.get("metadata") or {})})
    submission = RegistrationSubmission.objects.create(
        form_version=version, participant=source, event=event, status="submitted", answers=answers,
        answer_snapshot={"contract": version.snapshot, "answers": answers},
        age_classification="minor" if age_on(date.fromisoformat(mapped["dob"]), occurred_on) < 18 else "adult",
        submitted_at=submitted_at, source="offline", idempotency_key="intake:" + str(operation),
        intake_operation_id=operation, intake_metadata=metadata)
    acceptance = ConsentAcceptance.objects.create(participant=source, submission=submission, document=document,
        metadata={"document_version": document.version, "content_hash": document.content_hash,
                  "channel": "trailer", "intake_operation": str(operation),
                  "trailer_identity": preparation.trailer.identity})
    ConsentAcceptance.objects.filter(pk=acceptance.pk).update(accepted_at=accepted_at)
    review = None
    if resolution.review_required or auto_alias:
        review = ParticipantMatchReview.objects.create(source_submission=submission, source_participant=source,
            candidate_participant=target if auto_alias else (
                Participant.objects.get(pk=resolution.candidates[0]) if len(resolution.candidates) == 1 else None),
            candidates=list(resolution.candidates), conflict_reasons=list(resolution.reasons),
            status="resolved" if auto_alias else "open", created_at=timezone.now(), updated_at=timezone.now(),
            reviewed_at=timezone.now() if auto_alias else None,
            evidence={"policy": "dual_contact_11b", "automatic_new_source_association": auto_alias,
                      "offline_registration": payload.get("registration"),
                      "offline_preparation": str(preparation.pk)})
        if auto_alias:
            ParticipantIdentityLink.objects.create(review=review, source_participant=source,
                                                   candidate_participant=target)
    if not resolution.review_required:
        with fresh_identity_context():
            project_intake(submission=submission, target=target,
                           is_new=resolution.created and source.pk == target.pk)
        if resolution.created and source.pk == target.pk:
            initial_acquisition(participant=source, channel="event", acquired_at=submitted_at,
                event=event, source_submission=submission,
                provenance="First completed trusted offline intake " + str(operation), actor=None)
        if payload.get("registration"):
            try:
                with transaction.atomic(), fresh_identity_context():
                    import_registration(preparation=preparation, payload=payload["registration"],
                                        source=source, target=target)
            except (ValidationError, IntegrityError) as exc:
                if review is None:
                    review = ParticipantMatchReview.objects.create(source_submission=submission,
                        source_participant=source, candidate_participant=target, status="open",
                        candidates=[target.pk], conflict_reasons=["offline_registration_conflict"],
                        created_at=timezone.now(), updated_at=timezone.now())
                review.evidence = dict(review.evidence,
                    offline_registration=payload["registration"], offline_preparation=str(preparation.pk),
                    registration_status="held", registration_error="Registration evidence requires staff review.")
                review.save(update_fields=["evidence"])
    record_audit(actor=None, action="intake.offline.imported", instance=submission,
        source="authenticated_trailer", new_data={"trailer_identity": preparation.trailer.identity,
            "operation": str(operation), "identity_review": resolution.review_required,
            "source_actor_uuid": payload.get("source_actor_uuid")})
    if not resolution.review_required:
        from .portal_accounts import queue_invitation
        queue_invitation(resolution.participant)
    return submission, resolution.review_required


@transaction.atomic
def import_registration(*, preparation, payload, source, target):
    """Retain offline UUIDs and QR identity. Collisions are held, never repaired."""
    from apps.events.downsync import when
    try:
        registration_id = uuid.UUID(payload["id"])
        owner_id = uuid.UUID(payload["owner_identity"])
        ticket = payload["ticket"]
        ticket_id, token = uuid.UUID(ticket["id"]), uuid.UUID(ticket["token"])
        number = ticket["number"]
        registered_at = _when(payload["registered_at"])
        issued_at, expires_at = _when(ticket["issued_at"]), _when(ticket["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("Offline registration identifiers are invalid.") from exc
    owner = Participant.objects.select_for_update(of=("self",)).filter(identity_uuid=owner_id, kind="person", archived_at__isnull=True).first()
    if (owner is None or canonical_id(owner.pk) != canonical_id(target.pk)
            or not isinstance(number, str) or len(number) != 10 or not number.isdigit()
            or expires_at < issued_at):
        raise ValidationError("Offline registration ownership or ticket evidence is inconsistent.")
    existing = EventRegistration.objects.select_for_update(of=("self",)).filter(pk=registration_id).first()
    if existing and (existing.event_id != preparation.event_id or existing.participant_id != owner.pk):
        raise ValidationError("An established registration UUID has a different owner or event.")
    numbered = QrTicket.objects.select_for_update(of=("self",)).filter(ticket_number=number).first()
    if numbered:
        if numbered.pk != ticket_id or numbered.registration_id != registration_id or numbered.token != token:
            raise ValidationError("An established ticket number or token conflicts with this envelope.")
        return numbered.registration
    if QrTicket.objects.filter(pk=ticket_id).exists() or QrTicket.objects.filter(token=token).exists():
        raise ValidationError("An established ticket UUID or token belongs to other evidence.")
    if not any(r.start_number <= int(number) <= r.end_number for r in preparation.ticket_ranges.all()):
        raise ValidationError("The offline ticket is outside this preparation's reserved ranges.")
    group = None
    if payload.get("group_id"):
        group = preparation.event.groups.filter(pk=payload["group_id"]).first()
        if group is None:
            raise ValidationError("The offline group does not belong to the event.")
    if existing is None:
        if EventRegistration.objects.filter(event=preparation.event, participant=owner, status="active").exists():
            raise ValidationError("Another active registration exists. Preserve both histories for staff review.")
        existing = EventRegistration.objects.create(id=registration_id, event=preparation.event,
            participant=owner, group=group, source="offline_sync",
            custom_answers=payload.get("custom_answers") or {}, registered_at=registered_at,
            ticket_email_opt_in=bool(payload.get("ticket_email_opt_in")),
            ticket_sms_opt_in=bool(payload.get("ticket_sms_opt_in")))
    elif existing.tickets.exists():
        raise ValidationError("An established registration has different ticket history.")
    QrTicket.objects.create(id=ticket_id, registration=existing, token=token, ticket_number=number,
                           issued_at=issued_at, expires_at=expires_at, is_current=True)
    return existing
