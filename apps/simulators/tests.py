import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Attendance, Event, EventRegistration, QrTicket
from apps.games.models import EventGameConfiguration, GameDefinition
from apps.participants.models import Participant
from apps.stations.models import EventStation, ExperienceActivity, ExperienceSession, Station
from .models import EventSimulatorConfiguration, SimulatorCapture, SimulatorCaptureProfile
from .services import CaptureConflict, ingest_capture, normalize_identifier, parse_total_score, reconcile_capture


@override_settings(SIMULATOR_INGESTION_TOKEN="local-test-token", SIMULATOR_TEST_IDENTIFIER="0000000000")
class SimulatorCaptureTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(
            name="Simulator integration review", start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=3), status=Event.Status.UPCOMING,
        )
        self.station = Station.objects.create(code="sim-01", name="Simulator 01", station_type=Station.Type.SIMULATOR)
        self.assignment = EventStation.objects.create(event=self.event, station=self.station, is_active_context=True)
        self.profile = SimulatorCaptureProfile.objects.create(name="SimU center display", simulator_type="SimU", profile_version="1")
        EventSimulatorConfiguration.objects.create(
            event=self.event, station=self.station, profile=self.profile, simulator_type="SimU",
        )
        self.participant = Participant.objects.create(first_name="Riley", last_name="Test", contact_phone="4785551234")
        self.registration = EventRegistration.objects.create(event=self.event, participant=self.participant)
        self.experience = ExperienceSession.objects.create(
            event=self.event, registration=self.registration, participant=self.participant,
            mode=ExperienceSession.Mode.OFFICIAL,
        )
        ExperienceActivity.objects.create(
            session=self.experience, activity=ExperienceActivity.Activity.KIOSK,
            status=ExperienceActivity.Status.COMPLETED, started_at=now - timedelta(minutes=15),
            completed_at=now - timedelta(minutes=10),
        )
        ExperienceActivity.objects.create(session=self.experience, activity=ExperienceActivity.Activity.SIMULATOR)
        self.staff = get_user_model().objects.create_user("sim-staff", password="test-password", is_staff=True)

    def payload(self, **changes):
        data = {
            "submission_id": uuid.uuid4(), "event_id": self.event.pk,
            "station_code": self.station.code, "profile_id": self.profile.pk,
            "profile_version": "1", "raw_identifier": "tel478-555-1234",
            "raw_total_score": "83.33%", "captured_at": timezone.now(),
        }
        data.update(changes)
        return data

    def ingest(self, **changes):
        return ingest_capture(**self.payload(**changes))

    def simulator_activity(self):
        return ExperienceActivity.objects.get(session=self.experience, activity=ExperienceActivity.Activity.SIMULATOR)

    def test_score_formats_and_identifier_normalization(self):
        for raw, expected in (("83.33%", "83.33"), ("83.33", "83.33"), ("100%", "100"), ("0%", "0")):
            with self.subTest(raw=raw):
                self.assertEqual(parse_total_score(raw), Decimal(expected))
        self.assertEqual(normalize_identifier("TEL (478) 555-1234", ["tel"]), "4785551234")
        for raw in ("83.OO%", "101%", "-1%", "83,33%", "75.1234567%"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError):
                    parse_total_score(raw)

    def test_official_capture_matches_and_completes_no_game_experience(self):
        capture, replay = self.ingest()
        self.assertFalse(replay)
        self.assertEqual(capture.status, SimulatorCapture.Status.MATCHED)
        self.assertTrue(capture.leaderboard_eligible)
        self.assertEqual(capture.total_score, Decimal("83.33"))
        self.assertEqual(capture.raw_identifier, "tel478-555-1234")
        self.assertEqual(capture.normalized_identifier, "4785551234")
        self.assertEqual(capture.registration, self.registration)
        self.assertEqual(capture.profile_snapshot["profile_version"], "1")
        self.assertEqual(self.simulator_activity().status, ExperienceActivity.Status.COMPLETED)
        self.experience.refresh_from_db()
        self.assertIsNotNone(self.experience.completed_at)
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(QrTicket.objects.count(), 0)

    def test_configured_game_must_be_complete_before_simulator(self):
        game, _ = GameDefinition.objects.get_or_create(key="duck_hunt", defaults={"display_name": "Duck Hunt", "implementation_key": "duck_hunt"})
        EventGameConfiguration.objects.create(event=self.event, game=game)
        game_activity = ExperienceActivity.objects.create(session=self.experience, activity=ExperienceActivity.Activity.GAME)
        blocked, _ = self.ingest()
        self.assertEqual(blocked.status, SimulatorCapture.Status.NEEDS_REVIEW)
        self.assertEqual(blocked.review_reason, "experience_not_ready")
        self.assertFalse(blocked.is_official)
        self.assertEqual(self.simulator_activity().status, ExperienceActivity.Status.PENDING)
        game_activity.status = ExperienceActivity.Status.COMPLETED
        game_activity.started_at = timezone.now()
        game_activity.completed_at = timezone.now()
        game_activity.save(update_fields=("status", "started_at", "completed_at"))
        accepted, _ = self.ingest()
        self.assertEqual(accepted.status, SimulatorCapture.Status.MATCHED)
        self.assertEqual(self.simulator_activity().status, ExperienceActivity.Status.COMPLETED)

    def test_reserved_test_identity_never_matches_or_completes_official_experience(self):
        capture, _ = self.ingest(raw_identifier="tel0000000000")
        self.assertEqual(capture.status, SimulatorCapture.Status.TEST)
        self.assertEqual(capture.mode, SimulatorCapture.Mode.STAFF_TEST)
        self.assertFalse(capture.is_official)
        self.assertIsNone(capture.participant_id)
        self.assertFalse(capture.leaderboard_eligible)
        self.assertEqual(self.simulator_activity().status, ExperienceActivity.Status.PENDING)
        self.assertFalse(SimulatorCapture.objects.filter(status=SimulatorCapture.Status.NEEDS_REVIEW).exists())

    def test_unmatched_ambiguous_and_other_event_identifiers_need_review(self):
        unknown, _ = self.ingest(raw_identifier="tel1111111111")
        self.assertEqual(unknown.review_reason, "unmatched_identifier")
        other = Event.objects.create(
            name="Other Event", start_at=self.event.start_at, end_at=self.event.end_at,
            status=Event.Status.UPCOMING,
        )
        other_person = Participant.objects.create(first_name="Other", last_name="Event", contact_phone="9995551234")
        EventRegistration.objects.create(event=other, participant=other_person)
        scoped, _ = self.ingest(raw_identifier="tel9995551234")
        self.assertEqual(scoped.review_reason, "unmatched_identifier")
        duplicate_person = Participant.objects.create(first_name="Second", last_name="Candidate", contact_phone="4785551234")
        EventRegistration.objects.create(event=self.event, participant=duplicate_person)
        ambiguous, _ = self.ingest()
        self.assertEqual(ambiguous.review_reason, "ambiguous_identifier")
        self.assertIsNone(ambiguous.participant_id)
        self.assertEqual(self.simulator_activity().status, ExperienceActivity.Status.PENDING)

    def test_invalid_score_and_profile_version_preserve_capture_for_review(self):
        malformed, _ = self.ingest(raw_total_score="83.OO%")
        self.assertEqual(malformed.review_reason, "invalid_score")
        self.assertEqual(malformed.raw_total_score, "83.OO%")
        self.assertIsNone(malformed.total_score)
        stale, _ = self.ingest(profile_version="old")
        self.assertEqual(stale.review_reason, "profile_version_mismatch")
        self.assertEqual(stale.profile_version, "old")
        self.assertEqual(self.simulator_activity().status, ExperienceActivity.Status.PENDING)

    def test_network_replay_is_idempotent_and_conflicting_id_is_rejected(self):
        payload = self.payload()
        first, replay = ingest_capture(**payload)
        again, replay = ingest_capture(**payload)
        self.assertTrue(replay)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(SimulatorCapture.objects.count(), 1)
        with self.assertRaises(CaptureConflict):
            ingest_capture(**(payload | {"raw_total_score": "90%"}))
        self.assertEqual(SimulatorCapture.objects.filter(is_official=True).count(), 1)

    def test_second_real_capture_is_retained_without_replacing_official_result(self):
        first, _ = self.ingest()
        second, _ = self.ingest(raw_total_score="90%")
        self.assertEqual(second.status, SimulatorCapture.Status.NEEDS_REVIEW)
        self.assertEqual(second.review_reason, "existing_official_result")
        self.assertFalse(second.leaderboard_eligible)
        first.refresh_from_db()
        self.assertEqual(first.total_score, Decimal("83.33"))
        self.assertEqual(SimulatorCapture.objects.filter(is_official=True, voided_at__isnull=True).count(), 1)

    def test_staff_reconciliation_and_explicit_replacement_keep_history(self):
        first, _ = self.ingest()
        second, _ = self.ingest(raw_total_score="90%")
        with self.assertRaises(ValidationError):
            reconcile_capture(capture=second, registration=self.registration, actor=self.staff, reason="reviewed")
        replacement = reconcile_capture(
            capture=second, registration=self.registration, actor=self.staff,
            reason="Confirmed second run", replace_existing=True,
        )
        first.refresh_from_db()
        self.assertEqual(first.replaced_by, replacement)
        self.assertIsNotNone(first.voided_at)
        self.assertFalse(first.leaderboard_eligible)
        self.assertEqual(replacement.status, SimulatorCapture.Status.RESOLVED)
        self.assertTrue(replacement.leaderboard_eligible)
        self.assertEqual(self.simulator_activity().status, ExperienceActivity.Status.COMPLETED)

    def test_reconciliation_requires_staff_and_same_event(self):
        capture, _ = self.ingest(raw_identifier="tel1111111111")
        ordinary = get_user_model().objects.create_user("ordinary", password="test-password")
        with self.assertRaises(PermissionDenied):
            reconcile_capture(capture=capture, registration=self.registration, actor=ordinary, reason="manual match")
        other = Event.objects.create(name="Other", start_at=self.event.start_at, end_at=self.event.end_at)
        other_registration = EventRegistration.objects.create(event=other, participant=self.participant)
        with self.assertRaises(ValidationError):
            reconcile_capture(capture=capture, registration=other_registration, actor=self.staff, reason="manual match")
        self.assertFalse(capture.is_official)

    def test_api_auth_contract_and_replay(self):
        import json
        data = self.payload()
        data = {key: str(value) if isinstance(value, uuid.UUID) else value.isoformat() if hasattr(value, "isoformat") else value
                for key, value in data.items()}
        url = reverse("simulators:capture_ingest")
        body = json.dumps(data)
        self.assertEqual(self.client.post(url, body, content_type="application/json").status_code, 401)
        with override_settings(SIMULATOR_INGESTION_TOKEN=""):
            self.assertEqual(self.client.post(url, body, content_type="application/json").status_code, 503)
        headers = {"HTTP_AUTHORIZATION": "Bearer local-test-token"}
        first = self.client.post(url, body, content_type="application/json", **headers)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["outcome"], "matched")
        self.assertTrue(first.json()["activity_completed"])
        self.assertNotIn("raw_identifier", first.content.decode())
        second = self.client.post(url, body, content_type="application/json", **headers)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(first.json()["capture_id"], second.json()["capture_id"])
        data["raw_total_score"] = "91%"
        self.assertEqual(self.client.post(url, json.dumps(data), content_type="application/json", **headers).status_code, 409)
