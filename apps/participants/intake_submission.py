"""Pinned sessions and atomic intake persistence through existing services."""
from datetime import date
import hashlib
import uuid
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.audit import record_audit
from apps.events.models import Event, EventGroup, ParticipantMatchReview
from apps.events.services import register_participant
from .models import (RegistrationSubmission, ConsentAcceptance, ConsentDocumentVersion,
                     IntakeSession, IntakeFormAssignment, TrailerIntakeActivation, RegistrationFormVersion)
from .intake_contracts import validate_answers, canonical_json, age_on, canonical_answers
from .intake_identity import resolve_intake_identity
from .intake_lifecycle import verify_published
from .intake_projection import project_intake
from .workspace_services import initial_acquisition


def _staff_channel(actor, channel):
    if channel in {"staff", "trailer"} and not (
            getattr(actor, "is_authenticated", False) and actor.is_active and actor.is_staff
            and actor.has_perm("participants.add_participant")):
        raise PermissionDenied("Authorized staff assistance is required.")


def _legal(document, *, expected_hash=None, expected_text_hash=None):
    if (not document or not document.is_approved or not document.body.strip()
            or not document.content_hash
            or (document.effective_at and document.effective_at > timezone.now())):
        raise ValidationError("Approved versioned consent is unavailable.")
    text_hash = hashlib.sha256(document.body.encode()).hexdigest()
    if ((expected_hash is not None and document.content_hash != expected_hash)
            or (expected_text_hash is not None and text_hash != expected_text_hash)):
        raise ValidationError("The consent changed. It must be reviewed before proceeding.")
    return text_hash


def _public_event(event):
    if (event.status != Event.Status.UPCOMING or event.public_registration_status != "open"
            or not event.registration_is_open or event.end_at < timezone.now()):
        raise ValidationError("This event is not accepting public registrations.")


@transaction.atomic
def start_session(*, assignment, browser_key, actor=None, event=None, preparation=None):
    assignment = IntakeFormAssignment.objects.select_related("form", "active_version").get(
        pk=assignment.pk, is_active=True)
    if not assignment.form.is_active:
        raise ValidationError("This intake form is retired for new registrations.")
    channel = assignment.channel
    _staff_channel(actor, channel)
    if assignment.event_id and (not event or assignment.event_id != event.pk):
        raise ValidationError("The intake assignment belongs to another event.")
    if channel == "online" and event is not None:
        raise ValidationError("Use an event-linked intake assignment.")
    if channel == "event":
        if event is None:
            raise ValidationError("An event is required.")
        _public_event(event)
    trailer = None
    version = assignment.active_version
    if channel == "trailer":
        if (not preparation or not event or preparation.event_id != event.pk
                or preparation.trailer_id != assignment.trailer_id
                or preparation.status != "active"):
            raise ValidationError("An active matching trailer preparation is required.")
        trailer = preparation.trailer
        activation = TrailerIntakeActivation.objects.select_related("active_version").filter(
            trailer=trailer, event=event).first()
        if activation is None:
            raise ValidationError("A local Admin must install and activate an approved intake form first.")
        version = activation.active_version
        from apps.events.offline import check_offline_readiness
        if not check_offline_readiness(preparation).ready:
            raise ValidationError("Trailer preparation is not ready for offline intake.")
    verify_published(version)
    document = (preparation.consent_document if preparation else
                event.public_consent_document if event else
                ConsentDocumentVersion.objects.filter(key="btw-intake", is_approved=True).order_by("-effective_at", "-version").first())
    text_hash = _legal(document)
    questions = ([{"key": q.key, "label": q.label, "type": q.field_type,
                   "options": q.options, "required": q.is_required}
                  for q in event.registration_questions.filter(is_active=True).order_by("position", "id")]
                 if event else [])
    return IntakeSession.objects.create(
        form_version=version, contract_hash=version.content_hash,
        consent_document=document, consent_hash=document.content_hash,
        event=event, trailer=trailer, channel=channel,
        browser_key=browser_key, context={"consent_text_hash": text_hash,
            "timezone": event.timezone_name if event else str(timezone.get_current_timezone()),
            "preparation_id": str(preparation.pk) if preparation else None,
            "event_questions": questions, "extras": {}})


