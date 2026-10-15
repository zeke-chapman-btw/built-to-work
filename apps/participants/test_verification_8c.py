import uuid
from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.participants.models import Participant
from .qualification_models import QualificationCategory, QualificationCatalogEntry, ParticipantQualification
from .participant_document_models import ParticipantDocument, ParticipantDocumentCategory
from .verification_models import ParticipantVerificationRecord
from .verification_services import document_verification_summary, qualification_verification_summary, qualification_snapshot


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, AUTHENTICATION_BACKENDS=("django.contrib.auth.backends.ModelBackend",))
class ParticipantVerificationWorkflowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="approval-admin", email="admin@example.test", password="valid-password")
        self.staff = User.objects.create_user(username="ordinary-staff", password="valid-password", is_staff=True, is_active=True)
        self.participant = Participant.objects.create(first_name="Jordan", last_name="Worker")
        self.other = Participant.objects.create(first_name="Casey", last_name="Other")
        self.category = QualificationCategory.objects.create(name="Trades", slug="trades")
        self.catalog = QualificationCatalogEntry.objects.create(kind="skill", name="Electrical", category=self.category, description="Electrical work")
        self.qualification = ParticipantQualification.objects.create(participant=self.participant, catalog_entry=self.catalog, source="other", is_self_reported=True)
        self.document_category = ParticipantDocumentCategory.objects.create(name="License", slug="license")

    def make_document(self, participant=None, expires=None):
        payload = b"%PDF-1.4\nsynthetic fixture\n%%EOF"
        return ParticipantDocument.objects.create(
            participant=participant or self.participant, category=self.document_category, original_filename="license.pdf",
            file=SimpleUploadedFile("license.pdf", payload, content_type="application/pdf"), content_type="application/pdf",
            file_size=len(payload), issue_date=date.today(), expiration_date=expires, uploaded_by=self.admin, upload_source="staff",
        )

    def approve_permission(self, user=None, *, view=True, review=True):
        user = user or self.staff
        if view:
            user.user_permissions.add(Permission.objects.get(codename="view_qualificationapprovals"))
        if review:
            user.user_permissions.add(Permission.objects.get(codename="review_qualificationapprovals"))
        return user

    def test_only_authorized_staff_can_see_queue_or_submit_decisions(self):
        detail = reverse("participants:staff-qualification-approval-detail", args=("qualification", self.qualification.pk))
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("participants:staff-qualification-approvals")).status_code, 403)
        self.assertEqual(self.client.post(detail, {"status": "verified", "method": "staff_confirmed", "reason": "checked", "evidence_notes": "observed", "request_key": str(uuid.uuid4())}).status_code, 403)
        self.approve_permission(self.staff, review=False)
        response = self.client.get(reverse("participants:staff-qualification-approvals"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Approvals")
        self.assertFalse(response.context["can_decide"])

    def test_valid_skill_assessment_is_recorded_idempotently_and_displayed(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        detail = reverse("participants:staff-qualification-approval-detail", args=("qualification", self.qualification.pk))
        get = self.client.get(detail)
        key = str(get.context["form"].initial["request_key"])
        data = {"request_key": key, "status": "verified", "method": "skill_assessed", "reason": "Passed supervised bench assessment.", "evidence_notes": "Observed safe wiring and meter use."}
        self.assertEqual(self.client.post(detail, data).status_code, 302)
        self.assertEqual(self.client.post(detail, data).status_code, 302)
        self.assertEqual(ParticipantVerificationRecord.objects.filter(qualification=self.qualification).count(), 1)
        summary = qualification_verification_summary(self.qualification)
        self.assertEqual(summary["status"], "verified")
        self.assertEqual(summary["method"], "Skill-assessed")
        response = self.client.get(detail)
        self.assertContains(response, "Decision history")
        self.assertContains(response, "Passed supervised bench assessment")

    def test_rejection_requires_reason(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        detail = reverse("participants:staff-qualification-approval-detail", args=("qualification", self.qualification.pk))
        key = str(self.client.get(detail).context["form"].initial["request_key"])
        response = self.client.post(detail, {"request_key": key, "status": "rejected", "method": "staff_confirmed", "reason": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ParticipantVerificationRecord.objects.exists())
        self.assertContains(response, "reason is required", status_code=400)

    def test_self_reported_qualification_is_not_independently_verified(self):
        summary = qualification_verification_summary(self.qualification)
        self.assertEqual(summary["status"], "self_reported")
        with self.assertRaises(ValidationError):
            record = ParticipantVerificationRecord(participant=self.participant, qualification=self.qualification, status="verified", method="self_reported", reviewer=self.admin, reviewed_at=timezone.now(), qualification_snapshot=qualification_snapshot(self.qualification))
            record.full_clean()

    def test_document_upload_is_pending_until_review_and_history_is_preserved(self):
        document = self.make_document()
        self.assertEqual(document_verification_summary(document)["status"], "pending")
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        detail = reverse("participants:staff-qualification-approval-detail", args=("document", document.pk))
        key = str(self.client.get(detail).context["form"].initial["request_key"])
        response = self.client.post(detail, {"request_key": key, "status": "verified", "method": "document_verified", "reason": "License number and holder checked against original."})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(document_verification_summary(document)["status"], "verified")
        record = ParticipantVerificationRecord.objects.get(document=document)
        self.assertEqual(record.reviewer, self.admin)
        self.assertIsNotNone(record.reviewed_at)

    def test_evidence_from_another_participant_is_rejected(self):
        foreign = self.make_document(participant=self.other)
        record = ParticipantVerificationRecord(participant=self.participant, qualification=self.qualification, evidence_document=foreign, status="verified", method="document_verified", reviewer=self.admin, reviewed_at=timezone.now(), reason="reviewed", qualification_snapshot=qualification_snapshot(self.qualification))
        with self.assertRaises(ValidationError):
            record.full_clean()

    def test_expired_document_cannot_be_verified_or_appear_current(self):
        document = self.make_document(expires=date.today() - timedelta(days=1))
        self.assertEqual(document_verification_summary(document)["status"], "expired")
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        detail = reverse("participants:staff-qualification-approval-detail", args=("document", document.pk))
        response = self.client.post(detail, {"request_key": str(uuid.uuid4()), "status": "verified", "method": "document_verified", "reason": "reviewed"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ParticipantVerificationRecord.objects.filter(document=document).exists())

    def test_material_qualification_change_requires_fresh_review(self):
        record = ParticipantVerificationRecord.objects.create(participant=self.participant, qualification=self.qualification, status="verified", method="skill_assessed", evidence_notes="Observed skill assessment.", reason="Passed assessment.", reviewer=self.admin, reviewed_at=timezone.now(), qualification_snapshot=qualification_snapshot(self.qualification))
        self.qualification.notes = "Materially updated claim details"
        self.qualification.save(update_fields=["notes", "updated_at"])
        self.assertEqual(qualification_verification_summary(self.qualification)["status"], "pending")
        self.assertTrue(ParticipantVerificationRecord.objects.filter(pk=record.pk).exists())

    def test_replaced_evidence_does_not_remain_valid_for_qualification(self):
        document = self.make_document()
        record = ParticipantVerificationRecord.objects.create(participant=self.participant, qualification=self.qualification, evidence_document=document, status="verified", method="document_verified", reason="Matched certificate.", reviewer=self.admin, reviewed_at=timezone.now(), qualification_snapshot=qualification_snapshot(self.qualification))
        self.assertEqual(qualification_verification_summary(self.qualification)["status"], "verified")
        document.state = ParticipantDocument.State.REPLACED
        document.save(update_fields=["state", "updated_at"])
        self.assertEqual(qualification_verification_summary(self.qualification)["status"], "pending")
        self.assertTrue(ParticipantVerificationRecord.objects.filter(pk=record.pk).exists())

    def tearDown(self):
        for document in ParticipantDocument.objects.all():
            if document.file and document.file.name:
                document.file.storage.delete(document.file.name)
        super().tearDown()
