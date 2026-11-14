"""BTW can share work history, references and documents; customers see them and keep their response history."""
import uuid
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.participants.participant_document_models import ParticipantDocument, ParticipantDocumentCategory, ParticipantDocumentAudit
from apps.participants.workspace_models import ParticipantWorkExperience, ParticipantReference
from .test_employer import EmployerFixtures
from . import employer


@override_settings(PRIVATE_DOCUMENT_ROOT='/tmp/btw-test-private-documents')
class MoreSharedTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        person = self.candidate.participant
        ParticipantWorkExperience.objects.create(participant=person, role='SYNTHETIC welder helper', employer='SYNTHETIC shop')
        ParticipantReference.objects.create(participant=person, name='SYNTHETIC reference', role='Foreman', phone='5550100000',
                                            result='concern')
        category = ParticipantDocumentCategory.objects.create(name='SYNTHETIC CP4 files', slug='synthetic-cp4-files')
        self.doc = ParticipantDocument(participant=person, category=category, original_filename='resume.pdf', content_type='application/pdf',
                                       file_size=20, participant_visible=True)
        self.doc.file.save('resume.pdf', ContentFile(b'%PDF-1.4 synthetic'), save=False)
        self.doc.save()
        self.hidden = ParticipantDocument(participant=person, category=category, original_filename='staff-note.pdf', content_type='application/pdf',
                                          file_size=20, participant_visible=False)
        self.hidden.file.save('staff-note.pdf', ContentFile(b'%PDF-1.4 private'), save=False)
        self.hidden.save()

    def login(self):
        self.client.force_login(self.customer, backend='apps.customers.auth.CustomerAuthenticationBackend')
        s = self.client.session; s['customer_organization'] = str(self.organization.pk); s.save()

    def test_first_look_then_more_shared_keeps_responses_and_shows_new_sections(self):
        first = self.release(categories=['identity', 'summary', 'region', 'experience'])
        self.respond(first, kind='interview', message='SYNTHETIC Tuesday works')
        more = self.release(categories=['identity', 'summary', 'region', 'experience', 'work_history', 'references', 'documents'])
        self.assertNotEqual(first.pk, more.pk)
        self.login()
        response = self.client.get(reverse('customer_portal:recruiting-candidate', args=[more.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SYNTHETIC welder helper')
        self.assertContains(response, 'SYNTHETIC reference')
        self.assertNotContains(response, 'Concern')  # the staff reference check stays private
        self.assertContains(response, 'resume.pdf')
        self.assertNotContains(response, 'staff-note.pdf')
        self.assertContains(response, 'SYNTHETIC Tuesday works')  # earlier response kept after re-release
        self.assertContains(response, 'Interview requested')

    def test_released_document_download_is_scoped_and_audited(self):
        grant = self.release(categories=['identity', 'documents'])
        self.login()
        url = reverse('customer_portal:recruiting-candidate-document', args=[grant.pk, self.doc.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4 synthetic')
        self.assertTrue(ParticipantDocumentAudit.objects.filter(document=self.doc, action='employer_view').exists())
        hidden = reverse('customer_portal:recruiting-candidate-document', args=[grant.pk, self.hidden.uuid])
        self.assertEqual(self.client.get(hidden).status_code, 403)
        other = reverse('customer_portal:recruiting-candidate-document', args=[grant.pk, uuid.uuid4()])
        self.assertEqual(self.client.get(other).status_code, 403)

    def test_documents_not_released_cannot_be_downloaded(self):
        grant = self.release(categories=['identity', 'summary'])
        self.login()
        url = reverse('customer_portal:recruiting-candidate-document', args=[grant.pk, self.doc.uuid])
        self.assertEqual(self.client.get(url).status_code, 403)
