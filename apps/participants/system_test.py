"""Reusable BTW test identity and nonofficial Event-run lifecycle."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Participant, TestParticipantRun


@transaction.atomic
def get_or_create_test_participant():
    participant, _ = Participant.objects.get_or_create(
        kind=Participant.Kind.SYSTEM_TEST,
        defaults={"first_name": "BTW", "last_name": "Test Participant", "test_qr_token": uuid.uuid4()},
    )
    if participant.test_qr_token is None:
        participant.test_qr_token = uuid.uuid4()
        participant.save(update_fields=("test_qr_token", "updated_at"))
    return participant


def resolve_test_qr(token):
    try:
        token = uuid.UUID(str(token))
    except (TypeError, ValueError, AttributeError):
        return None
    return Participant.objects.filter(
        kind=Participant.Kind.SYSTEM_TEST, test_qr_token=token, archived_at__isnull=True
    ).first()


def simulator_identifier_for(participant):
    if participant.kind != Participant.Kind.SYSTEM_TEST:
        raise ValidationError("Only the BTW test identity has a reserved simulator identifier.")
    return settings.SIMULATOR_TEST_IDENTIFIER


def _validate_test_run(run):
    session = run.experience_session
    if (run.participant.kind != Participant.Kind.SYSTEM_TEST or session.mode != "staff_test"
            or session.registration_id or session.participant_id or session.event_id != run.event_id):
        raise ValidationError("This run is not an isolated BTW test experience.")
    if session.quiz_attempts.filter(is_official=True).exists() or session.game_sessions.filter(mode="official").exists():
        raise ValidationError("A test reset cannot modify official results.")
    if session.simulator_captures.filter(is_official=True).exists():
        raise ValidationError("A test reset cannot modify an official simulator capture.")


def _create_run(participant, event, actor):
    from apps.stations.models import ExperienceSession
    from apps.stations.services import start_test_session
    session = start_test_session(event=event, mode=ExperienceSession.Mode.STAFF_TEST, actor=actor)
    run = TestParticipantRun(participant=participant, event=event, experience_session=session)
    run.full_clean()
    run.save()
    return run


@transaction.atomic
def start_or_resume_test_run(*, participant, event, actor=None):
    participant = Participant.objects.select_for_update().get(pk=participant.pk)
    if participant.kind != Participant.Kind.SYSTEM_TEST or participant.archived_at is not None:
        raise ValidationError("Only the active BTW test identity can start a test run.")
    run = TestParticipantRun.objects.select_related("experience_session").filter(
        participant=participant, event=event, reset_at__isnull=True
    ).first()
    if run is not None:
        _validate_test_run(run)
        if run.experience_session.completed_at is not None:
            return reset_test_participant_run(run=run, actor=actor, reason="New scan after completion")
        return run
    return _create_run(participant, event, actor)


@transaction.atomic
def reset_test_participant_run(*, run, actor=None, reason="Test run restarted"):
    """Void only the current nonofficial run and retain all result/history rows."""
    run = TestParticipantRun.objects.select_for_update().select_related(
        "participant", "experience_session"
    ).get(pk=run.pk)
    if run.reset_at is not None:
        raise ValidationError("This test run was already reset.")
    _validate_test_run(run)
    session = run.experience_session
    now = timezone.now()
    session.quiz_attempts.filter(is_official=False, voided_at__isnull=True).update(
        status="void", voided_at=now, void_reason=reason, voided_by=actor,
    )
    session.game_sessions.filter(mode="staff_test", voided_at__isnull=True).update(
        status="voided", voided_at=now, void_reason=reason, voided_by=actor,
    )
    session.simulator_captures.filter(is_official=False, voided_at__isnull=True).update(
        voided_at=now, void_reason=reason, voided_by=actor,
    )
    run.reset_at = now
    run.reset_reason = reason
    run.save(update_fields=("reset_at", "reset_reason"))
    return _create_run(run.participant, run.event, actor)
