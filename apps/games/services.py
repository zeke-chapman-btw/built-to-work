from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from apps.core.audit import record_audit
from apps.stations.models import ExperienceActivity
from .models import EventGameConfiguration, GameSession

def configured_game(event):
    try:
        config = EventGameConfiguration.objects.select_related('game').get(event=event)
    except EventGameConfiguration.DoesNotExist:
        return None
    return config if config.game.active else None

def next_activity(event):
    return 'game' if configured_game(event) else 'simulator'

def _validate_official_progress(experience):
    if experience.mode != "official":
        return
    from apps.stations.services import validate_session_identity, _prior_steps_complete, activity_order_for_session
    validate_session_identity(experience)
    if ExperienceActivity.Activity.GAME not in activity_order_for_session(experience):
        raise ValidationError("This experience has no configured Game step.")
    if _prior_steps_complete(experience, ExperienceActivity.Activity.GAME):
        raise ValidationError("Complete the required Kiosk activity before starting the Game.")

def resolve_and_start(*, experience_session):
    config = configured_game(experience_session.event)
    if config is None:
        return None
    mode = experience_session.mode
    if mode not in GameSession.Mode.values:
        raise ValidationError('Unsupported experience mode.')
    with transaction.atomic():
        experience_session = type(experience_session).objects.select_for_update().get(pk=experience_session.pk)
        _validate_official_progress(experience_session)
        if experience_session.mode == "official" and GameSession.objects.filter(experience_session=experience_session).exclude(status="voided").exclude(game=config.game).exists():
            raise ValidationError("Game configuration changed during this experience; staff review is required.")
        existing = GameSession.objects.select_for_update().filter(experience_session=experience_session, game=config.game).exclude(status=GameSession.Status.VOIDED).first()
        if existing:
            if existing.status == GameSession.Status.IN_PROGRESS:
                _mark_game_activity_started(experience_session)
            return existing
        if experience_session.completed_at is not None:
            raise ValidationError("A completed experience cannot start another Game run.")
        try:
            with transaction.atomic():
                run = GameSession.objects.create(event=experience_session.event, experience_session=experience_session, game=config.game, mode=mode, status=GameSession.Status.IN_PROGRESS, game_version=config.game.implementation_version, configuration_version=config.configuration_version, configuration_snapshot=config.settings, started_at=timezone.now())
        except IntegrityError:
            existing = GameSession.objects.filter(experience_session=experience_session, game=config.game).exclude(status=GameSession.Status.VOIDED).first()
            if existing is None:
                raise
            if existing.status == GameSession.Status.IN_PROGRESS:
                _mark_game_activity_started(experience_session)
            return existing
        previous = GameSession.objects.filter(experience_session=experience_session, game=config.game, status=GameSession.Status.VOIDED, void_reason='Test Mode reset', replaced_by__isnull=True).order_by('-voided_at').first()
        if previous:
            previous.replaced_by = run
            previous.save(update_fields=('replaced_by',))
        _mark_game_activity_started(experience_session)
        record_audit(actor=None, action='game.started', instance=run, new_data={'game_key': config.game.key, 'mode': mode})
        return run


def _mark_game_activity_started(experience_session):
    activity, _ = ExperienceActivity.objects.get_or_create(
        session=experience_session, activity=ExperienceActivity.Activity.GAME
    )
    if activity.status == ExperienceActivity.Status.PENDING:
        activity.status = ExperienceActivity.Status.IN_PROGRESS
        activity.started_at = timezone.now()
        activity.save(update_fields=("status", "started_at"))
    return activity


