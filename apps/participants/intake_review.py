"""Permissioned Participant Review Queue decisions over existing identity links."""
import hashlib
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from apps.core.audit import record_audit
from apps.events.models import ParticipantMatchReview, ParticipantMatchDecision, ParticipantIdentityLink
from .models import Participant
from .identity import canonical_id, resolved_identity_map, fresh_identity_context
from .intake_projection import project_intake
from .workspace_services import initial_acquisition

ACTIONS = {"associate_update", "link_only", "separate", "review_later"}


def require_review(actor, *, write=False, update=False):
    needed = ["participants.view_participant", "events.view_participantmatchreview"]
    if write:
        needed.append("events.change_participantmatchreview")
    if update:
        needed.append("participants.change_participant")
    if not (actor.is_authenticated and actor.is_active and actor.is_staff and actor.has_perms(needed)):
        raise PermissionDenied


def review_revision(review):
    stamp = review.updated_at.isoformat() if review.updated_at else ""
    return hashlib.sha256((str(review.pk) + "|" + review.status + "|" + stamp).encode()).hexdigest()


def _new_origin(source, actor):
    metadata = source.intake_metadata
    if not metadata.get("provisional_source"):
        return
    channel = "event" if source.event_id else "online" if metadata["channel"] == "online" else "unknown"
    initial_acquisition(
        participant=source.participant, channel=channel,
        acquired_at=source.submitted_at if channel != "unknown" else None,
        event=source.event if channel == "event" else None, source_submission=source,
        provenance="Distinct-person intake confirmed by staff", actor=actor)


@transaction.atomic
def decide(*, actor, review_id, action, reason, expected_revision, target_id=None, confirmed=False):
    require_review(actor, write=True, update=action in {"associate_update", "separate"})
    if action not in ACTIONS:
        raise ValidationError("Choose a supported review action.")
    reason = (reason or "").strip()
    if not reason or len(reason) > 500:
        raise ValidationError("An attributable reason up to 500 characters is required.")
    review = ParticipantMatchReview.objects.select_for_update(of=("self",)).select_related(
        "source_submission__form_version", "source_submission__participant",
        "source_submission__event", "source_participant").get(pk=review_id, source_submission__isnull=False)
    source = review.source_submission
    if not source.intake_operation_id or review.source_participant_id != source.participant_id:
        raise ValidationError("The intake review source is inconsistent.")
    previous = review.decisions.order_by("-created_at", "-pk").first()
    try:
        requested_target = int(target_id) if target_id is not None else None
    except (TypeError, ValueError) as exc:
        raise ValidationError("Choose a valid participant.") from exc
    if (previous and previous.action == action and previous.actor_id == actor.pk
            and previous.reason == reason and review.candidate_participant_id == requested_target):
        return review
    if review.status not in {"open", "later"}:
        raise ValidationError("This intake has already been resolved.")
    if review_revision(review) != expected_revision:
        raise ValidationError("This review changed while you were editing. Reload it.")
    if action != "review_later" and not confirmed:
        raise ValidationError("Confirm the displayed identity and history consequences.")
    target = None
    if action in {"associate_update", "link_only"}:
        if requested_target is None:
            raise ValidationError("Choose the existing participant explicitly.")
        # Deterministic row locks prevent opposite-direction review deadlocks.
        people = {p.pk: p for p in Participant.objects.select_for_update(of=("self",)).filter(
            pk__in=sorted({review.source_participant_id, requested_target})).order_by("pk")}
        target = people.get(requested_target)
        if not target or target.archived_at or target.kind != "person":
            raise ValidationError("Choose an active person.")
        if canonical_id(target.pk) != target.pk:
            raise ValidationError("The selected participant is now an alias. Reload its canonical profile.")
        if target.pk != review.source_participant_id:
            if not source.intake_metadata.get("provisional_source"):
                raise ValidationError("This source is an established identity. Associating its prior records is outside Build 11B; true record merges are deferred. Review this intake against its retained identity or leave it pending.")
            existing = ParticipantIdentityLink.objects.select_for_update(of=("self",)).filter(
                source_participant_id=review.source_participant_id, is_active=True)
            if existing.exists():
                raise ValidationError("This source already has an identity link. Review it before changing attribution.")
            mapping = resolved_identity_map()
            current, seen = target.pk, set()
            while current in mapping and current not in seen:
                seen.add(current)
                current = mapping[current]
            if current == review.source_participant_id or current in seen:
                raise ValidationError("This identity link would be conflicting or circular.")
            review.candidate_participant = target
            review.status = "resolved"
            review.save(update_fields=["candidate_participant", "status"])
            ParticipantIdentityLink.objects.create(
                review=review, source_participant=review.source_participant, candidate_participant=target)
        else:
            review.candidate_participant, review.status = target, "resolved"
        if action == "associate_update":
            with fresh_identity_context():
                project_intake(submission=source, target=target, is_new=False, actor=actor)
    elif action == "separate":
        review.candidate_participant = None
        review.status = "rejected"
        if ParticipantIdentityLink.objects.filter(review=review, is_active=True).exists():
            raise ValidationError("Reverse an existing link before keeping identities separate.")
        project_intake(submission=source, is_new=bool(source.intake_metadata.get("provisional_source")), actor=actor)
        _new_origin(source, actor)
    else:
        review.status = "later"
        review.candidate_participant = None
    review.reviewed_by, review.reviewed_at = actor, timezone.now()
    review.updated_at = review.reviewed_at
    review.resolution_note = reason[:240]
    review.save(update_fields=["status", "candidate_participant", "reviewed_by",
                               "reviewed_at", "updated_at", "resolution_note"])
    ParticipantMatchDecision.objects.create(
        review=review, action=action, actor=actor, reason=reason,
        source_identity=review.source_participant.identity_uuid,
        candidate_identity=target.identity_uuid if target else None)
    if action != "review_later":
        finish_review_registration(review=review, participant=target or source.participant, actor=actor)
        from .portal_accounts import queue_invitation
        with fresh_identity_context():
            queue_invitation(target or source.participant, actor=actor)
    record_audit(actor=actor, action="intake.review." + action, instance=review,
        source="participant_review", reason=reason,
        new_data={"source_id": review.source_participant_id,
                  "target_id": target.pk if target else None, "status": review.status})
    return review


