from datetime import timedelta
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.assessments.models import QuizAttempt
from apps.events.models import Attendance, Event, EventRegistration, QrTicket
from apps.events.services import register_participant, resolve_ticket
from apps.games.models import GameDefinition, GameSession
from apps.stations.models import EventStation, ExperienceActivity, ExperienceSession, Station
from apps.stations.services import process_station_scan
from apps.simulators.models import EventSimulatorConfiguration, SimulatorCapture, SimulatorCaptureProfile
from apps.simulators.services import ingest_capture
from apps.participants.eligibility import (
    official_experiences, official_game_sessions, official_participants,
    official_quiz_attempts, official_registrations, official_simulator_captures,
)
from apps.participants.identity import find_or_create_participant, match_participant, update_participant_profile
from apps.participants.models import Participant, ParticipantCareerProfile, TestParticipantRun
from apps.participants.normalization import normalize_email, normalize_phone
from apps.participants.system_test import (
    get_or_create_test_participant, reset_test_participant_run,
    resolve_test_qr, simulator_identifier_for, start_or_resume_test_run,
)


class ParticipantFoundationTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event_a = Event.objects.create(name="Event A", start_at=now - timedelta(hours=1),
            end_at=now + timedelta(days=1), status=Event.Status.UPCOMING,
            allow_station_auto_check_in=True)
        self.event_b = Event.objects.create(name="Event B", start_at=now - timedelta(hours=1),
            end_at=now + timedelta(days=1), status=Event.Status.UPCOMING)

    def test_person_without_event_is_reused_for_later_registration(self):
        created = find_or_create_participant(
            first_name="Morgan", last_name="Stone", email=" MORGAN@EXAMPLE.TEST "
        )
        self.assertEqual(created.status, "created")
        person = created.participant
        update_participant_profile(
            participant=person,
            updates={"city": "Atlanta", "career_interests": "Welding"},
        )
        self.assertFalse(EventRegistration.objects.filter(participant=person).exists())
        self.assertEqual(person.event_registrations.count(), 0)

        matched = find_or_create_participant(
            first_name="Morgan", last_name="Stone", email="morgan@example.test"
        )
        self.assertEqual(matched.status, "matched")
        self.assertEqual(matched.participant.pk, person.pk)
        registration, registered = register_participant(
            event=self.event_a, participant=matched.participant, actor=None
        )
        self.assertTrue(registered)
        self.assertEqual(registration.participant_id, person.pk)
        self.assertEqual(Participant.objects.filter(kind=Participant.Kind.PERSON).count(), 1)
        person.refresh_from_db()
        self.assertEqual(person.city, "Atlanta")
        self.assertEqual(
            ParticipantCareerProfile.objects.get(participant=person).career_interests,
            "Welding",
        )

    def test_one_person_many_event_registrations_and_tickets(self):
        first = find_or_create_participant(first_name="Alex", last_name="Rivera", email=" ALEX@EXAMPLE.TEST ")
        second = find_or_create_participant(first_name="Alex", last_name="Rivera", email="alex@example.test")
        self.assertEqual(first.status, "created")
        self.assertEqual(second.status, "matched")
        person = first.participant
        registration_a, created_a = register_participant(event=self.event_a, participant=person, actor=None)
        registration_b, created_b = register_participant(event=self.event_b, participant=person, actor=None)
        again, created_again = register_participant(event=self.event_a, participant=person, actor=None)
        self.assertTrue(created_a and created_b)
        self.assertFalse(created_again)
        self.assertEqual(again.pk, registration_a.pk)
        self.assertNotEqual(registration_a.pk, registration_b.pk)
        self.assertEqual(registration_a.participant_id, registration_b.participant_id)
        ticket_a = registration_a.tickets.get(is_current=True)
        ticket_b = registration_b.tickets.get(is_current=True)
        self.assertNotEqual(ticket_a.token, ticket_b.token)
        self.assertEqual(resolve_ticket(ticket_a.token, expected_event=self.event_b).status, "wrong_event")
        self.assertEqual(Participant.objects.filter(kind=Participant.Kind.PERSON).count(), 1)

    def test_normalization_matching_and_ambiguity(self):
        person = find_or_create_participant(first_name="Taylor", last_name="One", phone="(404) 555-0123").participant
        self.assertEqual(normalize_phone("+1 (404) 555-0123"), "14045550123")
        self.assertEqual(normalize_email(" TAYLOR@EXAMPLE.TEST "), "taylor@example.test")
        self.assertEqual(person.contact_phone, "4045550123")
        self.assertEqual(match_participant(phone="404.555.0123").participant.pk, person.pk)
        person.contact_email = "taylor@example.test"
        person.save()
        self.assertEqual(match_participant(email=" TAYLOR@EXAMPLE.TEST ").participant.pk, person.pk)
        other = Participant.objects.create(first_name="Another", last_name="Person", contact_phone="4045550123")
        outcome = find_or_create_participant(first_name="New", last_name="Name", phone="4045550123")
        self.assertEqual(outcome.status, "ambiguous")
        self.assertEqual(set(outcome.candidate_ids), {person.pk, other.pk})
        self.assertEqual(Participant.objects.count(), 2)
        self.assertEqual(match_participant(identity_uuid=person.identity_uuid).participant.pk, person.pk)
        self.assertEqual(match_participant(identity_uuid="not-a-uuid").status, "invalid_identifier")
        self.assertEqual(match_participant(identity_uuid="00000000-0000-0000-0000-000000000001").status, "not_found")

    def test_profile_update_preserves_event_history(self):
        person = Participant.objects.create(first_name="Jordan", last_name="Lee", contact_email="old@example.test")
        original_identity = person.identity_uuid
        registration, _ = register_participant(event=self.event_a, participant=person, actor=None)
        ticket_id = registration.tickets.get(is_current=True).pk
        updated = update_participant_profile(participant=person, updates={
            "contact_email": " NEW@EXAMPLE.TEST ", "contact_phone": "(404) 555-0000",
            "city": "Atlanta", "career_interests": "Electrical", "willing_to_travel": True,
        })
        self.assertEqual(updated.identity_uuid, original_identity)
        self.assertEqual(updated.contact_email, "new@example.test")
        self.assertEqual(updated.contact_phone, "4045550000")
        self.assertEqual(ParticipantCareerProfile.objects.get(participant=person).career_interests, "Electrical")
        registration.refresh_from_db()
        self.assertEqual(registration.participant_id, person.pk)
        self.assertEqual(registration.tickets.get(is_current=True).pk, ticket_id)
        with self.assertRaises(ValidationError):
            update_participant_profile(participant=person, updates={"kind": Participant.Kind.SYSTEM_TEST})

    def test_test_identity_qr_is_nonofficial_and_reusable(self):
        test_person = get_or_create_test_participant()
        self.assertEqual(get_or_create_test_participant().pk, test_person.pk)
        self.assertEqual(test_person.kind, Participant.Kind.SYSTEM_TEST)
        self.assertEqual(simulator_identifier_for(test_person), settings.SIMULATOR_TEST_IDENTIFIER)
        self.assertEqual(settings.SIMULATOR_TEST_IDENTIFIER, "0000000000")
        self.assertEqual(resolve_test_qr(str(test_person.test_qr_token)).pk, test_person.pk)
        self.assertFalse(official_participants().filter(pk=test_person.pk).exists())
        with self.assertRaises(ValidationError):
            register_participant(event=self.event_a, participant=test_person, actor=None)
        run = start_or_resume_test_run(participant=test_person, event=self.event_a)
        self.assertEqual(run.experience_session.mode, ExperienceSession.Mode.STAFF_TEST)
        self.assertIsNone(run.experience_session.registration_id)
        self.assertIsNone(run.experience_session.participant_id)
        self.assertEqual(start_or_resume_test_run(participant=test_person, event=self.event_a).pk, run.pk)
        self.assertFalse(official_experiences().exists())
        self.assertFalse(official_registrations().exists())
        self.assertFalse(official_quiz_attempts().exists())
        self.assertFalse(official_game_sessions().exists())
        self.assertFalse(official_simulator_captures().exists())
        self.assertFalse(EventRegistration.objects.exists())
        self.assertFalse(QrTicket.objects.exists())
        self.assertFalse(Attendance.objects.exists())

    def test_test_qr_scans_resume_then_reset_without_official_records(self):
        test_person = get_or_create_test_participant()
        station = Station.objects.create(code="KIOSK-7A", name="Kiosk", station_type=Station.Type.KIOSK)
        EventStation.objects.create(event=self.event_a, station=station, enabled=True, is_active_context=True)
        first = process_station_scan(station_code=station.code, token=str(test_person.test_qr_token), actor=None)
        again = process_station_scan(station_code=station.code, token=str(test_person.test_qr_token), actor=None)
        self.assertEqual(first["status"], "activity_in_progress")
        self.assertEqual(first["session"].pk, again["session"].pk)
        run = TestParticipantRun.objects.get(experience_session=first["session"])
        new_run = reset_test_participant_run(run=run)
        self.assertNotEqual(new_run.pk, run.pk)
        self.assertNotEqual(new_run.experience_session_id, run.experience_session_id)
        self.assertEqual(process_station_scan(station_code=station.code,
            token=str(test_person.test_qr_token), actor=None)["session"].pk, new_run.experience_session_id)
        self.assertEqual(ExperienceSession.objects.filter(mode="official").count(), 0)
        self.assertEqual(EventRegistration.objects.count(), 0)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_reset_voids_only_test_results_and_preserves_history(self):
        test_person = get_or_create_test_participant()
        run = start_or_resume_test_run(participant=test_person, event=self.event_a)
        attempt = QuizAttempt.objects.create(experience_session=run.experience_session, is_official=False)
        game = GameDefinition.objects.create(key="test-game", display_name="Test Game", implementation_key="duck_hunt")
        game_run = GameSession.objects.create(event=self.event_a, experience_session=run.experience_session,
            game=game, mode=GameSession.Mode.STAFF_TEST, status=GameSession.Status.IN_PROGRESS,
            game_version="1", configuration_version="1")
        new_run = reset_test_participant_run(run=run, reason="Repeat hardware test")
        attempt.refresh_from_db()
        game_run.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(attempt.status, QuizAttempt.Status.VOID)
        self.assertIsNotNone(attempt.voided_at)
        self.assertEqual(game_run.status, GameSession.Status.VOIDED)
        self.assertIsNotNone(game_run.voided_at)
        self.assertIsNotNone(run.reset_at)
        self.assertEqual(run.reset_reason, "Repeat hardware test")
        self.assertEqual(new_run.experience_session.mode, ExperienceSession.Mode.STAFF_TEST)
        with self.assertRaises(ValidationError):
            reset_test_participant_run(run=run)
        self.assertFalse(official_quiz_attempts().exists())
        self.assertFalse(official_game_sessions().exists())

    def test_official_attempt_must_not_attach_to_test_experience(self):
        test_person = get_or_create_test_participant()
        run = start_or_resume_test_run(participant=test_person, event=self.event_a)
        QuizAttempt.objects.create(experience_session=run.experience_session, is_official=True)
        with self.assertRaises(ValidationError):
            reset_test_participant_run(run=run)
        run.refresh_from_db()
        self.assertIsNone(run.reset_at)
        self.assertEqual(TestParticipantRun.objects.count(), 1)

    def test_reserved_simulator_capture_completes_only_active_test_run(self):
        test_person = get_or_create_test_participant()
        run = start_or_resume_test_run(participant=test_person, event=self.event_a)
        run.experience_session.activities.filter(activity=ExperienceActivity.Activity.KIOSK).update(
            status=ExperienceActivity.Status.COMPLETED, started_at=timezone.now(), completed_at=timezone.now())
        station = Station.objects.create(code="SIM-7A", name="Simulator", station_type=Station.Type.SIMULATOR)
        EventStation.objects.create(event=self.event_a, station=station, enabled=True, is_active_context=True)
        profile = SimulatorCaptureProfile.objects.create(name="Test capture", simulator_type="SimU", profile_version="1")
        EventSimulatorConfiguration.objects.create(event=self.event_a, station=station,
            profile=profile, simulator_type="SimU")
        payload = dict(submission_id=uuid.uuid4(), event_id=self.event_a.pk, station_code=station.code,
            profile_id=profile.pk, profile_version="1", raw_identifier="tel0000000000",
            raw_total_score="88%", captured_at=timezone.now())
        capture, replay = ingest_capture(**payload)
        self.assertFalse(replay)
        self.assertEqual(capture.status, SimulatorCapture.Status.TEST)
        self.assertEqual(capture.mode, SimulatorCapture.Mode.STAFF_TEST)
        self.assertFalse(capture.is_official)
        self.assertIsNone(capture.registration_id)
        self.assertEqual(capture.participant_id, test_person.pk)
        self.assertEqual(capture.experience_session_id, run.experience_session_id)
        self.assertEqual(ingest_capture(**payload)[1], True)
        run.experience_session.refresh_from_db()
        self.assertIsNotNone(run.experience_session.completed_at)
        self.assertEqual(run.experience_session.activities.get(
            activity=ExperienceActivity.Activity.SIMULATOR).status, ExperienceActivity.Status.COMPLETED)
        self.assertFalse(official_simulator_captures().exists())
        self.assertFalse(EventRegistration.objects.exists())
        self.assertFalse(Attendance.objects.exists())
        reset_test_participant_run(run=run)
        capture.refresh_from_db()
        self.assertIsNotNone(capture.voided_at)
        self.assertEqual(capture.status, SimulatorCapture.Status.TEST)

    def test_kiosk_post_routes_test_qr_to_nonofficial_session(self):
        test_person = get_or_create_test_participant()
        station = Station.objects.create(code="KIOSK-POST", name="Kiosk", station_type=Station.Type.KIOSK)
        EventStation.objects.create(event=self.event_a, station=station, enabled=True, is_active_context=True)
        response = self.client.post(reverse("stations:kiosk", kwargs={"station_code": station.code}),
            {"token": str(test_person.test_qr_token)})
        self.assertEqual(response.status_code, 302)
        run = TestParticipantRun.objects.get(participant=test_person, event=self.event_a)
        self.assertEqual(self.client.session["test_kiosk_session"], str(run.experience_session_id))
        self.assertNotIn("participant_kiosk_session", self.client.session)
        self.assertFalse(EventRegistration.objects.exists())
        self.assertFalse(Attendance.objects.exists())

    def test_conflicting_email_and_phone_require_explicit_review(self):
        email_person = Participant.objects.create(first_name="Email", last_name="Person",
            contact_email="shared@example.test")
        phone_person = Participant.objects.create(first_name="Phone", last_name="Person",
            contact_phone="4045551111")
        result = find_or_create_participant(first_name="Unclear", last_name="Person",
            email="SHARED@example.test", phone="(404) 555-1111")
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(set(result.candidate_ids), {email_person.pk, phone_person.pk})
        self.assertEqual(Participant.objects.count(), 2)

    def test_official_queries_include_real_active_event_results_only(self):
        person = Participant.objects.create(first_name="Real", last_name="Candidate")
        registration, _ = register_participant(event=self.event_a, participant=person, actor=None)
        session = ExperienceSession.objects.create(event=self.event_a, registration=registration,
            participant=person, mode=ExperienceSession.Mode.OFFICIAL)
        quiz = QuizAttempt.objects.create(experience_session=session, participant=person, is_official=True)
        game = GameDefinition.objects.create(key="official-game", display_name="Game", implementation_key="duck_hunt")
        game_run = GameSession.objects.create(event=self.event_a, experience_session=session, game=game,
            mode=GameSession.Mode.OFFICIAL, status=GameSession.Status.COMPLETED,
            game_version="1", configuration_version="1")
        self.assertTrue(official_participants().filter(pk=person.pk).exists())
        self.assertTrue(official_registrations().filter(pk=registration.pk).exists())
        self.assertTrue(official_experiences().filter(pk=session.pk).exists())
        self.assertTrue(official_quiz_attempts().filter(pk=quiz.pk).exists())
        self.assertTrue(official_game_sessions().filter(pk=game_run.pk).exists())
        test_person = get_or_create_test_participant()
        self.assertFalse(official_participants().filter(pk=test_person.pk).exists())
        registration.status = EventRegistration.Status.VOID
        registration.save(update_fields=("status",))
        self.assertFalse(official_registrations().filter(pk=registration.pk).exists())
        self.assertFalse(official_experiences().filter(pk=session.pk).exists())
        self.assertFalse(official_quiz_attempts().filter(pk=quiz.pk).exists())
        self.assertFalse(official_game_sessions().filter(pk=game_run.pk).exists())