def complete_game_session(*, game_session, raw_score=None, result_data=None, actor=None):
    """Complete a configured Game run from trusted server-side game code.

    Participant-facing views must never call this for official sessions.
    The same transaction closes the generic GAME experience activity so normal
    progression can continue to Simulator.
    """
    if raw_score is not None and (isinstance(raw_score, bool) or not isinstance(raw_score, int) or raw_score < 0):
        raise ValidationError("Raw score must be a non-negative integer.")
    if result_data is not None and not isinstance(result_data, dict):
        raise ValidationError("Game result data must be an object.")

    with transaction.atomic():
        run = GameSession.objects.select_for_update().select_related(
            "event", "experience_session", "game"
        ).get(pk=game_session.pk)
        experience = run.experience_session
        _validate_official_progress(experience)
        if run.status == GameSession.Status.COMPLETED and ((raw_score is not None and raw_score != run.raw_score) or (result_data is not None and result_data != run.result_data)):
            raise ValidationError("This Game result is already recorded with different data.")
        if run.mode not in GameSession.Mode.values or run.mode != experience.mode:
            raise ValidationError("Game mode does not match its experience session.")
        if run.event_id != experience.event_id:
            raise ValidationError("Game and experience session must belong to the same Event.")
        if run.status == GameSession.Status.VOIDED or run.replaced_by_id:
            raise ValidationError("This Game session is voided or has been replaced.")
        config = configured_game(run.event)
        if config is None or config.game_id != run.game_id:
            raise ValidationError("This Game is no longer configured for the Event.")
        activity, _ = ExperienceActivity.objects.select_for_update().get_or_create(
            session=experience, activity=ExperienceActivity.Activity.GAME
        )
        if run.status != GameSession.Status.COMPLETED:
            if run.status != GameSession.Status.IN_PROGRESS:
                raise ValidationError("Only an in-progress Game session can be completed.")
            now = timezone.now()
            run.status = GameSession.Status.COMPLETED
            run.completed_at = now
            if raw_score is not None:
                run.raw_score = raw_score
            if result_data is not None:
                run.result_data = result_data
            run.save(update_fields=("status", "completed_at", "raw_score", "result_data"))
            record_audit(actor=actor, action="game.completed", instance=run, new_data={"game_key": run.game.key, "mode": run.mode})
        if activity.status != ExperienceActivity.Status.COMPLETED:
            now = run.completed_at or timezone.now()
            activity.status = ExperienceActivity.Status.COMPLETED
            activity.started_at = activity.started_at or run.started_at or now
            activity.completed_at = now
            activity.save(update_fields=("status", "started_at", "completed_at"))
        return run

def complete_test_session(*, game_session):
    if not game_session.pk:
        if game_session.mode == GameSession.Mode.OFFICIAL:
            raise PermissionDenied("Official games cannot use the test completion action.")
        raise ValidationError("A persisted Game session is required.")
    with transaction.atomic():
        run = GameSession.objects.select_for_update().get(pk=game_session.pk)
        if run.mode == GameSession.Mode.OFFICIAL:
            raise PermissionDenied("Official games cannot use the test completion action.")
        return complete_game_session(game_session=run)


def reset_nonofficial_session(*, game_session, actor=None):
    if game_session.mode == GameSession.Mode.OFFICIAL:
        raise PermissionDenied('Official sessions cannot be reset through Test Mode.')
    with transaction.atomic():
        run = GameSession.objects.select_for_update().get(pk=game_session.pk)
        if run.mode == GameSession.Mode.OFFICIAL:
            raise PermissionDenied("Official sessions cannot be reset through Test Mode.")
        if run.status != GameSession.Status.VOIDED:
            run.status = GameSession.Status.VOIDED
            run.voided_at = timezone.now()
            run.voided_by = actor
            run.void_reason = 'Test Mode reset'
            run.save(update_fields=('status', 'voided_at', 'voided_by', 'void_reason'))
        activity = ExperienceActivity.objects.filter(
            session=run.experience_session, activity=ExperienceActivity.Activity.GAME
        ).first()
        if activity and run.mode != GameSession.Mode.OFFICIAL:
            activity.status = ExperienceActivity.Status.PENDING
            activity.started_at = None
            activity.completed_at = None
            activity.save(update_fields=('status', 'started_at', 'completed_at'))
        return run
