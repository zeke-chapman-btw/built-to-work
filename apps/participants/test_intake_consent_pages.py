"""Five-page intake and Operations consent workspace regressions (synthetic data only)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase, Client
from django.urls import reverse

from apps.core.models import AuditLog
from apps.internal_ui.navigation import visible_workspace_links, visible_workspaces
from .models import IntakeSession, RegistrationSubmission, ConsentAcceptance
from .intake_wizard import session_revision, current_ui_step
from .intake_submission import complete_session
from .test_intake_ui_11b import IntakeUI11BTests
from .test_consent_workflow import ConsentDocumentWorkflowTests


class FivePageIntakeTests(TestCase):
    setUp = IntakeUI11BTests.setUp
    answers = IntakeUI11BTests.answers
    begin = IntakeUI11BTests.begin
    step = IntakeUI11BTests.step

    def test_personal_save_does_not_accept_consent_or_create_participant(self):
        session, url = self.begin()
        self.assertNotContains(self.client.get(url), self.document.body)
        self.assertEqual(self.step(session, url, 1).status_code, 302)
        session.refresh_from_db()
        self.assertIsNone(session.consent_accepted_at)
        self.assertNotIn("consent", session.answers)
        self.assertEqual(current_ui_step(session), 2)
        self.assertEqual(RegistrationSubmission.objects.count(), 0)
        consent = self.client.get(url)
        self.assertContains(consent, "Step 2 of 5")
        self.assertContains(consent, self.document.body)
        self.assertContains(consent, 'name="consent"')
        self.assertNotContains(consent, 'name="first_name"')
        self.assertNotContains(consent, 'name="current_situation"')
        self.assertNotContains(consent, 'checked')
        self.assertEqual(self.client.get(url + "?step=3").status_code, 404)
        self.assertEqual(self.client.post(url + "?step=3", {}).status_code, 404)

    def test_unchecked_consent_blocks_advancing_and_records_nothing(self):
        session, url = self.begin()
        self.step(session, url, 1)
        response = self.step(session, url, 2, {"consent": False})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please check your answers")
        session.refresh_from_db()
        self.assertIsNone(session.consent_accepted_at)
        self.assertEqual(current_ui_step(session), 2)
        self.assertFalse(ConsentAcceptance.objects.exists())
        self.assertFalse(RegistrationSubmission.objects.exists())

    def test_personal_post_cannot_smuggle_acceptance_or_later_answers(self):
        session, url = self.begin()
        values = self.answers()
        values["revision"] = session_revision(session)
        self.assertEqual(self.client.post(url, values).status_code, 302)
        session.refresh_from_db()
        self.assertIsNone(session.consent_accepted_at)
        self.assertNotIn("consent", session.answers)
        self.assertNotIn("career_timeline", session.answers)
        with self.assertRaises(ValidationError):
            complete_session(session_id=session.pk)

    def test_consent_acceptance_is_pinned_and_recorded_at_step_two(self):
        session, url = self.begin()
        self.step(session, url, 1)
        self.assertEqual(self.step(session, url, 2).status_code, 302)
        session.refresh_from_db()
        accepted_at = session.consent_accepted_at
        self.assertIsNotNone(accepted_at)
        self.assertEqual(current_ui_step(session), 3)
        for number in (3, 4, 5):
            self.assertEqual(self.step(session, url, number).status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.state, "submitted")
        self.assertEqual(session.consent_accepted_at, accepted_at)
        acceptance = ConsentAcceptance.objects.get()
        self.assertEqual(acceptance.document_id, self.document.pk)
        self.assertEqual(RegistrationSubmission.objects.count(), 1)

    def test_editing_personal_information_requires_consent_again_and_clears_later_answers(self):
        session, url = self.begin()
        for number in (1, 2, 3):
            self.step(session, url, number)
        self.assertEqual(self.step(session, url, 1, {"city": "SYNTHETIC changed city"}).status_code, 302)
        session.refresh_from_db()
        self.assertEqual(current_ui_step(session), 2)
        self.assertIsNone(session.consent_accepted_at)
        self.assertNotIn("consent", session.answers)
        self.assertNotIn("current_situation", session.answers)
        self.assertEqual(self.client.get(url + "?step=5").status_code, 404)

    def test_changed_consent_document_cannot_be_accepted_silently(self):
        session, url = self.begin()
        self.step(session, url, 1)
        self.document.body = "SYNTHETIC changed legal text"
        self.document.save()
        response = self.step(session, url, 2)
        self.assertContains(response, "The consent changed")
        session.refresh_from_db()
        self.assertIsNone(session.consent_accepted_at)
        self.assertEqual(current_ui_step(session), 2)

    def test_stale_revision_cannot_save_personal_or_consent_twice(self):
        session, url = self.begin()
        old_revision = session_revision(session)
        self.step(session, url, 1)
        response = self.client.post(url + "?step=1", {**self.answers(), "revision": old_revision})
        self.assertContains(response, "This intake changed")
        session.refresh_from_db()
        self.assertIsNone(session.consent_accepted_at)
        response = self.client.post(url + "?step=2", {"consent": "on", "revision": old_revision})
        self.assertContains(response, "This intake changed")
        session.refresh_from_db()
        self.assertIsNone(session.consent_accepted_at)

    def test_existing_accepted_session_resumes_without_rewriting_its_contract(self):
        session, url = self.begin()
        from .intake_wizard import save_step
        personal = {q["key"]: self.answers().get(q["key"]) for q in self.version.snapshot["questions"] if q["step"] == 1}
        session = save_step(session=session, step=1, values=personal, expected_revision=session_revision(session))
        accepted_at, contract_hash = session.consent_accepted_at, session.contract_hash
        self.assertContains(self.client.get(url), "Step 3 of 5")
        for number in (3, 4, 5):
            self.assertEqual(self.step(session, url, number).status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.consent_accepted_at, accepted_at)
        self.assertEqual(session.contract_hash, contract_hash)


    def test_form_builder_uses_five_participant_step_labels_without_changing_contract(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("participants:intake-config-detail", args=[self.version.form_id]))
        self.assertContains(response, "Preview five steps")
        from .intake_config_forms import QuestionConfigForm
        consent = QuestionConfigForm(contract=self.version.snapshot, key="consent")
        self.assertEqual(list(consent.fields["step"].choices), [(1, "Step 2 — Consent")])
        question = QuestionConfigForm(contract=self.version.snapshot, key="first_name")
        self.assertIn((4, "Step 5 — Career preferences"), list(question.fields["step"].choices))
        self.assertEqual(next(q["step"] for q in self.version.snapshot["questions"] if q["key"] == "consent"), 1)

    def test_event_ticket_questions_remain_on_final_page_and_create_one_registration(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.events.models import Event, EventRegistration, QrTicket, Attendance
        from .intake_lifecycle import assign
        event = Event.objects.create(name="SYNTHETIC Five Page Event", status="upcoming",
            start_at=timezone.now()+timedelta(days=1), end_at=timezone.now()+timedelta(days=2),
            public_registration_status="open", public_consent_document=self.document)
        assignment = assign(actor=self.admin, version_id=self.version.pk, channel="event", event=event)
        response = self.client.post(reverse("participants:intake-start", args=[assignment.pk]))
        session = IntakeSession.objects.latest("created_at")
        url = response.url
        for number in (1, 2, 3, 4):
            self.assertNotContains(self.client.get(url), 'name="ticket_email"')
            self.assertEqual(self.step(session, url, number).status_code, 302)
        self.assertContains(self.client.get(url), "Step 5 of 5")
        self.assertContains(self.client.get(url), 'name="ticket_email"')
        self.assertEqual(self.step(session, url, 5).status_code, 302)
        self.assertEqual(EventRegistration.objects.filter(event=event).count(), 1)
        self.assertEqual(QrTicket.objects.filter(registration__event=event, is_current=True).count(), 1)
        self.assertEqual(Attendance.objects.count(), 0)
        self.client.post(url, {})
        self.assertEqual(EventRegistration.objects.filter(event=event).count(), 1)


class OperationsConsentTests(ConsentDocumentWorkflowTests):
    def setUp(self):
        super().setUp()
        self.upload_url = reverse("participants:consent-upload")

    def url(self, action, document):
        return reverse("participants:consent-" + action, args=[document.pk])

    def test_operations_navigation_and_legacy_admin_links_use_one_home(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        links = visible_workspace_links(self.admin, "operations")
        self.assertEqual(len([item for item in links if item["label"] == "Consents"]), 1)
        self.assertEqual(next(item for item in links if item["label"] == "Consents")["url"], reverse("participants:consent-index"))
        self.assertRedirects(self.client.get(reverse("admin:participants_consentdocumentversion_changelist")), reverse("participants:consent-index"))
        self.assertRedirects(self.client.get(reverse("admin:participants_consentacceptance_changelist")), reverse("participants:consent-acceptances"))
        navigation = visible_workspaces(self.admin, reverse("participants:consent-index"))
        self.assertEqual([item["id"] for item in navigation if item["active"]], ["operations"])
        self.assertContains(self.client.get(reverse("participants:consent-index")), "Consent documents")
        home = self.client.get(reverse("internal:workspace", args=["operations"]))
        self.assertContains(home, 'aria-label="Operations sections"')
        self.assertContains(home, 'href="' + reverse("participants:consent-index") + '"')

    def test_history_is_scoped_read_only_and_original_pdf_is_private(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.upload()
        from .models import ConsentDocumentVersion
        document = ConsentDocumentVersion.objects.get()
        content_type = ContentType.objects.get_for_model(document, for_concrete_model=False)
        AuditLog.objects.create(actor=self.admin, content_type=content_type, object_id="999999", action="SYNTHETIC.UNRELATED")
        history = self.client.get(self.url("history", document))
        self.assertContains(history, "consent.document.uploaded")
        self.assertNotContains(history, "SYNTHETIC.UNRELATED")
        self.assertEqual(self.client.post(self.url("history", document), {}).status_code, 405)
        self.assertEqual(self.client.post(reverse("participants:consent-acceptances"), {}).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(self.url("pdf", document)).status_code, 302)
        self.assertEqual(self.client.get(self.url("history", document)).status_code, 302)

    def test_acceptance_read_permission_does_not_grant_document_management(self):
        user = get_user_model().objects.create_user(username="synthetic-consent-viewer", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_consentacceptance"))
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        self.assertRedirects(self.client.get(reverse("participants:consent-index")), reverse("participants:consent-acceptances"))
        self.assertEqual(self.client.get(self.upload_url).status_code, 403)
        self.assertEqual(self.client.get(reverse("participants:consent-acceptances")).status_code, 200)

    def test_canonical_mutations_require_csrf(self):
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(secure.post(self.upload_url, {}).status_code, 403)

    def test_all_management_screens_use_the_operations_home_and_branded_preview(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.upload()
        from .models import ConsentDocumentVersion
        document = ConsentDocumentVersion.objects.get()
        self.assertTemplateUsed(self.client.get(self.upload_url), "participants/consents/upload.html")
        self.assertTemplateUsed(self.client.get(self.url("review", document)), "participants/consents/review.html")
        preview = self.client.get(self.url("preview", document))
        self.assertTemplateUsed(preview, "participants/consents/preview.html")
        self.assertContains(preview, "Step 2 of 5")
        self.assertEqual(self.client.get(reverse("participants:consent-acceptances"), {"document": "bad-id"}).status_code, 404)
