from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.assessments.models import (
    AssessmentCategory,
    EventAssessmentCategory,
    AssessmentSection,
    Question,
    QuestionChoice,
    QuestionSet,
    QuestionSetItem,
    QuizAttempt,
)
from apps.events.models import Attendance, Event, EventRegistration, QrTicket
from apps.participants.models import Participant
from .models import EventStation, ExperienceSession, Station


class KioskTestModeTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(
            name="Kiosk Assessment Test Week",
            code="KIOSK-UI-TEST",
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=3650),
            timezone_name="UTC",
            status=Event.Status.UPCOMING,
        )
        self.station = Station.objects.create(
            code="KIOSK-01", name="Kiosk 01", station_type=Station.Type.KIOSK,
        )
        EventStation.objects.create(
            event=self.event, station=self.station, enabled=True, is_active_context=True,
        )

        self.first_config = self._create_ready_category("test_operations", 1)
        self.second_config = self._create_ready_category("test_safety", 2)

    def _url(self, name, **kwargs):
        return reverse(f"stations:{name}", kwargs=kwargs)

    def test_hidden_service_menu_only_offers_safe_kiosk_actions(self):
        response = self.client.get(self._url("service_menu", station_code=self.station.code))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start Test Mode")
        self.assertContains(response, "Return to Kiosk")
        self.assertNotContains(response, "Participant records")
        self.assertNotContains(response, "Event settings")
        self.assertNotContains(response, "Skip activity")

    def test_home_hides_tokens_and_has_no_idle_or_test_controls(self):
        response = self.client.get(self._url("kiosk", station_code=self.station.code))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SCAN YOUR QR TICKET")
        self.assertContains(response, 'name="token"')
        self.assertContains(response, "requestSubmit()")
        self.assertNotContains(response, "Begin")
        self.assertNotContains(response, "Are you still there?")
        self.assertNotContains(response, "data-idle-screen")
        self.assertNotContains(response, "TEST MODE")
        self.assertNotContains(response, "test_kiosk_session")

    def test_start_reset_and_exit_are_isolated_from_official_records(self):
        before = {
            "participants": Participant.objects.count(),
            "registrations": EventRegistration.objects.count(),
            "attendance": Attendance.objects.count(),
            "tickets": QrTicket.objects.count(),
            "official_sessions": ExperienceSession.objects.filter(mode=ExperienceSession.Mode.OFFICIAL).count(),
            "official_attempts": QuizAttempt.objects.filter(is_official=True).count(),
        }
        response = self.client.post(self._url("service_start_test", station_code=self.station.code))
        self.assertEqual(response.status_code, 302)
        first_id = self.client.session.get("test_kiosk_session")
        self.assertIsNotNone(first_id)
        first = ExperienceSession.objects.get(pk=first_id)
        self.assertEqual(first.mode, ExperienceSession.Mode.STAFF_TEST)
        self.assertIsNone(first.registration_id)
        self.assertIsNone(first.participant_id)
        self.assertEqual(first.event, self.event)
        self.assertIn("/quizzes/station/KIOSK-01/", response.url)
        self.assertNotIn("participant_kiosk_session", self.client.session)

        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assessment Instructions")
        self.assertContains(response, "TEST MODE")

        response = self.client.post(self._url(
            "service_start_new_test", station_code=self.station.code, session_id=first.pk,
        ))
        self.assertEqual(response.status_code, 302)
        second_id = self.client.session.get("test_kiosk_session")
        self.assertIsNotNone(second_id)
        self.assertNotEqual(first_id, second_id)
        first.refresh_from_db()
        self.assertEqual(first.mode, ExperienceSession.Mode.STAFF_TEST)
        self.assertFalse(QuizAttempt.objects.filter(is_official=True).exists())

        response = self.client.post(self._url(
            "service_exit_test", station_code=self.station.code, session_id=second_id,
        ))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("test_kiosk_session", self.client.session)
        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "TEST MODE")

        after = {
            "participants": Participant.objects.count(),
            "registrations": EventRegistration.objects.count(),
            "attendance": Attendance.objects.count(),
            "tickets": QrTicket.objects.count(),
            "official_sessions": ExperienceSession.objects.filter(mode=ExperienceSession.Mode.OFFICIAL).count(),
            "official_attempts": QuizAttempt.objects.filter(is_official=True).count(),
        }
        self.assertEqual(after, before)
        self.assertEqual(ExperienceSession.objects.filter(mode=ExperienceSession.Mode.STAFF_TEST).count(), 2)

    def test_test_reset_cannot_replace_an_unrelated_session(self):
        self.client.post(self._url("service_start_test", station_code=self.station.code))
        test_id = self.client.session["test_kiosk_session"]
        other = ExperienceSession.objects.create(event=self.event, mode=ExperienceSession.Mode.DEMO)
        response = self.client.post(self._url(
            "service_start_new_test", station_code=self.station.code, session_id=test_id,
        ))
        self.assertEqual(response.status_code, 302)
        other.refresh_from_db()
        self.assertEqual(other.mode, ExperienceSession.Mode.DEMO)
        self.assertTrue(ExperienceSession.objects.filter(pk=test_id).exists())

    def test_normal_participant_route_does_not_accept_a_test_session_as_participant(self):
        self.client.post(self._url("service_start_test", station_code=self.station.code))
        test_id = self.client.session["test_kiosk_session"]
        cookie = self.client.session
        cookie.pop("test_kiosk_session")
        cookie["participant_kiosk_session"] = test_id
        cookie.save()
        response = self.client.get(reverse("assessments:station_start", kwargs={"station_code": self.station.code, "session_id": test_id}))
        self.assertEqual(response.status_code, 404)

    def _create_ready_category(self, slug, order):
        category = AssessmentCategory.objects.create(
            name=slug.replace("_", " ").title(), slug=slug, is_active=True
        )
        question_set = QuestionSet.objects.create(
            category=category, name=f"{slug} published set", version=1
        )
        for difficulty in Question.Difficulty.values:
            for index in range(5):
                question = Question.objects.create(
                    category=category,
                    text=f"{slug} {difficulty} question {index}",
                    difficulty=difficulty,
                    is_active=True,
                )
                for choice_number in range(4):
                    QuestionChoice.objects.create(
                        question=question,
                        text=f"Answer {choice_number + 1}",
                        is_correct=choice_number == 0,
                        display_order=choice_number,
                    )
                QuestionSetItem.objects.create(
                    question_set=question_set, question=question
                )
        question_set.publish()
        return EventAssessmentCategory.objects.create(
            event=self.event,
            category=category,
            question_set=question_set,
            enabled=True,
            display_order=order,
        )

    def test_test_mode_runs_both_real_assessment_sections_as_nonofficial(self):
        first_config = self.first_config
        second_config = self.second_config
        before = {
            "participants": Participant.objects.count(),
            "registrations": EventRegistration.objects.count(),
            "attendance": Attendance.objects.count(),
            "tickets": QrTicket.objects.count(),
        }
        self.client.post(self._url("service_start_test", station_code=self.station.code))
        session_id = self.client.session["test_kiosk_session"]
        instructions_url = reverse(
            "assessments:station_start",
            kwargs={"station_code": self.station.code, "session_id": session_id},
        )
        response = self.client.post(instructions_url)
        self.assertEqual(response.status_code, 302)
        selection_url = reverse(
            "assessments:category_selection",
            kwargs={"station_code": self.station.code, "session_id": session_id},
        )
        response = self.client.post(
            selection_url, {"categories": [str(first_config.pk), str(second_config.pk)]}
        )
        self.assertEqual(response.status_code, 302)
        attempt_url = reverse("assessments:attempt", kwargs={"station_code": self.station.code, "session_id": session_id})
        self.assertEqual(response.url, attempt_url)
        activation_url = reverse("assessments:attempt_start", kwargs={"station_code": self.station.code, "session_id": session_id})
        attempt = QuizAttempt.objects.get(experience_session_id=session_id)
        self.assertFalse(attempt.is_official)
        self.assertIsNone(attempt.participant_id)
        sections = list(attempt.sections.order_by("position"))
        self.assertEqual(len(sections), 2)
        for section in sections:
            response = self.client.get(attempt_url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'data-started="false"')
            self.assertContains(response, "disabled")
            self.assertNotContains(response, "data-idle-screen")
            section.refresh_from_db()
            self.assertIsNone(section.started_at)
            self.assertIsNone(section.deadline_at)
            # Posting an answer during the overlay cannot start or answer a section.
            self.client.post(attempt_url, {"action": "answer", "question_position": 1, "choice_key": "0"})
            self.assertEqual(section.responses.count(), 0)
            self.client.post(activation_url, {"section_id": str(section.pk)})
            section.refresh_from_db()
            self.assertEqual(section.status, AssessmentSection.Status.ACTIVE)
            self.assertEqual((section.deadline_at - section.started_at).total_seconds(), 45)
            started_at = section.started_at
            # Duplicate countdown POST cannot restart a timer or start the next category.
            self.client.post(activation_url, {"section_id": str(section.pk)})
            section.refresh_from_db()
            self.assertEqual(section.started_at, started_at)
            self.assertEqual(attempt.sections.filter(status=AssessmentSection.Status.ACTIVE).count(), 1)
            for question in section.questions_snapshot:
                correct_index = next(index for index, choice in enumerate(question["choices"]) if choice["correct"])
                response = self.client.post(attempt_url, {
                    "action": "answer", "question_position": question["position"], "choice_key": str(correct_index),
                })
                self.assertEqual(response.status_code, 302)
            self.assertEqual(section.correct_count, 15)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, QuizAttempt.Status.COMPLETE)
        self.assertEqual(
            list(attempt.sections.order_by("position").values_list("status", flat=True)),
            [AssessmentSection.Status.COMPLETE, AssessmentSection.Status.COMPLETE],
        )
        results_url = reverse(
            "assessments:results",
            kwargs={"station_code": self.station.code, "session_id": session_id},
        )
        response = self.client.get(results_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TEST MODE")
        self.assertContains(response, "Simulator")
        self.assertEqual(Participant.objects.count(), before["participants"])
        self.assertEqual(EventRegistration.objects.count(), before["registrations"])
        self.assertEqual(Attendance.objects.count(), before["attendance"])
        self.assertEqual(QrTicket.objects.count(), before["tickets"])
        self.assertFalse(QuizAttempt.objects.filter(is_official=True).exists())

    def test_missing_ready_questions_has_clear_setup_error_without_session(self):
        EventAssessmentCategory.objects.filter(event=self.event).update(enabled=False)
        response = self.client.post(self._url("service_start_test", station_code=self.station.code))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "two ready categories", status_code=503)
        self.assertEqual(ExperienceSession.objects.count(), 0)

    def test_test_reset_rejects_official_session_and_preserves_attempt(self):
        from apps.assessments.services import create_attempt
        participant = Participant.objects.create(first_name="Review", last_name="Official")
        registration = EventRegistration.objects.create(event=self.event, participant=participant)
        official = ExperienceSession.objects.create(event=self.event, participant=participant, registration=registration)
        attempt = create_attempt(official, [str(self.first_config.pk), str(self.second_config.pk)], is_official=True)
        before = list(attempt.sections.values_list("status", "started_at", "deadline_at"))
        self.client.post(self._url("service_start_test", station_code=self.station.code))
        test_id = self.client.session["test_kiosk_session"]
        response = self.client.post(self._url("service_start_new_test", station_code=self.station.code, session_id=official.pk))
        self.assertEqual(response.status_code, 404)
        # Even a forged test ownership cookie cannot make an official session resettable.
        cookie = self.client.session
        cookie["test_kiosk_session"] = str(official.pk)
        cookie.save()
        response = self.client.post(self._url("service_start_new_test", station_code=self.station.code, session_id=official.pk))
        self.assertEqual(response.status_code, 404)
        attempt.refresh_from_db()
        official.refresh_from_db()
        self.assertTrue(attempt.is_official)
        self.assertIsNone(attempt.voided_at)
        self.assertEqual(official.mode, ExperienceSession.Mode.OFFICIAL)
        self.assertEqual(list(attempt.sections.values_list("status", "started_at", "deadline_at")), before)
        self.assertEqual(QuizAttempt.objects.filter(is_official=True).count(), 1)
        self.assertTrue(ExperienceSession.objects.filter(pk=test_id).exists())

    def test_empty_scan_is_safe_and_home_clears_previous_display_state(self):
        self.client.post(self._url("service_start_test", station_code=self.station.code))
        response = self.client.post(self._url("kiosk", station_code=self.station.code), {"token": ""})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("test_kiosk_session", self.client.session)
        self.assertNotIn("participant_kiosk_session", self.client.session)
        self.assertNotContains(response, "TEST MODE")
        self.assertContains(response, "Please try scanning")
        self.assertNotContains(response, "input.value='';form.requestSubmit()")

    def test_development_setup_is_repeatable_and_preserves_active_context(self):
        from django.core.management import call_command
        from django.test import override_settings
        from apps.assessments.services import eligible_event_categories
        EventStation.objects.filter(event=self.event, station=self.station).update(is_active_context=False)
        live_event = Event.objects.create(name="Unchanged active Event", start_at=timezone.now(), end_at=timezone.now() + timedelta(days=1))
        active_assignment = EventStation.objects.create(event=live_event, station=self.station, is_active_context=True)
        active_before = active_assignment.pk
        with override_settings(DEBUG=True):
            call_command("setup_kiosk_review", station=self.station.code, verbosity=0)
            questions_before = Question.objects.count()
            call_command("setup_kiosk_review", station=self.station.code, verbosity=0)
        self.assertEqual(Question.objects.count(), questions_before)
        self.assertEqual(len(eligible_event_categories(self.event)), 11)
        self.assertTrue(EventStation.objects.get(pk=active_before).is_active_context)
        self.assertFalse(Participant.objects.exists())
        self.assertFalse(EventRegistration.objects.exists())
        self.assertFalse(QrTicket.objects.exists())
        self.assertFalse(Attendance.objects.exists())

    def test_development_setup_refuses_production(self):
        from django.core.management import call_command, CommandError
        from django.test import override_settings
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            call_command("setup_kiosk_review", station=self.station.code)

    def test_development_setup_refuses_official_event_records(self):
        from django.core.management import call_command, CommandError
        from django.test import override_settings
        person = Participant.objects.create(first_name="Protected", last_name="Record")
        EventRegistration.objects.create(event=self.event, participant=person)
        before = Question.objects.count()
        with override_settings(DEBUG=True), self.assertRaises(CommandError):
            call_command("setup_kiosk_review", station=self.station.code)
        self.assertEqual(Question.objects.count(), before)
