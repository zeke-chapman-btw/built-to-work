from apps.assessments.models import EventAssessmentCategory
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.assessments.forms import QuestionForm
from apps.assessments.models import (
    AssessmentCategory,
    Question,
    QuestionChoice,
    QuestionSet,
    QuestionSetItem,
)


class AssessmentAuthoringTests(TestCase):
    def setUp(self):
        self.category = AssessmentCategory.objects.create(
            name="Test category", slug="test-category"
        )
        self.question_set = QuestionSet.objects.create(
            category=self.category, name="Test version", version=1
        )

    def add_question(self, difficulty, choice_count=4, correct_count=1):
        question = Question.objects.create(
            category=self.category,
            text=f"Question {difficulty} {Question.objects.count()}",
            difficulty=difficulty,
        )
        for index in range(choice_count):
            QuestionChoice.objects.create(
                question=question,
                text=f"Choice {index + 1}",
                is_correct=index < correct_count,
                display_order=index,
            )
        QuestionSetItem.objects.create(
            question_set=self.question_set, question=question
        )
        return question

    def add_complete_pool(self):
        for difficulty in Question.Difficulty.values:
            for _ in range(5):
                self.add_question(difficulty)

    def test_question_form_creates_four_choices_with_one_correct_answer(self):
        form = QuestionForm(
            data={
                "text": "Form-created question",
                "difficulty": Question.Difficulty.EASY,
                "is_active": "on",
                "choice_a": "A",
                "choice_b": "B",
                "choice_c": "C",
                "choice_d": "D",
                "correct_choice": "a",
            },
            instance=Question(category=self.category),
        )
        self.assertTrue(form.is_valid(), form.errors)
        question = form.save()
        self.assertEqual(question.choices.count(), 4)
        self.assertEqual(question.choices.filter(is_correct=True).count(), 1)

    def test_new_question_can_be_model_validated_before_choices_are_saved(self):
        question = Question(
            category=self.category,
            text="New question",
            difficulty=Question.Difficulty.EASY,
        )
        question.full_clean()

    def test_saved_question_requires_four_choices_and_one_correct_answer(self):
        question = self.add_question(Question.Difficulty.EASY, choice_count=3)
        with self.assertRaises(ValidationError):
            question.full_clean()

    def test_question_set_reports_missing_difficulty_questions(self):
        report = self.question_set.readiness()
        self.assertFalse(report["ready"])
        self.assertEqual(report["missing"], {"easy": 5, "medium": 5, "hard": 5})

    def test_complete_question_set_counts_five_questions_per_difficulty(self):
        self.add_complete_pool()
        report = self.question_set.readiness()
        self.assertEqual(report["counts"], {"easy": 5, "medium": 5, "hard": 5})
        self.assertEqual(report["missing"], {"easy": 0, "medium": 0, "hard": 0})

    def test_question_set_publishes_only_with_complete_pool(self):
        self.add_complete_pool()
        self.question_set.publish()
        self.question_set.refresh_from_db()
        self.assertEqual(self.question_set.status, QuestionSet.Status.PUBLISHED)
        self.assertTrue(self.question_set.readiness()["ready"])

    def test_question_set_publish_rejects_incomplete_pool(self):
        with self.assertRaises(ValidationError):
            self.question_set.publish()
        self.question_set.refresh_from_db()
        self.assertEqual(self.question_set.status, QuestionSet.Status.DRAFT)


class AssessmentEventConfigurationTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.events.models import Event

        now = timezone.now()
        self.event = Event.objects.create(
            name="Assessment configuration test event",
            start_at=now,
            end_at=now + timedelta(hours=4),
        )

    def create_ready_config(self, slug, order, *, display_name=None, active=True, enabled=True):
        category = AssessmentCategory.objects.create(
            name=slug.replace("-", " ").title(), slug=slug, is_active=active
        )
        question_set = QuestionSet.objects.create(
            category=category, name=f"{slug} published set", version=1
        )
        for difficulty in Question.Difficulty.values:
            for question_number in range(5):
                question = Question.objects.create(
                    category=category,
                    text=f"{slug} {difficulty} question {question_number + 1}",
                    difficulty=difficulty,
                )
                for choice_number in range(4):
                    QuestionChoice.objects.create(
                        question=question, text=f"Answer {choice_number + 1}",
                        is_correct=(choice_number == 0), display_order=choice_number,
                    )
                QuestionSetItem.objects.create(question_set=question_set, question=question)
        question_set.publish()
        return EventAssessmentCategory.objects.create(
            event=self.event, category=category, question_set=question_set,
            enabled=enabled, display_order=order, display_name=display_name or "",
        )

    def test_only_valid_enabled_categories_are_offered_in_event_order_with_override(self):
        from apps.assessments.services import eligible_event_categories

        first = self.create_ready_config("later-category", 20, display_name="Event-specific label")
        second = self.create_ready_config("first-category", 10)
        inactive = self.create_ready_config("inactive-category", 1, active=False)
        disabled = self.create_ready_config("disabled-category", 2, enabled=False)
        incomplete_category = AssessmentCategory.objects.create(
            name="Incomplete category", slug="incomplete-category"
        )
        incomplete_set = QuestionSet.objects.create(
            category=incomplete_category, name="Incomplete published set",
            status=QuestionSet.Status.PUBLISHED,
        )
        EventAssessmentCategory.objects.create(
            event=self.event, category=incomplete_category, question_set=incomplete_set,
            enabled=True, display_order=3,
        )

        available = eligible_event_categories(self.event)

        self.assertEqual([config.pk for config in available], [second.pk, first.pk])
        self.assertEqual(available[1].display_name, "Event-specific label")
        self.assertNotIn(inactive.pk, [config.pk for config in available])
        self.assertNotIn(disabled.pk, [config.pk for config in available])

    def test_fewer_than_two_ready_categories_are_available_for_selection(self):
        from apps.assessments.services import eligible_event_categories

        self.create_ready_config("only-category", 1)

        self.assertEqual(len(eligible_event_categories(self.event)), 1)


    def create_official_attempt(self, prefix):
        from apps.events.models import EventRegistration
        from apps.participants.models import Participant
        from apps.stations.models import ExperienceSession
        from apps.assessments.services import create_attempt

        first = self.create_ready_config(f"{prefix}-first", 20, display_name="First display override")
        second = self.create_ready_config(f"{prefix}-second", 10)
        participant = Participant.objects.create(first_name="Workflow", last_name=prefix)
        registration = EventRegistration.objects.create(event=self.event, participant=participant)
        session = ExperienceSession.objects.create(
            event=self.event, mode="official", registration=registration, participant=participant
        )
        attempt = create_attempt(
            session, [str(first.pk), str(second.pk)], is_official=True
        )
        return attempt, session, [str(first.pk), str(second.pk)]

    def test_official_attempt_is_idempotent_and_snapshots_questions_and_answers(self):
        from collections import Counter
        from datetime import timedelta
        from apps.assessments.models import AssessmentResponse, AssessmentSection, QuizAttempt
        from apps.assessments.services import create_attempt, submit_answer

        attempt, session, selected_ids = self.create_official_attempt("snapshot")
        self.assertTrue(attempt.is_official)
        self.assertEqual(attempt.experience_session_id, session.pk)
        sections = list(AssessmentSection.objects.filter(attempt=attempt).order_by("position"))
        self.assertEqual(len(sections), 2)
        self.assertEqual([section.position for section in sections], [1, 2])
        self.assertEqual(sections[0].category_name_snapshot, "First display override")
        for section in sections:
            snapshot = section.questions_snapshot
            self.assertEqual(len(snapshot), 15)
            self.assertEqual(Counter(item["difficulty"] for item in snapshot), {"easy": 5, "medium": 5, "hard": 5})
            self.assertEqual([item["position"] for item in snapshot], list(range(1, 16)))
            self.assertTrue(all(len(item["choices"]) == 4 for item in snapshot))
            self.assertTrue(all("is_correct" not in choice for item in snapshot for choice in item["choices"]))
        original = sections[0].questions_snapshot
        original_question = Question.objects.get(pk=original[0]["source_question_id"])
        original_question.text = "Edited after official start"
        original_question.save(update_fields=["text", "updated_at"])
        reloaded = create_attempt(session, selected_ids, is_official=True)
        self.assertEqual(reloaded.pk, attempt.pk)
        self.assertEqual(QuizAttempt.objects.filter(experience_session=session, is_official=True).count(), 1)
        sections[0].refresh_from_db()
        self.assertEqual(sections[0].questions_snapshot, original)

        first_choice_key = original[0]["choices"][0]["key"]
        later_choice_key = original[0]["choices"][1]["key"]
        self.assertTrue(submit_answer(sections[0], 1, first_choice_key, now=sections[0].started_at + timedelta(seconds=1)))
        self.assertFalse(submit_answer(sections[0], 1, later_choice_key, now=sections[0].started_at + timedelta(seconds=2)))
        response = AssessmentResponse.objects.get(section=sections[0], question_position=1)
        self.assertEqual(response.selected_choice_key, first_choice_key)
        self.assertEqual(AssessmentResponse.objects.filter(section=sections[0]).count(), 1)

    def test_section_deadline_expires_without_answer_and_starts_fresh_45_second_section(self):
        from datetime import timedelta
        from apps.assessments.models import AssessmentResponse, AssessmentSection
        from apps.assessments.services import submit_answer

        attempt, session, _ = self.create_official_attempt("timer")
        first, second = list(AssessmentSection.objects.filter(attempt=attempt).order_by("position"))
        self.assertEqual(first.duration_seconds, 45)
        self.assertEqual(first.deadline_at - first.started_at, timedelta(seconds=45))
        deadline = first.deadline_at
        choice_key = first.questions_snapshot[0]["choices"][0]["key"]

        self.assertFalse(submit_answer(first, 1, choice_key, now=deadline))

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, AssessmentSection.Status.COMPLETE)
        self.assertEqual(AssessmentResponse.objects.filter(section=first).count(), 0)
        self.assertEqual(second.status, AssessmentSection.Status.PENDING)
        self.assertIsNone(second.started_at)
        self.assertIsNone(second.deadline_at)
        from apps.assessments.services import _start_section
        _start_section(second, now=deadline)
        second.refresh_from_db()
        self.assertEqual(second.started_at, deadline)
        self.assertEqual(second.deadline_at - second.started_at, timedelta(seconds=45))

    def test_official_retry_with_reordered_categories_preserves_original_attempt(self):
        from apps.assessments.models import AssessmentSection, QuizAttempt
        from apps.assessments.services import create_attempt

        attempt, session, selected_ids = self.create_official_attempt("retry-order")
        before = list(
            AssessmentSection.objects.filter(attempt=attempt)
            .order_by("position")
            .values_list("category_name_snapshot", flat=True)
        )

        retry = create_attempt(session, list(reversed(selected_ids)), is_official=True)

        self.assertEqual(retry.pk, attempt.pk)
        self.assertEqual(
            QuizAttempt.objects.filter(experience_session=session, is_official=True).count(),
            1,
        )
        after = list(
            AssessmentSection.objects.filter(attempt=attempt)
            .order_by("position")
            .values_list("category_name_snapshot", flat=True)
        )
        self.assertEqual(after, before)


    def test_official_attempt_rejects_disabled_event_category_from_manual_selection(self):
        from apps.assessments.services import create_attempt

        attempt, session, selected_ids = self.create_official_attempt("unavailable")
        attempt.delete()
        disabled = self.create_ready_config("disabled-manual", 3, enabled=False)

        with self.assertRaises(ValidationError):
            create_attempt(
                session,
                [selected_ids[0], str(disabled.pk)],
                is_official=True,
            )


    def test_void_restart_requires_staff_and_reason_and_preserves_history(self):
        from django.contrib.auth import get_user_model
        from django.core.exceptions import PermissionDenied
        from apps.assessments.models import AssessmentSection, QuizAttempt
        from apps.assessments.services import void_and_restart
        from apps.core.models import AuditLog

        attempt, session, _ = self.create_official_attempt("void-restart")
        User = get_user_model()
        staff = User.objects.create_user(
            username="assessment-review-staff",
            password="valid-test-password",
            is_staff=True,
            is_active=True,
        )
        participant_user = User.objects.create_user(
            username="assessment-review-participant",
            password="valid-test-password",
            is_staff=False,
            is_active=True,
        )

        with self.assertRaises(PermissionDenied):
            void_and_restart(attempt, actor=session.participant, reason="Duplicate")
        with self.assertRaises(PermissionDenied):
            void_and_restart(attempt, actor=participant_user, reason="Duplicate")
        with self.assertRaises(ValidationError):
            void_and_restart(attempt, actor=staff, reason="  ")

        audit_count_before = AuditLog.objects.count()
        replacement = void_and_restart(
            attempt, actor=staff, reason="Development verification restart"
        )

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, QuizAttempt.Status.VOID)
        self.assertEqual(attempt.void_reason, "Development verification restart")
        self.assertEqual(AssessmentSection.objects.filter(attempt=attempt).count(), 2)
        self.assertNotEqual(replacement.pk, attempt.pk)
        self.assertEqual(replacement.replacement_for_id, attempt.pk)
        self.assertEqual(
            QuizAttempt.objects.filter(experience_session=session, is_official=True).count(),
            2,
        )
        self.assertGreater(AuditLog.objects.count(), audit_count_before)


    def test_scoring_tallies_are_persisted_and_results_omit_answer_key(self):
        from datetime import timedelta
        from types import SimpleNamespace
        from django.template.loader import render_to_string
        from apps.assessments.models import AssessmentSection
        from apps.assessments.services import submit_answer

        attempt, session, _selected_ids = self.create_official_attempt("scoring")
        sections = list(AssessmentSection.objects.filter(attempt=attempt).order_by("position"))
        first, second = sections
        question = next(item for item in first.questions_snapshot if item["position"] == 1)
        second_question = next(item for item in first.questions_snapshot if item["position"] == 2)
        correct = next(choice for choice in question["choices"] if choice["correct"])
        incorrect = next(choice for choice in question["choices"] if not choice["correct"])
        second_incorrect = next(choice for choice in second_question["choices"] if not choice["correct"])
        now = first.started_at + timedelta(seconds=1)

        self.assertTrue(submit_answer(first, 1, correct["key"], now=now))
        self.assertFalse(submit_answer(first, 1, incorrect["key"], now=now + timedelta(seconds=1)))
        self.assertTrue(submit_answer(first, 2, second_incorrect["key"], now=now + timedelta(seconds=2)))
        first.refresh_from_db()
        second.refresh_from_db()
        attempt.refresh_from_db()

        self.assertEqual(first.answered_count, 2)
        self.assertEqual(first.correct_count, 1)
        self.assertEqual(second.answered_count, 0)
        self.assertEqual(second.correct_count, 0)
        self.assertEqual(attempt.total_answered, 2)
        self.assertEqual(attempt.total_correct, 1)
        self.assertEqual(attempt.sections.count() * 15, 30)

        result_html = render_to_string(
            "assessments/results.html",
            {
                "attempt": attempt,
                "sections": sections,
                "station": SimpleNamespace(code="KIOSK-01"),
                "session": session,
            },
        )
        self.assertIn("1 correct", result_html)
        self.assertIn("TOTAL: 1 CORRECT", result_html)
        self.assertIn("TOTAL: 1 CORRECT", result_html)
        self.assertNotIn("percentile", result_html.lower())
        self.assertNotIn("Answer key", result_html)
        self.assertNotIn(question["text"], result_html)


    def test_participant_question_payload_excludes_answer_keys_and_source_ids(self):
        from apps.assessments.views import _participant_question_payload, _snapshot_choice_key

        question = {
            "position": 1,
            "text": "Which tool is safest?",
            "source_question_id": "internal-question-uuid",
            "choices": [
                {"key": "internal-choice-uuid-1", "text": "Gloves", "correct": True},
                {"key": "internal-choice-uuid-2", "text": "Bare hands", "correct": False},
            ],
        }
        payload = _participant_question_payload(question)
        self.assertEqual(_snapshot_choice_key(question, "0"), "internal-choice-uuid-1")
        self.assertEqual(_snapshot_choice_key(question, "unknown"), "")
        self.assertEqual(payload["choices"], [{"key": "0", "text": "Gloves"}, {"key": "1", "text": "Bare hands"}])
        self.assertNotIn("source_question_id", payload)
        self.assertNotIn("internal-question-uuid", repr(payload))
        self.assertNotIn("correct", repr(payload))
        self.assertNotIn("internal-choice-uuid", repr(payload))


    def test_answering_all_questions_completes_section_early_with_fresh_next_timer(self):
        from datetime import timedelta
        from apps.assessments.models import AssessmentSection
        from apps.assessments.services import submit_answer

        attempt, _session, _selected_ids = self.create_official_attempt("early-finish")
        first, second = list(AssessmentSection.objects.filter(attempt=attempt).order_by("position"))
        self.assertEqual(first.duration_seconds, 45)
        self.assertIsNone(second.started_at)
        first_snapshot = list(first.questions_snapshot)
        final_time = None
        for offset, question in enumerate(first_snapshot, start=1):
            choice = next(item for item in question["choices"] if item["correct"])
            final_time = first.started_at + timedelta(seconds=offset)
            self.assertTrue(submit_answer(first, question["position"], choice["key"], now=final_time))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, AssessmentSection.Status.COMPLETE)
        self.assertEqual(first.answered_count, 15)
        self.assertEqual(first.correct_count, 15)
        self.assertEqual(second.status, AssessmentSection.Status.PENDING)
        self.assertIsNone(second.started_at)
        self.assertIsNone(second.deadline_at)
        from apps.assessments.services import _start_section
        _start_section(second, now=final_time)
        second.refresh_from_db()
        self.assertEqual(second.status, AssessmentSection.Status.ACTIVE)
        self.assertEqual(second.started_at, final_time)
        self.assertEqual(second.deadline_at, final_time + timedelta(seconds=45))


    def test_assessment_authoring_and_restart_require_staff_access(self):
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.urls import reverse
        from apps.assessments.models import QuizAttempt

        attempt, _session, _selected_ids = self.create_official_attempt("access")
        authoring_url = reverse("assessments:category_list")
        restart_url = reverse("assessments:staff_restart", kwargs={"attempt_id": attempt.pk})
        anonymous = Client()
        self.assertIn(anonymous.get(authoring_url).status_code, (302, 403))
        self.assertIn(anonymous.get(restart_url).status_code, (302, 403))

        participant_user = get_user_model().objects.create_user(
            username="assessment-viewer", password="test-password"
        )
        participant_client = Client()
        participant_client.force_login(participant_user)
        self.assertNotEqual(participant_client.get(authoring_url).status_code, 200)
        response = participant_client.post(restart_url, {"reason": "unauthorized attempt"})
        self.assertNotEqual(response.status_code, 200)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, QuizAttempt.Status.ACTIVE)
        self.assertFalse(QuizAttempt.objects.filter(replacement_for=attempt).exists())


    def test_nonofficial_test_attempts_do_not_replace_official_attempt(self):
        from apps.assessments.models import QuizAttempt
        from apps.assessments.services import create_attempt

        official, session, selected_ids = self.create_official_attempt("official-and-demo")
        first_demo = create_attempt(session, selected_ids, is_official=False)
        second_demo = create_attempt(session, selected_ids, is_official=False)
        official.refresh_from_db()

        self.assertTrue(official.is_official)
        self.assertEqual(official.status, QuizAttempt.Status.ACTIVE)
        self.assertFalse(first_demo.is_official)
        self.assertFalse(second_demo.is_official)
        self.assertEqual(first_demo.pk, second_demo.pk)
        self.assertEqual(
            QuizAttempt.objects.filter(experience_session=session, is_official=True, voided_at__isnull=True).count(),
            1,
        )
        self.assertEqual(official.total_correct, 0)
        self.assertEqual(official.total_answered, 0)


    def test_http_refresh_resumes_official_attempt_with_same_deadline_and_order(self):
        from datetime import timedelta
        from unittest.mock import patch
        from django.test import Client
        from django.urls import reverse
        from django.utils import timezone
        from django.contrib.auth import get_user_model
        from apps.stations.models import EventStation, Station
        from apps.stations.services import activate_event_context

        attempt, session, _ = self.create_official_attempt("http-resume")
        station = Station.objects.create(
            code="KIOSK-HTTP-RESUME", name="Kiosk", station_type=Station.Type.KIOSK
        )
        assignment = EventStation.objects.create(event=self.event, station=station)
        staff = get_user_model().objects.create_user(
            username="assessment-http-staff", password="test-password", is_staff=True
        )
        activate_event_context(assignment=assignment, actor=staff)

        client = Client()
        browser_session = client.session
        browser_session["participant_kiosk_session"] = str(session.pk)
        browser_session.save()
        url = reverse(
            "assessments:attempt",
            kwargs={"station_code": station.code, "session_id": session.pk},
        )
        section = attempt.sections.order_by("position").first()
        fixed_now = section.started_at + timedelta(seconds=5)
        original_deadline = section.deadline_at

        with patch("apps.assessments.views.timezone.now", return_value=fixed_now), patch(
            "apps.assessments.services.timezone.now", return_value=fixed_now
        ):
            first = client.get(url)
            self.assertEqual(first.status_code, 200)
            first_section = first.context["section"]
            first_question = first.context["question"]
            first_choice_order = [choice["text"] for choice in first_question["choices"]]
            self.assertEqual(first_section.pk, section.pk)
            self.assertEqual(first_section.deadline_at, original_deadline)

            refreshed_before_answer = client.get(url)
            self.assertEqual(refreshed_before_answer.status_code, 200)
            self.assertEqual(refreshed_before_answer.context["attempt"].pk, attempt.pk)
            self.assertEqual(refreshed_before_answer.context["section"].pk, section.pk)
            self.assertEqual(refreshed_before_answer.context["question"]["text"], first_question["text"])
            self.assertEqual(
                [choice["text"] for choice in refreshed_before_answer.context["question"]["choices"]],
                first_choice_order,
            )
            self.assertEqual(refreshed_before_answer.context["section"].deadline_at, original_deadline)

            snapshot_question = section.questions_snapshot[0]
            correct_index = next(
                index for index, choice in enumerate(snapshot_question["choices"]) if choice["correct"]
            )
            answer = client.post(
                url,
                {
                    "action": "answer",
                    "question_position": snapshot_question["position"],
                    "choice_key": str(correct_index),
                },
            )
            self.assertEqual(answer.status_code, 302)

            refreshed = client.get(url)
            self.assertEqual(refreshed.status_code, 200)
            self.assertEqual(refreshed.context["attempt"].pk, attempt.pk)
            self.assertEqual(refreshed.context["section"].pk, section.pk)
            self.assertEqual(refreshed.context["section"].deadline_at, original_deadline)
            self.assertEqual(
                [choice["text"] for choice in first_question["choices"]], first_choice_order
            )
            self.assertEqual(
                list(section.responses.values_list("question_position", flat=True)),
                [snapshot_question["position"]],
            )
            self.assertEqual(
                __import__("apps.assessments.models", fromlist=["QuizAttempt"]).QuizAttempt.objects.filter(
                    experience_session=session, is_official=True
                ).count(),
                1,
            )
            if session.participant.contact_email:
                self.assertNotContains(refreshed, session.participant.contact_email)
            self.assertNotContains(refreshed, 'correct')


    def test_participant_finish_is_session_scoped_and_returns_to_kiosk_home(self):
        from django.test import Client
        from django.urls import reverse
        from django.contrib.auth import get_user_model
        from apps.stations.models import EventStation, Station
        from apps.stations.services import activate_event_context

        attempt, session, _ = self.create_official_attempt("finish-http")
        station = Station.objects.create(
            code="KIOSK-FINISH-HTTP", name="Kiosk", station_type=Station.Type.KIOSK
        )
        assignment = EventStation.objects.create(event=self.event, station=station)
        staff = get_user_model().objects.create_user(
            username="finish-http-staff", password="test-password", is_staff=True
        )
        activate_event_context(assignment=assignment, actor=staff)
        client = Client()
        browser_session = client.session
        browser_session["participant_kiosk_session"] = str(session.pk)
        browser_session.save()

        response = client.post(reverse(
            "assessments:finish",
            kwargs={"station_code": station.code, "session_id": session.pk},
        ))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("stations:kiosk", kwargs={"station_code": station.code}))
        self.assertNotIn("participant_kiosk_session", client.session)
        self.assertTrue(session.__class__.objects.filter(pk=session.pk).exists())
        self.assertTrue(attempt.__class__.objects.filter(pk=attempt.pk).exists())


    def test_official_assessment_completes_kiosk_then_unlocks_duck_only(self):
        from datetime import timedelta
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.urls import reverse
        from django.utils import timezone
        from apps.assessments.models import QuizAttempt
        from apps.assessments.services import submit_answer
        from apps.stations.models import EventStation, ExperienceActivity, Station
        from apps.stations.services import activate_event_context, _prior_steps_complete

        from apps.events.services import issue_ticket

        attempt, session, _ = self.create_official_attempt("progress-e2e")
        session.participant.first_name = "A_SENTINEL_NAME"
        session.participant.contact_email = "A_SENTINEL_EMAIL@example.invalid"
        session.participant.contact_phone = "A_SENTINEL_PHONE_5550100"
        session.participant.save(update_fields=["first_name", "contact_email", "contact_phone"])
        from django.contrib.contenttypes.models import ContentType
        from apps.core.models import AuditLog

        AuditLog.objects.create(
            action="A_SENTINEL_AUDIT",
            content_type=ContentType.objects.get_for_model(session.participant),
            object_id=str(session.participant.pk),
            source="A_SENTINEL_AUDIT_SOURCE",
            old_data={"sentinel": "A_SENTINEL_AUDIT_OLD"},
            new_data={"sentinel": "A_SENTINEL_AUDIT_NEW"},
            reason="A_SENTINEL_AUDIT_REASON",
        )
        self.event.allow_station_auto_check_in = True
        self.event.status = self.event.Status.UPCOMING
        self.event.start_at = timezone.now() - timedelta(minutes=10)
        self.event.save(update_fields=["allow_station_auto_check_in", "status", "start_at"])
        station = Station.objects.create(
            code="KIOSK-PROGRESS-E2E", name="Kiosk", station_type=Station.Type.KIOSK
        )
        assignment = EventStation.objects.create(event=self.event, station=station)
        staff = get_user_model().objects.create_user(
            username="progress-e2e-staff", password="test-password", is_staff=True
        )
        activate_event_context(assignment=assignment, actor=staff)
        client = Client()
        ticket = issue_ticket(registration=session.registration, actor=None)
        scan = client.post(
            reverse("stations:kiosk", kwargs={"station_code": station.code}),
            {"token": ticket.token},
        )
        self.assertEqual(scan.status_code, 302, scan.context.get("friendly_error") if scan.context else scan.content.decode()[:500])
        self.assertIn("/quizzes/station/", scan.url)
        self.assertEqual(client.session.get("participant_kiosk_session"), str(session.pk))

        kiosk_activity = session.activities.get(activity=ExperienceActivity.Activity.KIOSK)
        self.assertEqual(kiosk_activity.status, ExperienceActivity.Status.IN_PROGRESS)
        attempt_url = reverse(
            "assessments:attempt",
            kwargs={"station_code": station.code, "session_id": session.pk},
        )
        participant_page = client.get(attempt_url)
        self.assertEqual(participant_page.status_code, 200)
        for sentinel in ("A_SENTINEL_EMAIL@example.invalid", "A_SENTINEL_PHONE_5550100", "A_SENTINEL_AUDIT", ticket.token):
            self.assertNotContains(participant_page, sentinel)
        self.assertNotContains(participant_page, "correct")
        for section in attempt.sections.all():
            for item in section.questions_snapshot:
                self.assertNotContains(participant_page, str(item.get("source_question_id", "UNUSED_SENTINEL")))
                for choice in item["choices"]:
                    self.assertNotContains(participant_page, str(choice.get("id", "UNUSED_SENTINEL")))
        self.assertNotContains(participant_page, "staff_test")
        self.assertNotContains(participant_page, "demo session")

        from apps.assessments.models import QuestionSetItem, QuestionChoice
        question_ids = list(QuestionSetItem.objects.filter(
            question_set__category__event_configurations__event=session.event
        ).values_list("question_id", flat=True))
        choice_ids = list(QuestionChoice.objects.filter(
            question_id__in=question_ids
        ).values_list("pk", flat=True))

        sections = list(attempt.sections.order_by("position"))
        for index, question in enumerate(sections[0].questions_snapshot, start=1):
            choice = next(choice for choice in question["choices"] if choice["correct"])
            self.assertTrue(submit_answer(
                sections[0], question["position"], choice["key"],
                now=sections[0].started_at + timedelta(seconds=index),
            ))
        sections[0].refresh_from_db()
        kiosk_activity.refresh_from_db()
        self.assertEqual(sections[0].status, sections[0].Status.COMPLETE)
        self.assertEqual(kiosk_activity.status, ExperienceActivity.Status.IN_PROGRESS)

        sections[1].refresh_from_db()
        if sections[1].started_at is None:
            transition = client.get(attempt_url)
            self.assertEqual(transition.status_code, 200)
            self.assertContains(transition, "NEXT UP")
            self.assertIsNone(sections[1].started_at)
            start_url = reverse("assessments:attempt_start", kwargs={"station_code": station.code, "session_id": session.pk})
            self.assertEqual(client.post(start_url, {"section_id": str(sections[1].pk)}).status_code, 302)
            sections[1].refresh_from_db()
        for index, question in enumerate(sections[1].questions_snapshot, start=1):
            choice = next(choice for choice in question["choices"] if choice["correct"])
            self.assertTrue(submit_answer(
                sections[1], question["position"], choice["key"],
                now=sections[1].started_at + timedelta(seconds=index),
            ))

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, QuizAttempt.Status.COMPLETE)
        results_url = reverse(
            "assessments:results",
            kwargs={"station_code": station.code, "session_id": session.pk},
        )
        results_response = client.get(results_url)
        self.assertEqual(results_response.status_code, 200)
        for sentinel in ("A_SENTINEL_EMAIL@example.invalid", "A_SENTINEL_PHONE_5550100", "A_SENTINEL_AUDIT", ticket.token):
            self.assertNotContains(results_response, sentinel)
        kiosk_activity.refresh_from_db()
        self.assertEqual(kiosk_activity.status, ExperienceActivity.Status.COMPLETED)
        self.assertIsNone(_prior_steps_complete(session, ExperienceActivity.Activity.SIMULATOR))
        results_response = client.get(results_url)
        self.assertEqual(results_response.status_code, 200)
        for sentinel in ("A_SENTINEL_EMAIL@example.invalid", "A_SENTINEL_PHONE_5550100", "A_SENTINEL_AUDIT", ticket.token):
            self.assertNotContains(results_response, sentinel)
        self.assertEqual(
            QuizAttempt.objects.filter(experience_session=session, is_official=True).count(), 1
        )
        self.assertEqual(session.__class__.objects.filter(registration=session.registration).count(), 1)

        from apps.assessments.models import AssessmentResponse
        from apps.participants.models import ParticipantAccount

        original_results = list(AssessmentResponse.objects.filter(section__attempt=attempt).values_list(
            "question_position", "selected_choice_key", "is_correct"
        ))
        kiosk_url = reverse("stations:kiosk", kwargs={"station_code": station.code})
        finish_url = reverse(
            "assessments:finish", kwargs={"station_code": station.code, "session_id": session.pk}
        )
        finish_response = client.post(finish_url)
        self.assertRedirects(finish_response, kiosk_url, fetch_redirect_response=False)
        from apps.events.services import resolve_ticket
        self.assertEqual(resolve_ticket(ticket.token, expected_event=ticket.registration.event).status, "already_checked_in")
        completed_rescan = client.post(kiosk_url, {"token": ticket.token})
        self.assertEqual(completed_rescan.status_code, 200)
        self.assertContains(completed_rescan, "You've already completed this assessment")
        self.assertNotContains(completed_rescan, "ticket couldn't be recognized")
        self.assertEqual(QuizAttempt.objects.filter(experience_session=session, is_official=True).count(), 1)
        self.assertEqual(session.__class__.objects.filter(registration=session.registration).count(), 1)
        self.assertEqual(list(AssessmentResponse.objects.filter(section__attempt=attempt).values_list(
            "question_position", "selected_choice_key", "is_correct"
        )), original_results)

        home = client.get(kiosk_url)
        self.assertEqual(home.status_code, 200)
        for sentinel in ("A_SENTINEL_EMAIL@example.invalid", "A_SENTINEL_PHONE_5550100", "A_SENTINEL_AUDIT", str(session.pk), str(attempt.pk)):
            self.assertNotContains(home, sentinel)
        self.assertNotContains(home, ticket.token)
        for response_page in (participant_page, results_response, home):
            for database_id in [*question_ids, *choice_ids]:
                self.assertNotContains(response_page, str(database_id))
            for internal_value in ("is_correct", "correct_answer", "answer_key", "source_question_id", "staff_test", "demo session"):
                self.assertNotContains(response_page, internal_value)

        participant_user = get_user_model().objects.create_user(
            username="participant-a-account", email="a-login@example.invalid", password="participant-password"
        )
        account = ParticipantAccount.objects.create(
            participant=session.participant, user=participant_user,
            login_email="a-login@example.invalid", status=ParticipantAccount.Status.ACTIVE,
            email_verified_at=timezone.now(), password_set_at=timezone.now(),
        )
        participant_client = Client()
        participant_client.force_login(participant_user)

        attempt_b, session_b, _ = self.create_official_attempt("participant-b")
        session_b.participant.first_name = "B_SENTINEL_NAME"
        session_b.participant.save(update_fields=["first_name"])
        ticket_b = issue_ticket(registration=session_b.registration, actor=None)
        participant_b_url = reverse(
            "assessments:attempt", kwargs={"station_code": station.code, "session_id": session_b.pk}
        )
        before_b = list(AssessmentResponse.objects.filter(section__attempt=attempt_b).values_list(
            "question_position", "selected_choice_key", "is_correct"
        ))
        cross_session = client.get(participant_b_url)
        self.assertEqual(cross_session.status_code, 404)
        self.assertEqual(list(AssessmentResponse.objects.filter(section__attempt=attempt_b).values_list(
            "question_position", "selected_choice_key", "is_correct"
        )), before_b)
        b_restart_url = reverse("assessments:staff_restart", kwargs={"attempt_id": attempt_b.pk})
        self.assertEqual(participant_client.get(b_restart_url).status_code, 403)
        self.assertEqual(participant_client.get(reverse("assessments:dashboard")).status_code, 403)
        participant_session = participant_client.session
        participant_session["participant_kiosk_session"] = str(session.pk)
        participant_session.save()
        self.assertEqual(participant_client.get(participant_b_url).status_code, 404)

        b_scan = client.post(kiosk_url, {"token": ticket_b.token})
        self.assertEqual(b_scan.status_code, 302)
        self.assertEqual(client.session.get("participant_kiosk_session"), str(session_b.pk))
        b_page = client.get(participant_b_url)
        self.assertEqual(b_page.status_code, 200)
        self.assertNotContains(b_page, "A_SENTINEL_NAME")
        self.assertNotContains(b_page, "A_SENTINEL_EMAIL@example.invalid")
        self.assertNotContains(b_page, "A_SENTINEL_PHONE_5550100")
        self.assertEqual(attempt_b.experience_session_id, session_b.pk)
        self.assertEqual(attempt.experience_session_id, session.pk)
        self.assertEqual(QuizAttempt.objects.filter(experience_session=session_b, is_official=True).count(), 1)
        self.assertEqual(session.__class__.objects.filter(registration=session.registration).count(), 1)
        self.assertEqual(session.__class__.objects.filter(registration=session_b.registration).count(), 1)

    def test_not_ready_station_start_is_friendly_and_creates_no_official_attempt(self):
        from datetime import timedelta
        from django.test import Client
        from django.utils import timezone
        from django.urls import reverse
        from apps.assessments.models import QuizAttempt
        from apps.events.models import EventRegistration
        from apps.events.services import issue_ticket
        from apps.participants.models import Participant
        from apps.stations.models import EventStation, ExperienceSession, Station
        from apps.stations.services import activate_event_context

        now = timezone.now()
        self.event.status = self.event.Status.UPCOMING
        self.event.start_at = now - timedelta(minutes=5)
        self.event.end_at = now + timedelta(hours=4)
        self.event.allow_station_auto_check_in = True
        self.event.save(update_fields=["status", "start_at", "end_at", "allow_station_auto_check_in"])
        self.create_ready_config("single-ready-category", 10)
        from apps.assessments.services import eligible_event_categories
        self.assertEqual(len(eligible_event_categories(self.event)), 1)

        participant = Participant.objects.create(first_name="Not", last_name="Ready")
        registration = EventRegistration.objects.create(event=self.event, participant=participant)
        station = Station.objects.create(code="NOTREADY-KIOSK", name="Kiosk", station_type=Station.Type.KIOSK)
        assignment = EventStation.objects.create(event=self.event, station=station)
        activate_event_context(assignment=assignment, actor=None)
        ticket = issue_ticket(registration=registration, actor=None)
        client = Client()
        kiosk_url = reverse("stations:kiosk", kwargs={"station_code": station.code})
        scan_response = client.post(kiosk_url, {"token": ticket.token})
        session_id = client.session["participant_kiosk_session"]
        station_start_url = reverse("assessments:station_start", kwargs={"station_code": station.code, "session_id": session_id})
        self.assertRedirects(scan_response, station_start_url, fetch_redirect_response=False)

        session = ExperienceSession.objects.get(registration=registration)
        self.assertEqual(str(session.pk), str(session_id))
        self.assertEqual(session.event_id, self.event.pk)
        self.assertEqual(len(eligible_event_categories(session.event)), 1)
        attempts = QuizAttempt.objects.filter(experience_session=session, is_official=True)
        self.assertEqual(attempts.count(), 0)
        instructions = client.get(station_start_url)
        self.assertEqual(instructions.status_code, 200)
        self.assertContains(instructions, "HERE’S HOW IT WORKS")
        response = client.post(station_start_url, follow=True)
        self.assertContains(response, "ASSESSMENT UNAVAILABLE", status_code=503)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "ASSESSMENT UNAVAILABLE", status_code=503)
        self.assertContains(response, "Please ask a Built to Work team member for help.", status_code=503)
        for internal_value in ("Traceback", "Exception", "ValidationError", "IntegrityError", "QuestionSet", "question_set", "single-ready-category", "not_ready"):
            self.assertNotContains(response, internal_value, status_code=503)
        self.assertEqual(attempts.count(), 0)