def _event_extras(session):
    extra = session.context.get("extras", {})
    if not isinstance(extra, dict):
        raise ValidationError("Invalid event registration context.")
    event = session.event
    if event is None:
        return None, {}, False, False
    group = None
    if extra.get("group_id"):
        group = EventGroup.objects.filter(pk=extra["group_id"], event=event, is_active=True).first()
        if group is None:
            raise ValidationError("Choose an active group for this event.")
    if event.group_mode == "required" and group is None:
        raise ValidationError("Choose a group for this event.")
    supplied = extra.get("answers", {})
    if not isinstance(supplied, dict):
        raise ValidationError("Invalid event answers.")
    definitions = session.context.get("event_questions", [])
    if set(supplied) - {q["key"] for q in definitions}:
        raise ValidationError("Unsupported event answers.")
    values = {}
    for q in definitions:
        value = supplied.get(q["key"], "")
        if q["required"] and (not isinstance(value, str) or not value.strip()):
            raise ValidationError(q["label"] + " is required.")
        if value not in (None, ""):
            if not isinstance(value, str):
                raise ValidationError("Event answers must use their supported format.")
            if q["type"] in {"single_choice", "yes_no"}:
                allowed = ({"yes", "no"} if q["type"] == "yes_no" else
                           {str(v.get("value", "")) if isinstance(v, dict) else str(v) for v in q["options"]})
                if value not in allowed:
                    raise ValidationError("Choose a valid answer for " + q["label"])
            elif len(value) > (3000 if q["type"] == "long_text" else 500):
                raise ValidationError("Event answer exceeds its supported length.")
            values[q["key"]] = {"label": q["label"], "answer": value}
    for key in ("ticket_email", "ticket_sms"):
        if type(extra.get(key, False)) is not bool:
            raise ValidationError("Ticket delivery preferences must be Yes or No.")
    return group, values, extra.get("ticket_email", False), extra.get("ticket_sms", False)


