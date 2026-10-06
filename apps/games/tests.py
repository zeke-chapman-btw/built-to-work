from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.events.models import Event, EventRegistration
from apps.participants.models import Participant
from apps.stations.models import ExperienceActivity, ExperienceSession, Station
from apps.stations.services import STATION_ACTIVITY, activity_order_for_session, _ensure_activity_rows

from apps.games.models import EventGameConfiguration, GameDefinition, GameSession
from apps.games.services import complete_game_session, complete_test_session, configured_game, next_activity, reset_nonofficial_session, resolve_and_start


class GameFrameworkTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(name='Game framework test', code='GAME-FRAMEWORK-TEST', start_at=now, end_at=now + timedelta(days=1))
        self.session = ExperienceSession.objects.create(event=self.event, mode=ExperienceSession.Mode.STAFF_TEST)

    def test_event_without_game_skips_game_stage_and_session(self):
        self.assertEqual(next_activity(self.event), 'simulator')
        _ensure_activity_rows(self.session)
        activities = set(self.session.activities.values_list('activity', flat=True))
        self.assertEqual(activities, {ExperienceActivity.Activity.KIOSK, ExperienceActivity.Activity.SIMULATOR})
        self.assertIsNone(resolve_and_start(experience_session=self.session))
        self.assertEqual(GameSession.objects.count(), 0)
        self.assertIsNone(self.session.registration_id)
        self.assertIsNone(self.session.participant_id)

    def test_configured_game_launch_is_idempotent_and_mode_isolated(self):
        game = GameDefinition.objects.create(key='test_hunt', display_name='Duck Hunt', implementation_key='test_hunt')
        EventGameConfiguration.objects.create(event=self.event, game=game, configuration_version='2', settings={'lives': 3})
        self.assertEqual(next_activity(self.event), 'game')
        _ensure_activity_rows(self.session)
        self.assertEqual(set(self.session.activities.values_list('activity', flat=True)), {ExperienceActivity.Activity.KIOSK, ExperienceActivity.Activity.GAME, ExperienceActivity.Activity.SIMULATOR})
        run = resolve_and_start(experience_session=self.session)
        self.assertEqual(run, resolve_and_start(experience_session=self.session))
        self.assertEqual(GameSession.objects.count(), 1)
        self.assertEqual(run.mode, ExperienceSession.Mode.STAFF_TEST)
        self.assertFalse(run.is_official)
        self.assertEqual(run.configuration_snapshot, {'lives': 3})
        self.assertEqual(run.configuration_version, '2')
        self.assertIsNone(self.session.registration_id)
        self.assertIsNone(self.session.participant_id)

    def test_test_reset_voids_and_links_replacement_without_official_effect(self):
        game = GameDefinition.objects.create(key='test_hunt', display_name='Duck Hunt', implementation_key='test_hunt')
        EventGameConfiguration.objects.create(event=self.event, game=game)
        first = resolve_and_start(experience_session=self.session)
        complete_test_session(game_session=first)
        reset_nonofficial_session(game_session=first)
        replacement = resolve_and_start(experience_session=self.session)
        first.refresh_from_db()
        self.assertEqual(first.status, GameSession.Status.VOIDED)
        self.assertEqual(first.replaced_by, replacement)
        self.assertEqual(replacement.status, GameSession.Status.IN_PROGRESS)
        self.assertEqual(GameSession.objects.filter(experience_session=self.session, status=GameSession.Status.VOIDED).count(), 1)

    def test_official_runs_cannot_use_test_only_complete_or_reset(self):
        official_session = self._official_session()
        game = GameDefinition.objects.create(key="official_guard", display_name="Official Guard", implementation_key="official_guard")
        EventGameConfiguration.objects.create(event=self.event, game=game, configuration_version="1", settings={})
        _ensure_activity_rows(official_session)
        run = resolve_and_start(experience_session=official_session)
        stale_test_context = GameSession(pk=run.pk, mode=GameSession.Mode.STAFF_TEST)
        with self.assertRaises(PermissionDenied):
            complete_test_session(game_session=stale_test_context)
        with self.assertRaises(PermissionDenied):
            reset_nonofficial_session(game_session=stale_test_context)
        run.refresh_from_db()
        self.assertEqual(run.mode, GameSession.Mode.OFFICIAL)
        self.assertEqual(run.status, GameSession.Status.IN_PROGRESS)

    def _official_session(self):
        participant = Participant.objects.create(first_name="Official", last_name="Game Tester")
        registration = EventRegistration.objects.create(event=self.event, participant=participant)
        return ExperienceSession.objects.create(
            event=self.event, registration=registration, participant=participant,
            mode=ExperienceSession.Mode.OFFICIAL,
        )

    def test_official_game_completion_finishes_generic_activity_idempotently(self):
        session = self._official_session()
        game = GameDefinition.objects.create(key="review_game", display_name="Review Game", implementation_key="review_game")
        EventGameConfiguration.objects.create(event=self.event, game=game, configuration_version="1", settings={"rounds": 1})
        _ensure_activity_rows(session)
        run = resolve_and_start(experience_session=session)
        kiosk = session.activities.get(activity=ExperienceActivity.Activity.KIOSK)
        now = timezone.now()
        kiosk.status = ExperienceActivity.Status.COMPLETED
        kiosk.started_at = now
        kiosk.completed_at = now
        kiosk.save(update_fields=("status", "started_at", "completed_at"))

        completed = complete_game_session(game_session=run, raw_score=42, result_data={"rounds": 1})
        repeated = complete_game_session(game_session=run, raw_score=99, result_data={"rounds": 9})

        completed.refresh_from_db()
        self.assertEqual(completed.status, GameSession.Status.COMPLETED)
        self.assertEqual(completed.raw_score, 42)
        self.assertEqual(completed.result_data, {"rounds": 1})
        self.assertEqual(repeated.pk, completed.pk)
        self.assertEqual(session.activities.get(activity=ExperienceActivity.Activity.GAME).status, ExperienceActivity.Status.COMPLETED)
        rows = {row.activity: row for row in session.activities.all()}
        next_pending = next(name for name in activity_order_for_session(session) if rows[name].status == ExperienceActivity.Status.PENDING)
        self.assertEqual(next_pending, ExperienceActivity.Activity.SIMULATOR)

    def test_voided_or_replaced_game_cannot_be_completed(self):
        game = GameDefinition.objects.create(key="reset_game", display_name="Reset Game", implementation_key="reset_game")
        EventGameConfiguration.objects.create(event=self.event, game=game, configuration_version="1", settings={})
        _ensure_activity_rows(self.session)
        original = resolve_and_start(experience_session=self.session)
        reset_nonofficial_session(game_session=original)
        restarted = resolve_and_start(experience_session=self.session)
        original.refresh_from_db()
        self.assertEqual(original.replaced_by_id, restarted.pk)
        with self.assertRaises(ValidationError):
            complete_game_session(game_session=original)

    def test_nonofficial_completion_does_not_satisfy_official_progression(self):
        game = GameDefinition.objects.create(key="isolated_game", display_name="Isolated Game", implementation_key="isolated_game")
        EventGameConfiguration.objects.create(event=self.event, game=game, configuration_version="1", settings={})
        official = self._official_session()
        _ensure_activity_rows(official)
        _ensure_activity_rows(self.session)
        test_run = resolve_and_start(experience_session=self.session)
        complete_game_session(game_session=test_run, raw_score=3)
        self.assertEqual(official.activities.get(activity=ExperienceActivity.Activity.GAME).status, ExperienceActivity.Status.PENDING)
        self.assertFalse(GameSession.objects.filter(experience_session=official, status=GameSession.Status.COMPLETED).exists())

    def test_generic_game_routing_and_duck_hunt_seed_keep_legacy_distinct(self):
        game = GameDefinition.objects.get(key="duck_hunt")
        EventGameConfiguration.objects.create(event=self.event, game=game, configuration_version="1", settings={})
        _ensure_activity_rows(self.session)
        self.assertEqual(configured_game(self.event).game_id, game.pk)
        self.assertEqual(activity_order_for_session(self.session), (ExperienceActivity.Activity.KIOSK, ExperienceActivity.Activity.GAME, ExperienceActivity.Activity.SIMULATOR))
        self.assertEqual(STATION_ACTIVITY[Station.Type.GAME], ExperienceActivity.Activity.GAME)
        self.assertEqual(STATION_ACTIVITY[Station.Type.DUCK], ExperienceActivity.Activity.DUCK)

    def test_legacy_duck_activity_order_remains_for_existing_sessions(self):
        self.session.activities.create(activity=ExperienceActivity.Activity.DUCK)
        self.assertEqual(activity_order_for_session(self.session), (ExperienceActivity.Activity.KIOSK, ExperienceActivity.Activity.DUCK, ExperienceActivity.Activity.SIMULATOR))
