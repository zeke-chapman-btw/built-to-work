"""Read-first UX, additive history and participant/staff boundaries."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.db import connection, close_old_connections
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from .models import (Participant, ParticipantAccount, ParticipantCareerProfile, ParticipantQualification,
                     ParticipantVerificationRecord, ParticipantDocument, ParticipantDocumentCategory, QualificationDocumentLink)
from .workspace_models import ParticipantWorkExperience, ParticipantNote
from .portal_profile_services import save_entry, archive_entry
from .verification_services import qualification_snapshot, qualification_verification_summary


class PortalProfileExpansionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('synthetic-profile-person')
        self.person = Participant.objects.create(first_name='SYNTHETIC', last_name='Profile Review', date_of_birth=date(1995, 1, 1))
        self.account = ParticipantAccount.objects.create(participant=self.person, user=self.user, login_email='profile@example.test', status='active', email_verified_at=timezone.now())
        self.career = ParticipantCareerProfile.objects.create(participant=self.person, skills_interests='Original skill summary', education_training='Original training summary', career_interests='Original career interest', willing_to_travel=True)
        self.other_user = User.objects.create_user('synthetic-other-profile')
        self.other = Participant.objects.create(first_name='SYNTHETIC', last_name='Other Person')
        ParticipantAccount.objects.create(participant=self.other, user=self.other_user, login_email='other-profile@example.test', status='active', email_verified_at=timezone.now())
        self.admin = User.objects.create_superuser('synthetic-profile-reviewer', password='SYNTHETIC-unit-only')
        self.client.force_login(self.user, backend='apps.participants.auth.ParticipantAuthenticationBackend')

    def add(self, section, **changes):
        values = {'request_key': uuid.uuid4(), 'custom_name': 'SYNTHETIC ' + section, 'experience_details': 'Self-reported detail'}
        if section == 'work':
            values = {'request_key': uuid.uuid4(), 'role': 'SYNTHETIC Equipment operator', 'employer': 'SYNTHETIC Employer',
                      'location': '', 'started_on': None, 'ended_on': None, 'is_current': True, 'description': 'Equipment experience'}
        values.update(changes)
        return save_entry(self.user, section, values)

    def path(self, name, *args):
        return reverse('participants:' + name, args=args)

    def test_default_profile_is_read_first_with_section_actions_and_jobs_navigation(self):
        response = self.client.get(self.path('profile'))
        self.assertEqual(response.status_code, 200)
        for text in ('Work Experience', 'Skills &amp; Trades', 'Education &amp; Training', 'Licenses &amp; Certifications',
                     'Edit Information', 'Add Your Work Experience', 'Add Your Skills', 'Upload Your Resume', 'Job Opportunities'):
            self.assertContains(response, text)
        self.assertNotContains(response, '<input type="text"')
        self.assertNotContains(response, '<textarea')
        jobs = self.client.get(self.path('portal-jobs'))
        self.assertContains(jobs, 'Coming soon')
        self.assertContains(jobs, 'aria-current="page" href="' + self.path('portal-jobs') + '"')
        self.assertNotContains(self.client.get(self.path('portal-events')), '<h2>Job Opportunities</h2>')

    def test_each_current_section_updates_only_its_fields(self):
        path = self.path('portal-profile-edit', 'availability')
        page = self.client.get(path)
        data = {'revision': page.context['form']['revision'].value(), 'work_availability': 'Weekends', 'willing_to_travel': '3',
                'career_interests': 'ATTACKED', 'date_of_birth': '1900-01-01', 'notes': 'ATTACKED'}
        self.assertEqual(self.client.post(path, data).status_code, 302)
        self.career.refresh_from_db(); self.person.refresh_from_db()
        self.assertEqual(self.career.career_interests, 'Original career interest')
        self.assertEqual(self.career.education_training, 'Original training summary')
        self.assertEqual(self.career.work_availability, 'Weekends')
        self.assertEqual(self.person.date_of_birth, date(1995, 1, 1))
        for section in ('personal', 'preferences', 'skills-summary', 'education-summary'):
            response = self.client.get(self.path('portal-profile-edit', section))
            self.assertContains(response, 'Cancel')
            self.assertNotContains(response, 'date_of_birth')

    def test_cancel_and_get_do_not_mutate_profile(self):
        before = (self.person.updated_at, self.career.updated_at)
        for section in ('personal', 'preferences', 'availability', 'skills-summary', 'education-summary'):
            self.assertEqual(self.client.get(self.path('portal-profile-edit', section)).status_code, 200)
        self.person.refresh_from_db(); self.career.refresh_from_db()
        self.assertEqual(before, (self.person.updated_at, self.career.updated_at))

    def test_stale_section_edit_rejected(self):
        path = self.path('portal-profile-edit', 'skills-summary')
        page = self.client.get(path); revision = page.context['form']['revision'].value()
        self.career.education_training = 'A newer update'; self.career.save()
        response = self.client.post(path, {'revision': revision, 'skills_interests': 'Stale replacement'})
        self.assertContains(response, 'changed while this page was open')
        self.career.refresh_from_db(); self.assertEqual(self.career.skills_interests, 'Original skill summary')

    def test_adding_and_editing_work_preserves_other_entries(self):
        first = self.add('work'); second = self.add('work', role='SYNTHETIC Welder')
        path = self.path('portal-entry-edit', 'work', first.pk)
        page = self.client.get(path)
        values = {k: page.context['form'][k].value() or '' for k in page.context['form'].fields}
        values.update(role='SYNTHETIC Updated operator', is_current='on')
        self.assertEqual(self.client.post(path, values).status_code, 302)
        self.assertEqual(ParticipantWorkExperience.objects.filter(participant=self.person).count(), 2)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(first.role, 'SYNTHETIC Updated operator'); self.assertEqual(second.role, 'SYNTHETIC Welder')

    def test_add_work_allows_unpaid_equipment_activity_without_fictitious_employer(self):
        path = self.path('portal-entry-add', 'work')
        response = self.client.post(path, {'request_key': str(uuid.uuid4()), 'role': 'SYNTHETIC Family farm equipment', 'description': 'Unpaid experience'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ParticipantWorkExperience.objects.get(participant=self.person).employer, '')

    def test_add_skill_training_credential_uses_existing_qualification_model(self):
        for section, name in [('skills', 'SYNTHETIC TIG'), ('training', 'SYNTHETIC Welding course'), ('credentials', 'SYNTHETIC License')]:
            path = self.path('portal-entry-add', section)
            data = {'request_key': str(uuid.uuid4()), 'custom_name': name, 'experience_details': 'Participant description'}
            if section != 'skills':
                data['reported_provider'] = 'SYNTHETIC Training provider'
            self.assertEqual(self.client.post(path, data).status_code, 302)
            row = ParticipantQualification.objects.get(participant=self.person, custom_name=name)
            self.assertTrue(row.is_self_reported); self.assertEqual(row.source, 'participant')
            self.assertEqual(qualification_verification_summary(row)['status'], 'unverified')
        self.career.refresh_from_db()
        self.assertEqual(self.career.skills_interests, 'Original skill summary')
        self.assertEqual(self.career.education_training, 'Original training summary')

    def test_replayed_add_request_does_not_duplicate_entries(self):
        for section in ('work', 'skills', 'training', 'credentials'):
            key = uuid.uuid4(); first = self.add(section, request_key=key); second = self.add(section, request_key=key)
            self.assertEqual(first.pk, second.pk)
            changed = {'role': 'Different'} if section == 'work' else {'custom_name': 'Different'}
            with self.assertRaises(ValidationError): self.add(section, request_key=key, **changed)

    def test_cannot_reclassify_or_edit_other_section_with_an_id(self):
        row = self.add('training')
        self.assertEqual(self.client.get(self.path('portal-entry-edit', 'credentials', row.pk)).status_code, 403)

    def test_cross_participant_work_and_qualification_access_denied(self):
        work = ParticipantWorkExperience.objects.create(participant=self.other, source='participant', created_by=self.other_user, role='SYNTHETIC other')
        qual = ParticipantQualification.objects.create(participant=self.other, source='participant', custom_name='SYNTHETIC other skill', reported_kind='skill', is_self_reported=True, created_by=self.other_user)
        for section, row in [('work', work), ('skills', qual)]:
            for action in ('portal-entry-edit', 'portal-entry-archive'):
                self.assertEqual(self.client.get(self.path(action, section, row.pk)).status_code, 404)
                self.assertEqual(self.client.post(self.path(action, section, row.pk), {'custom_name': 'ATTACKED'}).status_code, 404)
        with self.assertRaises(ParticipantQualification.DoesNotExist): save_entry(self.user, 'skills', {'custom_name': 'ATTACKED'}, qual.pk)

    def test_direct_services_reject_staff_field_injection(self):
        for changes in ({'source': 'staff'}, {'is_self_reported': False}, {'notes': 'ATTACKED'}, {'participant_id': self.other.pk}, {'is_active': False}):
            with self.assertRaises(PermissionDenied): self.add('skills', **changes)

    def test_staff_records_notes_and_verification_are_protected(self):
        work = ParticipantWorkExperience.objects.create(participant=self.person, employer='SYNTHETIC Staff employer', role='Staff role', source='staff', created_by=self.admin)
        qual = ParticipantQualification.objects.create(participant=self.person, custom_name='SYNTHETIC Staff credential', source='staff', notes='PRIVATE INTERNAL QUALIFICATION NOTE')
        ParticipantNote.objects.create(participant=self.person, author=self.admin, content='PRIVATE INTERNAL PARTICIPANT NOTE')
        self.assertEqual(self.client.post(self.path('portal-entry-edit', 'work', work.pk), {}).status_code, 403)
        self.assertEqual(self.client.post(self.path('portal-entry-edit', 'credentials', qual.pk), {}).status_code, 403)
        page = self.client.get(self.path('profile'))
        self.assertNotContains(page, 'PRIVATE INTERNAL')

    def test_any_staff_review_locks_participant_entry_and_legacy_snapshot_unchanged(self):
        row = self.add('credentials')
        decision = ParticipantVerificationRecord.objects.create(participant=self.person, qualification=row,
            status='verified', method='staff_confirmed', reviewer=self.admin, reviewed_at=timezone.now(),
            evidence_notes='SYNTHETIC reviewed', qualification_snapshot=qualification_snapshot(row))
        snapshot = dict(decision.qualification_snapshot)
        self.assertEqual(self.client.post(self.path('portal-entry-edit', 'credentials', row.pk), {}).status_code, 403)
        self.assertEqual(self.client.post(self.path('portal-entry-archive', 'credentials', row.pk), {}).status_code, 403)
        decision.refresh_from_db(); self.assertEqual(decision.qualification_snapshot, snapshot)
        self.assertEqual(qualification_verification_summary(row)['status'], 'verified')
        legacy = ParticipantQualification.objects.create(participant=self.person, custom_name='SYNTHETIC Legacy')
        self.assertNotIn('reported_kind', qualification_snapshot(legacy))
        self.assertNotIn('reported_provider', qualification_snapshot(legacy))

    def test_staff_edits_lock_own_work(self):
        row = self.add('work'); row.updated_by = self.admin; row.save()
        self.assertEqual(self.client.post(self.path('portal-entry-edit', 'work', row.pk), {}).status_code, 403)

    def test_editable_intake_work_and_claim_retain_original_submission_and_provenance(self):
        from .models import RegistrationForm, RegistrationFormVersion, RegistrationSubmission
        form = RegistrationForm.objects.create(slug='synthetic-profile-history')
        version = RegistrationFormVersion.objects.create(form=form, version=1, snapshot={'questions': []})
        source = RegistrationSubmission.objects.create(form_version=version, participant=self.person,
            status='submitted', answers={'original': 'SYNTHETIC original intake answer'},
            answer_snapshot={'original': 'SYNTHETIC original snapshot'})
        work = ParticipantWorkExperience.objects.create(participant=self.person, source='intake', source_submission=source,
            source_entry_id=uuid.uuid4(), role='Original intake role', reported_duration='1–3 years')
        claim = ParticipantQualification.objects.create(participant=self.person, source='registration',
            source_submission=source, source_question_key='original_credential', source_answer_hash='a' * 64,
            source_answer_snapshot={'answer': 'Original intake claim'}, custom_name='Original claim', is_self_reported=True)
        save_entry(self.user, 'work', {'revision': work.updated_at.isoformat(), 'role': 'Current clarified role',
            'employer': '', 'location': '', 'started_on': None, 'ended_on': None, 'is_current': False, 'description': ''}, work.pk)
        save_entry(self.user, 'credentials', {'revision': claim.updated_at.isoformat(), 'custom_name': 'Current clarified claim'}, claim.pk)
        source.refresh_from_db(); work.refresh_from_db(); claim.refresh_from_db()
        self.assertEqual(source.answers, {'original': 'SYNTHETIC original intake answer'})
        self.assertEqual(source.answer_snapshot, {'original': 'SYNTHETIC original snapshot'})
        self.assertEqual(work.source_submission_id, source.pk); self.assertEqual(work.reported_duration, '1–3 years')
        self.assertEqual(claim.source_answer_snapshot, {'answer': 'Original intake claim'})
        self.assertEqual(claim.source_answer_hash, 'a' * 64)
        with self.assertRaises(PermissionDenied): archive_entry(self.user, 'work', work.pk, work.updated_at.isoformat())

    def test_linked_evidence_prevents_edit_and_archive(self):
        row = self.add('credentials')
        document = ParticipantDocument.objects.create(participant=self.person, category=ParticipantDocumentCategory.objects.filter(is_active=True).first(),
            file='synthetic-not-opened.pdf', original_filename='synthetic.pdf', content_type='application/pdf', file_size=20)
        QualificationDocumentLink.objects.create(document=document, qualification=row, linked_by=self.admin)
        self.assertEqual(self.client.get(self.path('portal-entry-edit', 'credentials', row.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.path('portal-entry-archive', 'credentials', row.pk)).status_code, 403)

    def test_archive_is_confirmed_post_and_keeps_records_and_audit(self):
        row = self.add('skills'); path = self.path('portal-entry-archive', 'skills', row.pk)
        self.assertEqual(self.client.get(path).status_code, 200)
        row.refresh_from_db(); self.assertTrue(row.is_active)
        self.assertEqual(self.client.post(path, {'revision': row.updated_at.isoformat()}).status_code, 302)
        row.refresh_from_db(); self.assertFalse(row.is_active)
        self.assertTrue(AuditLog.objects.filter(action='portal.profile_entry_archived', object_id=str(row.pk)).exists())

    def test_stale_entry_edit_and_archive_do_not_overwrite(self):
        row = self.add('skills'); stale = row.updated_at.isoformat(); row.custom_name = 'Newer'; row.save()
        with self.assertRaises(ValidationError): save_entry(self.user, 'skills', {'custom_name': 'Stale', 'revision': stale}, row.pk)
        with self.assertRaises(ValidationError): archive_entry(self.user, 'skills', row.pk, stale)
        row.refresh_from_db(); self.assertEqual(row.custom_name, 'Newer'); self.assertTrue(row.is_active)

    def test_csrf_required_for_section_entry_and_archive(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user, backend='apps.participants.auth.ParticipantAuthenticationBackend')
        row = self.add('skills')
        for name, args in [('portal-profile-edit', ['personal']), ('portal-entry-add', ['work']), ('portal-entry-archive', ['skills', row.pk])]:
            self.assertEqual(client.post(self.path(name, *args), {}).status_code, 403)

    def test_plain_staff_and_anonymous_cannot_enter_profile_editors(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.path('portal-entry-add', 'skills')).status_code, 302)
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(self.path('portal-entry-add', 'skills')).status_code, 403)

    def test_new_information_visible_to_authorized_existing_staff_workspace(self):
        work = self.add('work'); skill = self.add('skills'); training = self.add('training', reported_provider='SYNTHETIC Academy')
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        url = self.path('staff-profile', self.person.pk)
        self.assertContains(self.client.get(url + '?section=career'), work.role)
        page = self.client.get(url + '?section=qualifications')
        self.assertContains(page, skill.custom_name); self.assertContains(page, training.custom_name)
        self.assertContains(page, 'Participant reported')

    @override_settings(BTW_PREVIEW_GATE_ENABLED=True, BTW_PREVIEW_GATE_USERNAME='synthetic-review', BTW_PREVIEW_GATE_PASSWORD='synthetic-only')
    def test_admin_preview_uses_same_editors_and_retains_admin_identity(self):
        import base64
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(b'synthetic-review:synthetic-only').decode()
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.client.post(reverse('internal:portal-preview-enter'))
        self.assertEqual(self.client.session['_auth_user_id'], str(self.admin.pk))
        path = self.path('portal-entry-add', 'skills')
        self.assertContains(self.client.get(path), 'Admin Preview')
        self.assertEqual(self.client.post(path, {'request_key': uuid.uuid4(), 'custom_name': 'SYNTHETIC Preview-only skill'}).status_code, 302)
        self.assertFalse(ParticipantQualification.objects.filter(participant=self.person, custom_name='SYNTHETIC Preview-only skill').exists())
        self.client.post(reverse('internal:portal-preview-exit'))
        self.assertEqual(self.client.session['_auth_user_id'], str(self.admin.pk))


@skipUnless(connection.vendor == 'postgresql', 'Concurrent creation requires PostgreSQL row locks')
class PortalProfileEntryConcurrencyTests(TransactionTestCase):
    def test_same_add_request_creates_one_work_and_one_qualification_under_concurrency(self):
        user = get_user_model().objects.create_user('synthetic-profile-race')
        person = Participant.objects.create(first_name='SYNTHETIC', last_name='Concurrent')
        ParticipantAccount.objects.create(participant=person, user=user, login_email='race-profile@example.test',
                                          status='active', email_verified_at=timezone.now())
        for section in ('work', 'skills'):
            key = uuid.uuid4(); barrier = Barrier(2)
            def add():
                close_old_connections()
                try:
                    barrier.wait(timeout=15)
                    values = {'request_key': key, 'role': 'SYNTHETIC Concurrent work', 'employer': '', 'location': '',
                              'started_on': None, 'ended_on': None, 'is_current': False, 'description': ''} if section == 'work' else {
                              'request_key': key, 'custom_name': 'SYNTHETIC Concurrent skill'}
                    return save_entry(user, section, values).pk
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:
                ids = list(pool.map(lambda _: add(), range(2)))
            self.assertEqual(ids[0], ids[1])
        self.assertEqual(ParticipantWorkExperience.objects.filter(participant=person).count(), 1)
        self.assertEqual(ParticipantQualification.objects.filter(participant=person).count(), 1)