@transaction.atomic
def complete_session(*, session_id, actor=None):
    session = IntakeSession.objects.select_for_update(of=("self",)).select_related(
        "form_version__form", "consent_document", "event", "trailer").get(pk=session_id)
    _staff_channel(actor, session.channel)
    actor = actor if getattr(actor, "is_authenticated", False) else None
    if session.submission_id:
        # Fetch after the row lock: a waiting PostgreSQL statement can retain
        # an old outer-join snapshot even after seeing the updated session FK.
        return RegistrationSubmission.objects.get(pk=session.submission_id)
    if session.current_step != 4 or not session.consent_accepted_at:
        raise ValidationError("Complete all four steps and review the required consent.")
    # Lock each nullable relation explicitly; PostgreSQL cannot lock the
    # nullable side of the session's outer joins. Keep event/document lock order.
    pinned = RegistrationFormVersion.objects.select_for_update(of=("self",)).select_related("form").get(
        pk=session.form_version_id)
    version = verify_published(pinned, allow_retired=True)
    session.form_version = version
    if session.event_id:
        session.event = Event.objects.select_for_update(of=("self",)).get(pk=session.event_id)
    session.consent_document = ConsentDocumentVersion.objects.select_for_update(of=("self",)).get(
        pk=session.consent_document_id)
    if session.contract_hash != version.content_hash:
        raise ValidationError("Pinned form integrity check failed.")
    _legal(session.consent_document, expected_hash=session.consent_hash,
           expected_text_hash=session.context["consent_text_hash"])
    if session.channel == "event":
        _public_event(session.event)
    occurred_at = timezone.now()
    occurred_on = timezone.localtime(occurred_at, ZoneInfo(session.context["timezone"])).date()
    cleaned = validate_answers(version.snapshot, session.answers, occurred_on=occurred_on)
    answers = cleaned["answers"]
    group, event_answers, ticket_email, ticket_sms = _event_extras(session)
    resolution = resolve_intake_identity(answers=canonical_answers(version.snapshot, answers), occurred_on=occurred_on)
    participant = resolution.participant
    registration = None
    if session.event_id and not resolution.review_required:
        if session.channel in {"staff", "trailer"} and not actor.has_perm("events.add_eventregistration"):
            raise PermissionDenied
        registration, created = register_participant(
            event=session.event, participant=participant, actor=actor,
            source="staff" if session.channel in {"staff", "trailer"} else "online",
            group=group, custom_answers=event_answers)
        if created:
            registration.ticket_email_opt_in = ticket_email
            registration.ticket_sms_opt_in = ticket_sms
            if ticket_email or ticket_sms:
                registration.ticket_communication_opt_in_at = occurred_at
            registration.save(update_fields=["ticket_email_opt_in", "ticket_sms_opt_in",
                                              "ticket_communication_opt_in_at"])
    payload_hash = hashlib.sha256(canonical_json(answers).encode()).hexdigest()
    submission = RegistrationSubmission.objects.create(
        form_version=version, participant=participant, event=session.event, status="submitted",
        answers=answers, answer_snapshot={"contract": version.snapshot, "answers": answers},
        submitted_at=occurred_at, source="offline" if session.channel == "trailer" else "smart_intake",
        age_classification="minor" if age_on(date.fromisoformat(answers["dob"]), occurred_on) < 18 else "adult",
        idempotency_key="intake:" + str(session.pk), intake_operation_id=session.pk,
        intake_metadata={"schema": "btw-intake-11b-v1", "channel": session.channel,
            "occurred_on": occurred_on.isoformat(), "timezone": session.context["timezone"],
            "form_version_uuid": str(version.stable_id), "contract_hash": session.contract_hash,
            "payload_hash": payload_hash, "answer_states": cleaned["states"],
            "provisional_source": resolution.review_required,
            "created_participant": resolution.created,
            "registration_id": str(registration.pk) if registration else None,
            "registration_status": "pending_identity_review" if resolution.review_required and session.event_id else "registered" if registration else "not_applicable",
            "event_extras": session.context.get("extras", {}),
            "event_questions": session.context.get("event_questions", []),
            "original_event_id": str(session.event_id) if session.event_id else None})
    acceptance = ConsentAcceptance.objects.create(
        participant=participant, submission=submission, document=session.consent_document,
        metadata={"document_version": session.consent_document.version,
                  "content_hash": session.consent_hash, "channel": session.channel,
                  "intake_operation": str(session.pk)})
    ConsentAcceptance.objects.filter(pk=acceptance.pk).update(accepted_at=session.consent_accepted_at)
    if resolution.review_required:
        ParticipantMatchReview.objects.create(
            source_submission=submission, source_participant=participant,
            candidate_participant_id=resolution.candidates[0] if len(resolution.candidates) == 1 else None,
            candidates=list(resolution.candidates), conflict_reasons=list(resolution.reasons),
            evidence={"policy": "dual_contact_11b", "operation": str(session.pk)},
            created_at=occurred_at, updated_at=occurred_at)
    else:
        project_intake(submission=submission, is_new=resolution.created, actor=actor)
        if resolution.created:
            initial_acquisition(
                participant=participant, channel="event" if session.event_id else
                "online" if session.channel == "online" else "unknown",
                acquired_at=occurred_at if session.event_id or session.channel == "online" else None,
                event=session.event if session.event_id else None, source_submission=submission,
                provenance="First completed trusted intake " + str(session.pk), actor=actor)
    if registration is not None:
        if session.channel == "trailer":
            # The transport package and reservation are integrated by the shared
            # trailer service, within this same transaction.
            from .intake_trailer import retain_local_envelope
            retain_local_envelope(session=session, submission=submission, registration=registration,
                                  actor=actor, new_registration=created)
        from apps.events.communications import queue_ticket_delivery
        queue_ticket_delivery(registration)
    elif session.channel == "trailer":
        from .intake_trailer import retain_pending_intake
        retain_pending_intake(session=session, submission=submission, actor=actor)
    session.submission, session.state = submission, "submitted"
    session.save(update_fields=["submission", "state", "updated_at"])
    record_audit(actor=actor if getattr(actor, "is_authenticated", False) else None,
        action="intake.completed", instance=submission, source="smart_intake",
        new_data={"review_required": resolution.review_required, "channel": session.channel})
    return submission
