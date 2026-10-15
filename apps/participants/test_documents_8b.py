from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.participants.models import Participant
from apps.participants.participant_document_models import (
    ParticipantDocument, ParticipantDocumentAudit, ParticipantDocumentCategory,
    QualificationDocumentLink,
)
from apps.participants.qualification_models import ParticipantQualification


MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'


@override_settings(PRIVATE_DOCUMENT_ROOT='/tmp/btw-test-private-documents')
class ParticipantDocumentTests(TestCase):
    def setUp(self):
        self.participant = Participant.objects.create(first_name='Jordan', last_name='Test')
        self.category = ParticipantDocumentCategory.objects.create(name='License', slug='license')
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username='document-staff', password='test-password', is_staff=True
        )
        codenames = (
            'view_participant', 'view_participantdocument',
            'manage_participant_documents', 'manage_participant_document_categories',
            'manage_qualification_document_links',
        )
        permissions = Permission.objects.filter(
            content_type__app_label='participants', codename__in=codenames
        )
        self.staff.user_permissions.add(*permissions)
        self.client.force_login(self.staff, backend=MODEL_BACKEND)

    def upload(self, name='license.pdf', content=b'%PDF-1.4 synthetic fixture'):
        data = {
            'category': self.category.pk,
            'audit_reason': 'Verified test upload',
            'file': SimpleUploadedFile(name, content, content_type='application/pdf'),
        }
        return self.client.post(
            reverse('participants:staff-participant-documents', args=[self.participant.pk]), data
        )

    def test_authorized_upload_is_private_audited_and_download_permission_checked(self):
        response = self.upload()
        self.assertRedirects(
            response, reverse('participants:staff-participant-documents', args=[self.participant.pk]),
            fetch_redirect_response=False,
        )
        document = ParticipantDocument.objects.get(participant=self.participant)
        self.assertTrue(document.file.name)
        self.assertTrue(ParticipantDocumentAudit.objects.filter(
            document=document, action='uploaded', actor=self.staff
        ).exists())
        download = self.client.get(
            reverse('participants:staff-document-download', args=[document.uuid])
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download['X-Content-Type-Options'], 'nosniff')
        self.assertIn('no-store', download['Cache-Control'])
        self.assertTrue(ParticipantDocumentAudit.objects.filter(
            document=document, action='downloaded'
        ).exists())
        with self.assertRaises(ValueError):
            document.file.url

    def test_staff_without_document_permission_cannot_upload_or_view(self):
        user = get_user_model().objects.create_user(
            username='no-document-perms', password='test-password', is_staff=True
        )
        self.client.force_login(user, backend=MODEL_BACKEND)
        directory = self.client.get(reverse('participants:staff-document-directory'))
        self.assertEqual(directory.status_code, 403)
        upload = self.upload()
        self.assertEqual(upload.status_code, 403)
        self.assertFalse(ParticipantDocument.objects.exists())

    def test_invalid_file_signature_is_rejected_without_record(self):
        response = self.upload(name='fake.pdf', content=b'<html>not a PDF</html>')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ParticipantDocument.objects.exists())

    def test_archive_requires_reason_and_preserves_record_and_file(self):
        self.upload()
        document = ParticipantDocument.objects.get(participant=self.participant)
        url = reverse('participants:staff-document-archive', args=[document.uuid])
        self.client.post(url, {})
        document.refresh_from_db()
        self.assertEqual(document.state, ParticipantDocument.State.ACTIVE)
        self.client.post(url, {'audit_reason': 'Expired credential'})
        document.refresh_from_db()
        self.assertEqual(document.state, ParticipantDocument.State.ARCHIVED)
        self.assertTrue(document.file.storage.exists(document.file.name))
        self.assertTrue(ParticipantDocumentAudit.objects.filter(
            document=document, action='archived', reason='Expired credential'
        ).exists())

    def test_replacement_retains_original_and_records_both_versions(self):
        self.upload()
        original = ParticipantDocument.objects.get(participant=self.participant)
        replacement = SimpleUploadedFile('renewed.pdf', b'%PDF-1.7 replacement', content_type='application/pdf')
        response = self.client.post(
            reverse('participants:staff-document-replace', args=[original.uuid]),
            {'category': self.category.pk, 'audit_reason': 'Renewed license', 'file': replacement},
        )
        self.assertEqual(response.status_code, 302)
        original.refresh_from_db()
        self.assertEqual(original.state, ParticipantDocument.State.REPLACED)
        self.assertTrue(original.file.storage.exists(original.file.name))
        new_document = ParticipantDocument.objects.get(replaces=original)
        self.assertEqual(new_document.state, ParticipantDocument.State.ACTIVE)
        self.assertEqual(ParticipantDocumentAudit.objects.filter(
            document=original, action='replaced'
        ).count(), 1)

    def test_expiration_and_cross_participant_qualification_link_are_rejected(self):
        self.upload()
        document = ParticipantDocument.objects.get(participant=self.participant)
        other = Participant.objects.create(first_name='Other', last_name='Person')
        qualification = ParticipantQualification.objects.create(participant=other, custom_name='Electrical')
        link = QualificationDocumentLink(document=document, qualification=qualification, reason='test')
        with self.assertRaises(ValidationError):
            link.full_clean()
        document.issue_date = date.today()
        document.expiration_date = date.today() - timedelta(days=1)
        with self.assertRaises(ValidationError):
            document.full_clean()

    def test_expiration_equal_to_issue_date_is_valid(self):
        self.upload()
        document = ParticipantDocument.objects.get(participant=self.participant)
        document.issue_date = date.today()
        document.expiration_date = date.today()
        document.full_clean()


    def tearDown(self):
        for document in ParticipantDocument.objects.all():
            if document.file and document.file.name:
                document.file.storage.delete(document.file.name)
        super().tearDown()