def finish_review_registration(*, review, participant, actor):
    """Resolve registration separately; never bypass existing event policy."""
    source = review.source_submission
    if not source.event_id:
        return None
    from types import SimpleNamespace
    from apps.events.services import register_participant
    from .intake_submission import _event_extras
    evidence = dict(review.evidence)
    if not actor.has_perm("events.add_eventregistration"):
        evidence.update(registration_status="held", registration_error="Authorized event staff must finish this registration.")
        review.evidence = evidence
        review.save(update_fields=["evidence"])
        return None
    if evidence.get("offline_registration"):
        from .intake_sync import import_registration
        from apps.events.models import OfflineEventPreparation
        from django.db import IntegrityError
        try:
            prep = OfflineEventPreparation.objects.get(pk=evidence["offline_preparation"],
                                                       event_id=source.event_id)
            with transaction.atomic(), fresh_identity_context():
                registration = import_registration(preparation=prep,
                    payload=evidence["offline_registration"], source=source.participant, target=participant)
        except (ValidationError, IntegrityError, OfflineEventPreparation.DoesNotExist):
            evidence.update(registration_status="held",
                registration_error="Offline ticket or registration ownership conflicts require separate review; existing IDs are retained.")
        else:
            evidence.update(registration_status="registered", registration_id=str(registration.pk))
            evidence.pop("registration_error", None)
        review.evidence = evidence
        review.save(update_fields=["evidence"])
        return None
    proxy = SimpleNamespace(event=source.event, context={
        "extras": source.intake_metadata.get("event_extras", {}),
        "event_questions": source.intake_metadata.get("event_questions", [])})
    try:
        group, custom, email, sms = _event_extras(proxy)
        registration, created = register_participant(
            event=source.event, participant=participant, actor=actor,
            source="staff", group=group, custom_answers=custom)
    except ValidationError as exc:
        evidence.update(registration_status="held", registration_error="; ".join(exc.messages))
        review.evidence = evidence
        review.save(update_fields=["evidence"])
        return None
    if created:
        registration.ticket_email_opt_in, registration.ticket_sms_opt_in = email, sms
        if email or sms:
            registration.ticket_communication_opt_in_at = timezone.now()
        registration.save(update_fields=["ticket_email_opt_in", "ticket_sms_opt_in",
                                          "ticket_communication_opt_in_at"])
    evidence.update(registration_status="registered", registration_id=str(registration.pk))
    evidence.pop("registration_error", None)
    review.evidence = evidence
    review.save(update_fields=["evidence"])
    session = getattr(source, "intake_session", None)
    if session is not None and session.channel == "trailer":
        from .intake_trailer import retain_local_envelope
        retain_local_envelope(session=session, submission=source, registration=registration,
                              actor=actor, review=review, new_registration=created)
    from apps.events.communications import queue_ticket_delivery
    queue_ticket_delivery(registration)
    return registration


@transaction.atomic
def retry_registration(*, actor, review_id, expected_revision, reason, confirmed=False):
    require_review(actor, write=True)
    if not actor.has_perm("events.add_eventregistration"):
        raise PermissionDenied
    review = ParticipantMatchReview.objects.select_for_update(of=("self",)).select_related(
        "source_submission__event", "source_submission__participant", "candidate_participant").get(
        pk=review_id, source_submission__isnull=False)
    if review.status not in {"resolved", "rejected"} or review_revision(review) != expected_revision:
        raise ValidationError("Resolve identity first and reload the latest review before retrying.")
    reason = (reason or "").strip()
    if not confirmed or not reason or len(reason) > 500:
        raise ValidationError("Confirm this retry and provide a reason up to 500 characters.")
    target = review.candidate_participant if review.status == "resolved" else review.source_submission.participant
    if target is None:
        raise ValidationError("This review has no retained target identity.")
    finish_review_registration(review=review, participant=target, actor=actor)
    review.updated_at = timezone.now()
    review.save(update_fields=["updated_at"])
    record_audit(actor=actor, action="intake.review.registration_retry", instance=review,
        source="participant_review", reason=reason,
        new_data={"status": review.evidence.get("registration_status", "unknown")})
    return review
