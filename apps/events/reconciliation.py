"""Audited, non-destructive resolution of uncertain offline identities."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    CentralSyncReceipt, OfflineRegistrationEnvelope, ParticipantIdentityLink,
    ParticipantMatchDecision, ParticipantMatchReview,
)
from .services import audit_action
from .sync import import_registration_if_safe


def _require_reviewer(actor):
    if not (actor.is_authenticated and actor.is_active and actor.is_staff
            and actor.has_perm("events.change_participantmatchreview")):
        raise PermissionDenied


@transaction.atomic
def resolve_match(*, review_id, action, reason, actor):
    """Import the source identity, then optionally link it to the candidate.

    A confirmed match is a reversible link, not a destructive database merge.
    Both participant UUIDs and all records remain attached to their original rows.
    """
    _require_reviewer(actor)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required.")
    if len(reason) > 500:
        raise ValidationError("Reason must be 500 characters or fewer.")
    if action not in (ParticipantMatchDecision.Action.SAME, ParticipantMatchDecision.Action.SEPARATE):
        raise ValidationError("Choose same person or keep separate.")
    review = ParticipantMatchReview.objects.select_for_update(of=("self",)).select_related(
        "envelope__preparation__event", "candidate_participant"
    ).get(pk=review_id)
    if review.envelope_id is None:
        raise ValidationError("Use the Participant Review Queue for a smart-intake case; legacy offline decisions do not apply.")
    if review.status != ParticipantMatchReview.Status.OPEN:
        previous = review.decisions.order_by("-created_at", "-pk").first()
        if previous and previous.action == action:
            return review
        raise ValidationError("This match case has already been resolved.")
    envelope = review.envelope
    registration, error = import_registration_if_safe(
        event=envelope.preparation.event, payload=envelope.payload,
        preparation=envelope.preparation, reviewed_match=True,
    )
    if error or registration is None:
        raise ValidationError(error or "The source registration could not be imported.")
    source = registration.participant
    candidate = review.candidate_participant
    if action == ParticipantMatchDecision.Action.SAME:
        if candidate is None or candidate.pk == source.pk:
            raise ValidationError("A distinct candidate is required for a same-person decision.")
        ParticipantIdentityLink.objects.create(
            review=review, source_participant=source, candidate_participant=candidate,
        )
        review.status = ParticipantMatchReview.Status.RESOLVED
    else:
        review.status = ParticipantMatchReview.Status.REJECTED
    review.reviewed_by = actor
    review.reviewed_at = timezone.now()
    review.resolution_note = reason[:240]
    review.save(update_fields=["status", "reviewed_by", "reviewed_at", "resolution_note"])
    ParticipantMatchDecision.objects.create(
        review=review, action=action, actor=actor, reason=reason,
        source_identity=source.identity_uuid,
        candidate_identity=candidate.identity_uuid if candidate else None,
    )
    envelope.status = OfflineRegistrationEnvelope.Status.SYNCED
    envelope.synced_at = timezone.now()
    envelope.save(update_fields=["status", "synced_at"])
    CentralSyncReceipt.objects.filter(
        status=CentralSyncReceipt.Status.REVIEW,
        trailer_identity=envelope.preparation.trailer.identity,
        event_id=envelope.preparation.event_id,
        participant_identity=envelope.participant_identity,
        ticket_number=envelope.ticket_number,
    ).update(status=CentralSyncReceipt.Status.ACCEPTED, last_error="", acknowledged_at=timezone.now())
    audit_action(
        actor=actor, action="participant_match_resolved", instance=review,
        new_data={"decision": action, "source_identity": str(source.identity_uuid),
                  "candidate_identity": str(candidate.identity_uuid) if candidate else ""},
        reason=reason,
    )
    return review


@transaction.atomic
def reverse_match(*, review_id, reason, actor):
    if not (actor.is_authenticated and actor.is_active and actor.is_superuser):
        raise PermissionDenied
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required.")
    if len(reason) > 500:
        raise ValidationError("Reason must be 500 characters or fewer.")
    review = ParticipantMatchReview.objects.select_for_update(of=("self",)).select_related(
        "candidate_participant"
    ).get(pk=review_id)
    if review.envelope_id is None:
        raise ValidationError("Use the Participant Review Queue for a smart-intake case; legacy offline decisions do not apply.")
    link = ParticipantIdentityLink.objects.select_for_update().filter(review=review).first()
    if not link:
        raise ValidationError("This case has no same-person link to reverse.")
    if not link.is_active:
        return review
    link.is_active = False
    link.reversed_at = timezone.now()
    link.reversed_by = actor
    link.save(update_fields=["is_active", "reversed_at", "reversed_by"])
    review.status = ParticipantMatchReview.Status.REJECTED
    review.reviewed_by = actor
    review.reviewed_at = timezone.now()
    review.resolution_note = reason[:240]
    review.save(update_fields=["status", "reviewed_by", "reviewed_at", "resolution_note"])
    ParticipantMatchDecision.objects.create(
        review=review, action=ParticipantMatchDecision.Action.REVERSE,
        actor=actor, reason=reason, source_identity=link.source_participant.identity_uuid,
        candidate_identity=link.candidate_participant.identity_uuid,
    )
    audit_action(
        actor=actor, action="participant_match_reversed", instance=review,
        new_data={"source_identity": str(link.source_participant.identity_uuid),
                  "candidate_identity": str(link.candidate_participant.identity_uuid)},
        reason=reason,
    )
    return review
