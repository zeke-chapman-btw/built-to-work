"""HTTP permissions, pinned wizard and Admin lifecycle using synthetic personas."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, Client
from django.urls import reverse
from . import test_intake_submission_11b as baseline
from .models import IntakeSession, RegistrationSubmission, Participant, RegistrationForm
from .intake_lifecycle import save_draft, publish, revision, assign


class IntakeUI11BTests(TestCase):
    setUp = baseline.IntakeSubmission11BTests.setUp
    answers = baseline.IntakeSubmission11BTests.answers

    def begin(self):
        response = self.client.post(reverse("participants:intake-start", args=[self.assignment.pk]))
        self.assertEqual(response.status_code, 302)
        return IntakeSession.objects.latest("created_at"), response.url

    def step(self, session, url, number, changes=None):
        answers = self.answers(**(changes or {}))
        values = {q["key"]: answers[q["key"]] for q in self.version.snapshot["questions"]
                  if q["step"] == max(1, number - 1) and q["key"] in answers
                  and (number > 2 or (q["type"] == "consent") == (number == 2))}
        session.refresh_from_db()
        from .intake_wizard import session_revision
        values["revision"] = session_revision(session)
        response = self.client.post(url + "?step=" + str(number), values)
        return response

    def test_anonymous_five_step_completion_is_private_and_idempotent(self):
        session, url = self.begin()
        for number in (1, 2, 3, 4, 5):
            response = self.step(session, url, number)
            self.assertEqual(response.status_code, 302, response.content.decode()[:2000])
        completed = self.client.get(url)
        self.assertContains(completed, "Your intake has been received")
        self.assertNotContains(completed, "synthetic@example.test")
        self.assertNotContains(completed, "5550101234")
        self.assertEqual(RegistrationSubmission.objects.count(), 1)
        self.client.post(url, {})
        self.assertEqual(RegistrationSubmission.objects.count(), 1)

    def test_sessions_cannot_be_read_or_posted_by_a_different_browser(self):
        session, url = self.begin()
        other = Client()
        self.assertEqual(other.get(url).status_code, 403)
        self.assertEqual(other.post(url, {}).status_code, 403)
        self.assertFalse(RegistrationSubmission.objects.exists())

    def test_csrf_is_required_and_steps_cannot_be_skipped(self):
        session, url = self.begin()
        self.assertEqual(self.client.get(url + "?step=4").status_code, 404)
        secure = Client(enforce_csrf_checks=True)
        self.assertEqual(secure.post(reverse("participants:intake-start", args=[self.assignment.pk])).status_code, 403)
        response = self.step(session, url, 1, {"email": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please check your answers")
        session.refresh_from_db()
        self.assertEqual(session.current_step, 1)

    def test_started_version_stays_pinned_across_publication_and_assignment(self):
        session, url = self.begin()
        updated = save_draft(actor=self.admin, form_id=self.version.form_id,
                             contract=self.version.snapshot, expected_revision=revision(self.version))
        updated = publish(actor=self.admin, version_id=updated.pk, expected_revision=revision(updated))
        assign(actor=self.admin, version_id=updated.pk, channel="online")
        for number in (1, 2, 3, 4, 5):
            self.assertEqual(self.step(session, url, number).status_code, 302)
        self.assertEqual(RegistrationSubmission.objects.get().form_version_id, self.version.pk)
        later, _ = self.begin()
        self.assertEqual(later.form_version_id, updated.pk)

    def test_admin_controls_and_preview_cannot_be_used_by_operations_staff(self):
        user = get_user_model().objects.create_user(username="synthetic-operations", is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(codename__in=[
            "change_registrationform", "view_registrationform", "view_registrationformversion"]))
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        for url in [reverse("participants:intake-config-index"),
                    reverse("participants:intake-config-detail", args=[self.version.form_id]),
                    reverse("participants:intake-config-preview", args=[self.version.pk])]:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {"action": "retire", "confirmed": "yes"}).status_code, 403)
        self.assertTrue(RegistrationForm.objects.get(pk=self.version.form_id).is_active)

    def test_admin_pages_render_and_duplicate_has_no_submissions(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        for url in [reverse("participants:intake-config-index"),
                    reverse("participants:intake-config-detail", args=[self.version.form_id]),
                    reverse("participants:intake-config-question", args=[self.version.form_id, "first_name"]),
                    reverse("participants:intake-config-child", args=[self.version.form_id, "skills"]),
                    reverse("participants:intake-config-skills", args=[self.version.form_id]),
                    reverse("participants:intake-config-mappings", args=[self.version.form_id])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for step in range(1, 6):
            response = self.client.get(reverse("participants:intake-config-preview", args=[self.version.pk]), {"step": step})
            self.assertEqual(response.status_code, 200)
        self.assertFalse(Participant.objects.exists())
        response = self.client.post(reverse("participants:intake-config-detail", args=[self.version.form_id]), {
            "action": "duplicate", "name": "SYNTHETIC independent copy"})
        self.assertEqual(response.status_code, 302)
        copied = RegistrationForm.objects.exclude(pk=self.version.form_id).get()
        self.assertEqual(copied.versions.get().version, 1)
        self.assertEqual(copied.versions.get().status, "draft")
        self.assertFalse(copied.versions.get().submissions.exists())

    def test_legacy_routes_only_delegate_for_an_explicit_assignment(self):
        response = self.client.get(reverse("participants:registration-form"))
        self.assertEqual(response.url, reverse("participants:intake-start", args=[self.assignment.pk]))
        self.assignment.is_active = False
        self.assignment.save()
        response = self.client.get(reverse("participants:registration-form"))
        self.assertNotEqual(response.status_code, 302)
        self.assertFalse(RegistrationSubmission.objects.exists())
