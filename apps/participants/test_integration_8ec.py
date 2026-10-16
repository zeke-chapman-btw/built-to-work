import json
import tempfile
import uuid
from datetime import date, timedelta
from pathlib import Path
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from .models import Participant
from .participant_document_models import ParticipantDocument, ParticipantDocumentCategory, QualificationDocumentLink
from .participant_document_storage import PrivateParticipantDocumentStorage
from .participant_review_models import ParticipantReviewRecord
from .qualification_models import ParticipantQualification
from .verification_models import ParticipantVerificationRecord
from .verification_services import qualification_snapshot, qualification_verification_summary


class ParticipantIntegration8ECTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser('synthetic-reviewer', 'reviewer@example.test', 'test-password')
        self.person = Participant.objects.create(first_name='SYNTHETIC Taylor', last_name='Example', contact_email='taylor@example.test')
        self.other = Participant.objects.create(first_name='SYNTHETIC Avery', last_name='Example')
        self.client.force_login(self.actor, backend='django.contrib.auth.backends.ModelBackend')
        self.qual_url = reverse('participants:staff-participant-qualifications', args=[self.person.pk])
        self.profile_url = reverse('participants:staff-profile', args=[self.person.pk])
        self.docs_url = reverse('participants:staff-participant-documents', args=[self.person.pk])
        self.review_url = reverse('participants:staff-participant-review', args=[self.person.pk])
        self.category = ParticipantDocumentCategory.objects.get(slug='license-cdl')
        self.directory = tempfile.TemporaryDirectory()
        self.file_field = ParticipantDocument._meta.get_field('file')
        self.original_storage = self.file_field.storage
        self.file_field.storage = PrivateParticipantDocumentStorage(location=self.directory.name)
        self.addCleanup(setattr, self.file_field, 'storage', self.original_storage)
        self.addCleanup(self.directory.cleanup)

    def staff(self, name, *codenames, is_staff=True):
        user = get_user_model().objects.create_user(name, password='test-password', is_staff=is_staff)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='participants', codename__in=codenames))
        return user

    def login(self, user):
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')

    def qualification(self):
        return ParticipantQualification.objects.create(participant=self.person, custom_name='SYNTHETIC CDL Class A / tanker endorsement', years_of_experience='4.5', experience_details='Fictional equipment and trade experience only', is_self_reported=True, expiration_date=date.today()+timedelta(days=30))

    def decision(self, qualification, status='verified', **extra):
        data = {'request_key': str(uuid.uuid4()), 'status': status, 'method': 'staff_confirmed', 'reason': 'SYNTHETIC development review', 'evidence_notes': 'Fictional test evidence; no real credential verified'}
        data.update(extra)
        return self.client.post(reverse('participants:staff-qualification-approval-detail', args=['qualification',qualification.pk]), data)

    def upload(self, **extra):
        data = {'category': self.category.pk, 'audit_reason': 'SYNTHETIC upload test', 'description': 'SYNTHETIC — not a real license', 'file': SimpleUploadedFile('SYNTHETIC-test-license.pdf', b'%PDF-1.4 synthetic test fixture', content_type='application/pdf')}
        data.update(extra)
        return self.client.post(self.docs_url, data)

    def review_data(self, **extra):
        prior = self.person.internal_reviews.first()
        data = {'request_key': str(uuid.uuid4()), 'previous_record': prior.pk if prior else '', 'status': 'pending', 'internal_notes': 'SYNTHETIC internal review notes'}
        data.update(extra)
        return data

    def test_qualification_create_edit_dates_and_profile_integration(self):
        response = self.client.post(self.qual_url, {'custom_name': 'SYNTHETIC equipment training', 'years_of_experience': '2.5', 'experience_details': 'Excavator / welding / training example', 'issue_date':'2025-01-01', 'expiration_date':'2030-01-01', 'is_self_reported':'on', 'audit_reason':'SYNTHETIC staff entry'})
        self.assertRedirects(response, self.qual_url)
        qualification = self.person.qualifications.get()
        self.assertEqual(qualification.source, 'staff')
        self.assertEqual(qualification.created_by, self.actor)
        self.assertEqual(qualification_verification_summary(qualification)['status'], 'unverified')
        response = self.client.get(self.profile_url)
        self.assertContains(response, qualification.custom_name)
        self.assertContains(response, 'Self-reported')
        self.assertContains(response, 'Manage qualifications')
        edit = reverse('participants:staff-participant-qualification-edit', args=[self.person.pk, qualification.pk])
        self.assertRedirects(self.client.post(edit, {'custom_name':qualification.custom_name,'experience_details':'Updated fictional experience','years_of_experience':'3.0','issue_date':'2025-01-01','expiration_date':'2031-01-01','is_self_reported':'on','audit_reason':'SYNTHETIC edit'}), self.qual_url)
        qualification.refresh_from_db()
        self.assertEqual(qualification.expiration_date, date(2031,1,1))

    def test_invalid_dates_and_unauthorized_qualification_edits_rejected(self):
        response = self.client.post(self.qual_url, {'custom_name':'Fictional training','issue_date':'2030-01-01','expiration_date':'2029-01-01','audit_reason':'test'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.person.qualifications.exists())
        reader = self.staff('qualification-reader','view_participant','view_participantqualification')
        self.login(reader)
        self.assertEqual(self.client.get(self.qual_url).status_code, 200)
        self.assertEqual(self.client.post(self.qual_url, {'custom_name':'No permission','audit_reason':'test'}).status_code, 403)

    def test_verification_states_are_attributed_and_json_safe(self):
        qualification = self.qualification()
        json.dumps(qualification_snapshot(qualification))
        prior = None
        for status in ('pending','verified','rejected','unverified'):
            self.assertEqual(self.decision(qualification,status).status_code, 302)
            record = ParticipantVerificationRecord.objects.filter(qualification=qualification).first()
            self.assertEqual(record.reviewer, self.actor)
            self.assertIsNotNone(record.reviewed_at)
            self.assertEqual(record.supersedes, prior)
            self.assertEqual(qualification_verification_summary(qualification)['status'],status)
            prior = record
        self.assertFalse(self.person.internal_reviews.exists())

    def test_material_change_and_expiration_do_not_keep_verified_status(self):
        qualification = self.qualification()
        self.assertEqual(self.decision(qualification).status_code,302)
        qualification.experience_details = 'Changed synthetic equipment details'
        qualification.save()
        self.assertEqual(qualification_verification_summary(qualification)['status'],'pending')
        qualification.expiration_date = date.today()-timedelta(days=1)
        qualification.save()
        self.assertEqual(qualification_verification_summary(qualification)['status'],'expired')
        response = self.decision(qualification)
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'Expired qualifications',status_code=400)
        self.assertEqual(qualification.verification_records.count(),1)

    def test_document_upload_page_download_and_qualification_links(self):
        qualification = self.qualification()
        self.assertRedirects(self.upload(),self.docs_url)
        document = self.person.documents.get()
        self.assertContains(self.client.get(self.docs_url), 'SYNTHETIC-test-license.pdf')
        self.assertContains(self.client.get(self.profile_url), 'Download securely')
        self.assertEqual(Path(document.file.path).stat().st_mode & 0o777,0o600)
        with self.assertRaises(ValueError): document.file.url
        self.assertNotIn(document.original_filename,document.file.name)
        link = reverse('participants:staff-document-qualification-link',args=[document.uuid,0])
        self.assertRedirects(self.client.post(link, {'qualification_id':qualification.pk,'reason':'SYNTHETIC evidence link'}),self.docs_url)
        self.assertTrue(QualificationDocumentLink.objects.filter(document=document,qualification=qualification).exists())
        self.assertContains(self.client.get(self.qual_url),reverse('participants:staff-document-download',args=[document.uuid]))
        self.assertEqual(qualification_verification_summary(qualification)['status'],'unverified')
        self.assertFalse(self.person.internal_reviews.exists())
        download = self.client.get(reverse('participants:staff-document-download',args=[document.uuid]))
        self.assertEqual(download.status_code,200)
        self.assertEqual(download['Content-Type'],'application/octet-stream')
        self.assertIn('attachment',download['Content-Disposition'])
        self.assertIn('no-store',download['Cache-Control'])
        self.assertIn('sandbox',download['Content-Security-Policy'])
        download.close()
        foreign = ParticipantQualification.objects.create(participant=self.other,custom_name='Other fictional record')
        self.assertEqual(self.client.post(link,{'qualification_id':foreign.pk,'reason':'test'}).status_code,404)
        self.assertEqual(self.client.post(link,{'qualification_id':'bad','reason':'test'}).status_code,404)

    @override_settings(PARTICIPANT_DOCUMENT_MAX_UPLOAD_BYTES=16)
    def test_bad_type_signature_and_oversize_uploads_rejected(self):
        for name,content in [('synthetic.exe',b'%PDF-1.4'),('synthetic.pdf',b'<html>bad</html>'),('synthetic.pdf',b'%PDF-'+b'x'*20)]:
            self.upload(file=SimpleUploadedFile(name,content))
            self.assertFalse(self.person.documents.exists())

    def test_private_document_denied_for_station_and_nonstaff_users(self):
        self.upload()
        document=self.person.documents.get()
        download=reverse('participants:staff-document-download',args=[document.uuid])
        for user in (self.staff('station-only'),self.staff('nonstaff-with-permissions','view_participant','view_participantdocument','review_participants',is_staff=False)):
            self.login(user)
            for url in (self.profile_url,self.docs_url,download,self.review_url,self.qual_url):
                self.assertEqual(self.client.get(url).status_code,403,url)
            self.assertEqual(self.client.post(self.review_url,self.review_data(status='approved')).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(download).status_code,302)

    def test_document_reader_sees_no_management_controls(self):
        self.upload()
        reader=self.staff('document-reader','view_participant','view_participantdocument')
        self.login(reader)
        response=self.client.get(self.docs_url)
        self.assertContains(response,'Download securely')
        for text in ('Upload supporting document','Edit details','Link qualification','Archive reason','Audit history'):
            self.assertNotContains(response,text)
        self.assertEqual(self.upload().status_code,403)

    def test_participant_review_history_attribution_and_idempotency(self):
        self.assertContains(self.client.get(self.profile_url),'Not reviewed')
        data=self.review_data(status='approved',reviewer='someone-else',reviewed_at='2001-01-01')
        self.assertRedirects(self.client.post(self.review_url,data),self.profile_url)
        review=self.person.internal_reviews.get()
        self.assertEqual(review.reviewer,self.actor)
        self.assertGreater(review.reviewed_at.year,2001)
        self.assertRedirects(self.client.post(self.review_url,data),self.review_url)
        self.assertEqual(self.person.internal_reviews.count(),1)
        self.assertRedirects(self.client.post(self.review_url,self.review_data(status='rejected')),self.profile_url)
        latest=self.person.internal_reviews.first()
        self.assertEqual(latest.supersedes,review)
        self.assertEqual(self.person.internal_reviews.count(),2)
        self.assertFalse(ParticipantVerificationRecord.objects.exists())
        latest.internal_notes='tamper'
        with self.assertRaises(ValidationError): latest.save()
        with self.assertRaises(ValidationError): latest.delete()

    def test_review_requires_notes_and_rejects_stale_or_cross_participant_requests(self):
        self.assertEqual(self.client.post(self.review_url,self.review_data(internal_notes='   ')).status_code,400)
        stale=self.review_data()
        self.client.post(self.review_url,self.review_data())
        self.assertEqual(self.client.post(self.review_url,stale).status_code,400)
        used=self.person.internal_reviews.first()
        other_url=reverse('participants:staff-participant-review',args=[self.other.pk])
        self.assertEqual(self.client.post(other_url,{'request_key':used.request_key,'previous_record':'','status':'pending','internal_notes':used.internal_notes}).status_code,403)
        self.assertFalse(self.other.internal_reviews.exists())

    def test_internal_notes_and_decisions_have_separate_permissions(self):
        self.client.post(self.review_url,self.review_data())
        self.client.logout()
        viewer=self.staff('profile-reader','view_participant','review_qualificationapprovals')
        self.login(viewer)
        response=self.client.get(self.profile_url)
        self.assertNotContains(response,'Internal participant review')
        self.assertNotContains(response,'SYNTHETIC internal review notes')
        self.assertEqual(self.client.get(self.review_url).status_code,403)
        readonly=self.staff('review-reader','view_participant','view_participantreviewrecord')
        self.login(readonly)
        self.assertContains(self.client.get(self.review_url),'SYNTHETIC internal review notes')
        self.assertNotContains(self.client.get(self.review_url),'Save internal review')
        self.assertEqual(self.client.post(self.review_url,self.review_data(status='approved')).status_code,403)
        reviewer=self.staff('participant-reviewer','view_participant','review_participants')
        self.login(reviewer)
        self.assertEqual(self.client.post(self.review_url,self.review_data(status='approved')).status_code,302)
        qualification=self.qualification()
        self.assertEqual(self.decision(qualification).status_code,403)

    def test_csrf_required_for_internal_review(self):
        client=Client(enforce_csrf_checks=True)
        client.force_login(self.actor,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(client.post(self.review_url,self.review_data()).status_code,403)
        self.assertFalse(self.person.internal_reviews.exists())

    def test_document_metadata_audit_preserves_before_and_after(self):
        self.assertEqual(self.upload().status_code,302)
        document=self.person.documents.get()
        original=document.description
        response=self.client.post(reverse("participants:staff-document-edit",args=[document.uuid]),{"category":document.category_id,"description":"SYNTHETIC updated description","audit_reason":"SYNTHETIC metadata correction"})
        self.assertEqual(response.status_code,302)
        event=document.audit_events.get(action="metadata_changed")
        self.assertEqual(event.details["before"]["description"],original)
        self.assertEqual(event.details["after"]["description"],"SYNTHETIC updated description")
