"""Synthetic-PDF tests for the BTW administrator consent workflow."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from apps.core.models import AuditLog
from .models import ConsentDocumentVersion


def synthetic_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'),
    })
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})
    })
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 72 720 Td (Synthetic consent terms for tests only.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class ConsentDocumentWorkflowTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        media_settings = override_settings(PRIVATE_CONSENT_ROOT=self.media.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.admin = get_user_model().objects.create_superuser(
            username='consent-admin', email='consent-admin@example.test', password='test-password'
        )
        self.upload_url = reverse('admin:participants_consentdocumentversion_upload')

    def upload(self, contents=None):
        return self.client.post(self.upload_url, {
            'key': 'btw-intake', 'version': 'test-v1', 'title': 'Synthetic test document',
            'original_pdf': SimpleUploadedFile('synthetic.pdf', contents or synthetic_pdf(), content_type='application/pdf'),
        })

    def url(self, action, document):
        return reverse('admin:participants_consentdocumentversion_' + action, args=[document.pk])

    def test_upload_review_verify_publish_preserves_original_and_audit(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.upload()
        self.assertEqual(response.status_code, 302)
        document = ConsentDocumentVersion.objects.get()
        self.assertFalse(document.is_approved)
        self.assertTrue(Path(document.original_pdf.path).is_relative_to(Path(self.media.name)))
        with self.assertRaises(ValueError):
            _ = document.original_pdf.url
        self.assertIn('Synthetic consent terms', document.body)
        self.assertEqual(len(document.content_hash), 64)
        self.assertEqual(self.client.get(self.url('pdf', document)).status_code, 200)
        self.assertEqual(self.client.post(self.url('publish', document), {'confirm_publish': 'on'}).status_code, 400)
        review = self.url('review', document)
        self.assertEqual(self.client.post(review, {'action': 'save', 'body': 'Corrected synthetic terms.'}).status_code, 302)
        self.assertEqual(self.client.post(review, {'action': 'verify', 'checked_against_pdf': 'on'}).status_code, 400)
        preview = self.client.get(self.url('preview', document))
        self.assertContains(preview, 'Corrected synthetic terms.')
        self.assertEqual(self.client.post(review, {'action': 'verify', 'checked_against_pdf': 'on'}).status_code, 302)
        self.assertEqual(self.client.post(self.url('publish', document), {'confirm_publish': 'on'}).status_code, 302)
        document.refresh_from_db()
        self.assertTrue(document.is_approved)
        self.assertEqual(document.published_by, self.admin)
        self.assertEqual(document.text_verified_by, self.admin)
        self.assertIsNotNone(document.published_at)
        self.assertEqual(document.version, 'test-v1')
        self.assertEqual(self.client.post(review, {'action': 'save', 'body': 'new'}).status_code, 400)
        self.assertTrue(AuditLog.objects.filter(action='consent.document.published', object_id=str(document.pk)).exists())

    def test_malformed_pdf_is_rejected(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.upload(b'%PDF-1.7\nnot a valid PDF')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ConsentDocumentVersion.objects.count(), 0)

    def test_non_admin_cannot_upload_review_or_publish(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.upload()
        document = ConsentDocumentVersion.objects.get()
        staff = get_user_model().objects.create_user(username='staff-only', password='test-password', is_staff=True)
        self.client.force_login(staff, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.client.post(self.url('review', document), {'action': 'save', 'body': 'evil'}).status_code, 403)
        self.assertEqual(self.client.post(self.url('publish', document), {'confirm_publish': 'on'}).status_code, 403)
        self.assertEqual(self.client.get(self.url('pdf', document)).status_code, 403)
        document.refresh_from_db()
        self.assertFalse(document.is_approved)

    def test_draft_does_not_open_public_registration(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.upload()
        self.client.logout()
        response = self.client.get(reverse('participants:registration-form'))
        self.assertEqual(response.status_code, 503)
