import base64
import importlib
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.assessments.forms import QuestionForm
from apps.assessments.models import AssessmentCategory, EventAssessmentCategory, Question, QuestionSet, QuestionSetItem
from apps.assessments.services import _snapshot_questions, create_attempt, eligible_event_categories, latest_effective_set
from apps.events.models import Event
from apps.core.models import AuditLog
from apps.stations.models import ExperienceSession


class QuizAdminContentTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="quiz-admin", email="quiz-admin@example.test", password="test-pass")
        self.staff = User.objects.create_user(username="quiz-staff", email="quiz-staff@example.test", password="test-pass", is_staff=True)
        self.category = AssessmentCategory.objects.create(name="Welding", slug="welding")
        self.set = QuestionSet.objects.create(category=self.category, name="Welding review", version=1)
        now = timezone.now()
        self.event = Event.objects.create(name="Quiz content test", status=Event.Status.UPCOMING, start_at=now - timedelta(minutes=1), end_at=now + timedelta(hours=2))

    def add_questions(self, counts=(5, 5, 5), category=None, question_set=None):
        category = category or self.category
        question_set = question_set or self.set
        for difficulty, count in zip((Question.Difficulty.EASY, Question.Difficulty.MEDIUM, Question.Difficulty.HARD), counts):
            for i in range(count):
                question = Question.objects.create(category=category, text=f"{difficulty} question {i}", difficulty=difficulty)
                for order, letter in enumerate("ABCD", 1):
                    question.choices.create(text=f"{letter} for {i}", display_order=order, is_correct=letter == "C")
                QuestionSetItem.objects.create(question_set=question_set, question=question)
    def test_admin_can_edit_category_and_staff_cannot(self):
        url = reverse("assessments:category_edit", args=[self.category.pk])
        data = {"name": "Metalwork", "description": "", "is_active": "", "display_order": 4}
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.category.refresh_from_db()
        self.assertEqual((self.category.name, self.category.is_active), ("Metalwork", False))
        self.assertEqual(self.set.category_id, self.category.pk)
        self.assertEqual(self.client.get(reverse("assessments:category_list")).status_code, 200)

    def test_draft_question_authoring_preserves_answer_order_and_preview_is_read_only(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("assessments:question_create", args=[self.set.pk])
        data = {"text": "Which answer?", "difficulty": "medium", "is_active": "on", "choice_a": "First", "choice_b": "Second", "choice_c": "Third", "choice_d": "Fourth", "correct_choice": "c"}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        question = Question.objects.get(text="Which answer?")
        self.assertEqual(list(question.choices.values_list("text", flat=True)), ["First", "Second", "Third", "Fourth"])
        before = self.event.assessment_categories.count()
        response = self.client.get(reverse("assessments:question_preview", args=[question.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Which answer?")
        self.assertContains(response, "PREVIEW")
        self.assertEqual(self.event.assessment_categories.count(), before)
        self.assertEqual(self.client.post(reverse("assessments:question_preview", args=[question.pk])).status_code, 405)

    def test_invalid_image_is_rejected_and_no_image_is_allowed(self):
        base = {"text": "Photo?", "difficulty": "easy", "is_active": "on", "choice_a": "A", "choice_b": "B", "choice_c": "C", "choice_d": "D", "correct_choice": "a"}
        form = QuestionForm(base, category=self.category)
        self.assertTrue(form.is_valid(), form.errors)
        fake = SimpleUploadedFile("fake.png", b"not an image", content_type="image/png")
        form = QuestionForm(base, {"image": fake}, category=self.category)
        self.assertFalse(form.is_valid())
        self.assertIn("image", form.errors)
        pixel = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pJkAAAAASUVORK5CYII=")
        valid = SimpleUploadedFile("pixel.png", pixel, content_type="image/png")
        form = QuestionForm(base, {"image": valid}, category=self.category)
        self.assertTrue(form.is_valid(), form.errors)

    def test_publishing_schedule_and_retired_exclusion(self):
        self.add_questions()
        self.assertIsNone(latest_effective_set(self.category))
        future = timezone.now() + timedelta(days=1)
        self.set.publish(effective_at=future)
        self.assertIsNone(latest_effective_set(self.category))
        self.assertEqual(latest_effective_set(self.category, future + timedelta(seconds=1)), self.set)
        config = EventAssessmentCategory.objects.create(event=self.event, category=self.category, question_set=self.set)
        self.assertEqual(config.pool_mode, EventAssessmentCategory.PoolMode.DEFAULT)
        self.assertNotIn(config, eligible_event_categories(self.event))
        self.set.effective_at = timezone.now() - timedelta(seconds=1)
        self.set.save(update_fields=("effective_at",))
        self.assertIn(config, eligible_event_categories(self.event))
        self.set.status = QuestionSet.Status.RETIRED
        self.set.save(update_fields=("status",))
        self.assertNotIn(config, eligible_event_categories(self.event))

    def test_difficulty_bands_fallback_and_snapshot_history(self):
        self.add_questions((2, 5, 8))
        self.set.publish()
        config = EventAssessmentCategory.objects.create(event=self.event, category=self.category, question_set=self.set)
        first = _snapshot_questions(config)
        self.assertEqual(len(first), 15)
        bands = [item["difficulty"] for item in first]
        self.assertEqual(bands, sorted(bands, key={"easy": 0, "medium": 1, "hard": 2}.get))
        self.assertEqual([choice["text"] for choice in first[0]["choices"]], ["A for 0", "B for 0", "C for 0", "D for 0"] if first[0]["source_question_id"] == str(Question.objects.get(text="easy question 0").pk) else ["A for 1", "B for 1", "C for 1", "D for 1"])
        source = Question.objects.get(pk=first[0]["source_question_id"])
        prior_text = first[0]["text"]
        source.text = "Edited after snapshot"
        source.save(update_fields=("text",))
        self.assertEqual(first[0]["text"], prior_text)
        with patch("apps.assessments.services.random.sample", wraps=__import__("random").sample) as sample:
            _snapshot_questions(config)
            self.assertGreaterEqual(sample.call_count, 3)

    def test_event_curated_pool_and_active_override(self):
        self.add_questions()
        self.set.publish()
        url = reverse("assessments:event_configuration", args=[self.event.pk])
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        data = {"category": str(self.category.pk), "question_set": str(self.set.pk), "pool_mode": "curated", "enabled": "on", "display_order": 1, "override_active": "on", "override_reason": "Approved equipment test"}
        self.assertEqual(self.client.post(url, {k: v for k, v in data.items() if not k.startswith("override_")}).status_code, 403)
        self.assertFalse(EventAssessmentCategory.objects.filter(event=self.event).exists())
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="assessment.event_config.overridden", reason="Approved equipment test").exists())
        config = EventAssessmentCategory.objects.get(event=self.event)
        self.assertEqual(config.pool_mode, EventAssessmentCategory.PoolMode.CURATED)
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.post(url, data).status_code, 403)

    def test_default_pool_pins_effective_version_and_curated_subset_is_respected(self):
        self.add_questions((7, 7, 6))
        self.set.publish()
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("assessments:event_configuration", args=[self.event.pk])
        default = {"category": str(self.category.pk), "pool_mode": "default", "enabled": "on", "display_order": 0,
            "override_active": "on", "override_reason": "Approved pool setup"}
        response = self.client.post(url, default)
        self.assertEqual(response.status_code, 302)
        config = EventAssessmentCategory.objects.get(event=self.event)
        self.assertEqual(config.pool_mode, EventAssessmentCategory.PoolMode.DEFAULT)
        self.assertEqual(config.question_set_id, self.set.pk)
        newer = QuestionSet.objects.create(category=self.category, name="Future version", version=2)
        self.assertEqual(config.question_set_id, self.set.pk)
        selected = list(self.set.items.values_list("question_id", flat=True)[:15])
        curated = {"category": str(self.category.pk), "question_set": str(self.set.pk), "pool_mode": "curated",
            "enabled": "on", "display_order": 0, "curated_questions": [str(value) for value in selected],
            "override_active": "on", "override_reason": "Limit to reviewed questions"}
        self.assertEqual(self.client.post(url + "?edit=" + str(config.pk), curated).status_code, 302)
        config.refresh_from_db()
        self.assertEqual(config.pool_mode, EventAssessmentCategory.PoolMode.CURATED)
        self.assertEqual(set(config.curated_questions.values_list("pk", flat=True)), set(selected))
        self.assertEqual({item["source_question_id"] for item in _snapshot_questions(config)}, {str(value) for value in selected})

    def test_published_set_creates_independent_draft_version(self):
        self.add_questions()
        self.set.publish()
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("assessments:question_set_detail", args=[self.set.pk])
        self.assertEqual(self.client.post(url, {"action": "new_version"}).status_code, 302)
        draft = QuestionSet.objects.get(category=self.category, version=2)
        self.assertEqual(draft.status, QuestionSet.Status.DRAFT)
        self.assertEqual(draft.items.count(), self.set.items.count())
        self.assertEqual(self.client.get(reverse("assessments:question_set_detail", args=[draft.pk])).status_code, 200)
        original = self.set.items.first().question
        clone = draft.items.filter(question__text=original.text).first().question
        self.assertNotEqual(original.pk, clone.pk)
        clone.text = "Changed in Draft"
        clone.save(update_fields=("text",))
        original.refresh_from_db()
        self.assertNotEqual(original.text, clone.text)

    def test_inactive_membership_is_excluded_from_new_attempt_snapshots(self):
        self.add_questions((6, 5, 5))
        other_category = AssessmentCategory.objects.create(name="Electrical", slug="electrical")
        other_set = QuestionSet.objects.create(category=other_category, name="Electrical review", version=1)
        self.add_questions((5, 5, 5), category=other_category, question_set=other_set)
        self.set.publish()
        other_set.publish()
        inactive = self.set.items.filter(question__difficulty=Question.Difficulty.EASY).first()
        inactive.is_active = False
        inactive.save(update_fields=("is_active",))
        first = EventAssessmentCategory.objects.create(event=self.event, category=self.category, question_set=self.set, pool_mode=EventAssessmentCategory.PoolMode.DEFAULT)
        second = EventAssessmentCategory.objects.create(event=self.event, category=other_category, question_set=other_set, pool_mode=EventAssessmentCategory.PoolMode.DEFAULT)
        session = ExperienceSession.objects.create(event=self.event, mode=ExperienceSession.Mode.STAFF_TEST)
        attempt = create_attempt(session, [str(first.pk), str(second.pk)], is_official=False)
        selected = {row["source_question_id"] for section in attempt.sections.all() for row in section.questions_snapshot}
        self.assertNotIn(str(inactive.question_id), selected)
        original_text = attempt.sections.first().questions_snapshot[0]["text"]
        source = Question.objects.get(pk=attempt.sections.first().questions_snapshot[0]["source_question_id"])
        source.text = "Changed after attempt start"
        source.save(update_fields=("text",))
        self.set.status = QuestionSet.Status.RETIRED
        self.set.save(update_fields=("status",))
        reloaded = create_attempt(session, [str(first.pk), str(second.pk)], is_official=False)
        self.assertEqual(reloaded.pk, attempt.pk)
        self.assertEqual(reloaded.sections.first().questions_snapshot[0]["text"], original_text)

    def test_staff_cannot_edit_questions_or_publish(self):
        self.add_questions()
        question = self.set.items.first().question
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        edit_url = reverse("assessments:question_edit", args=[self.set.pk, question.pk])
        edit_data = {"text": "Changed", "difficulty": question.difficulty, "is_active": "on", "choice_a": "A", "choice_b": "B", "choice_c": "C", "choice_d": "D", "correct_choice": "c"}
        self.assertEqual(self.client.post(edit_url, edit_data).status_code, 403)
        publish_url = reverse("assessments:question_set_detail", args=[self.set.pk])
        self.assertEqual(self.client.post(publish_url, {"action": "publish"}).status_code, 403)

    def test_content_actions_write_audit_records(self):
        self.add_questions()
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        detail = reverse("assessments:question_set_detail", args=[self.set.pk])
        self.assertEqual(self.client.post(detail, {"action": "publish"}).status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="assessment.set.published").exists())
        self.assertEqual(self.client.post(detail, {"action": "retire"}).status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="assessment.set.retired").exists())

    def test_legacy_event_pool_migration_classifies_existing_rows_as_default(self):
        self.add_questions()
        config = EventAssessmentCategory.objects.create(event=self.event, category=self.category, question_set=self.set, pool_mode=EventAssessmentCategory.PoolMode.CURATED)
        migration = importlib.import_module("apps.assessments.migrations.0003_eventassessmentcategory_curated_questions_and_more")
        class AppsRegistry:
            @staticmethod
            def get_model(app_label, model_name):
                self.assertEqual((app_label, model_name), ("assessments", "EventAssessmentCategory"))
                return EventAssessmentCategory
        migration.classify_legacy_event_pools(AppsRegistry(), None)
        config.refresh_from_db()
        self.assertEqual(config.pool_mode, EventAssessmentCategory.PoolMode.DEFAULT)
