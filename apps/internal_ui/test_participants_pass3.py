"""Pass 3 presentation regressions against authorized records and real save APIs."""
from collections import Counter
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.participants.models import Participant, ParticipantCareerProfile
from apps.participants.qualification_models import ParticipantQualification
from apps.participants.ui_presentation import qualification_display_groups
from apps.participants.workspace_services import profile_revision


class Controls(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.names = Counter()
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'input', 'select', 'textarea'} and attrs.get('name'):
            self.names[attrs['name']] += 1


class QualificationGroupingTests(SimpleTestCase):
    def record(self, kind='', catalog='', label='Synthetic record'):
        return SimpleNamespace(reported_kind=kind, catalog_entry=SimpleNamespace(kind=catalog) if catalog else None, custom_name=label)

    def test_explicit_kinds_and_legacy_are_distinct_and_not_mutated(self):
        records = [self.record(kind) for kind in ('skill', 'training', 'credential', '')]
        before = [vars(row).copy() for row in records]
        groups = qualification_display_groups(records)
        self.assertEqual([g['title'] for g in groups], ['Skills & Trades', 'Education & Training', 'Licenses & Certifications', 'Other recorded qualifications'])
        self.assertEqual([item['record'] for g in groups for item in g['items']], records)
        self.assertEqual([vars(row) for row in records], before)

    def test_conflicting_and_generic_catalog_records_are_honestly_retained(self):
        records = [self.record('skill', 'license'), self.record('', 'qualification'), self.record('', 'experience')]
        groups = qualification_display_groups(records)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['title'], 'Other recorded qualifications')
        self.assertIn('conflict', groups[0]['items'][0]['classification_note'])
        self.assertEqual(len(groups[0]['items']), 3)

    def test_catalog_kind_used_without_name_inference(self):
        groups = qualification_display_groups([self.record('', 'license'), self.record(label='CDL OSHA certified electrician')])
        self.assertEqual([g['title'] for g in groups], ['Licenses & Certifications', 'Other recorded qualifications'])

    def test_empty_and_supplied_page_only(self):
        self.assertEqual(qualification_display_groups([]), [])
        records = [self.record('skill') for _ in range(26)]
        self.assertEqual(sum(len(g['items']) for g in qualification_display_groups(records[25:])), 1)


class ParticipantsPass3Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.owner = U.objects.create_superuser('synthetic-pass3-test', 'pass3@example.test', 'synthetic-only')
        cls.reader = U.objects.create_user('synthetic-pass3-reader', is_staff=True)
        cls.reader.user_permissions.add(Permission.objects.get(content_type__app_label='participants', codename='view_participant'))
        cls.person = Participant.objects.create(first_name='SYNTHETIC Jordan', last_name='Torres', city='Augusta', state='GA', contact_email='jordan@example.test')
        cls.career = ParticipantCareerProfile.objects.create(participant=cls.person, career_interests='Welding', skills_interests='Blueprint reading', years_of_experience=0, willing_to_travel=False)

    def setUp(self):
        self.client.force_login(self.owner, backend='django.contrib.auth.backends.ModelBackend')

    def test_whole_profile_editor_has_each_existing_field_once(self):
        response = self.client.get(reverse('participants:staff-edit', args=[self.person.pk]))
        self.assertEqual(response.status_code, 200)
        controls = Controls(response.content.decode())
        expected = [field.html_name for form in (response.context['form'], response.context['career_form']) for field in form]
        for name in expected:
            with self.subTest(name=name): self.assertEqual(controls.names[name], 1)
        self.assertContains(response, 'One Save profile updates all profile sections.')
        self.assertContains(response, 'data-section-form')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, '>Save profile</button>', count=1)

    def test_save_reopen_preserves_all_career_values_and_identity(self):
        url = reverse('participants:staff-edit', args=[self.person.pk])
        response = self.client.get(url)
        data = {}
        for form in (response.context['form'], response.context['career_form']):
            for field in form:
                value = field.value()
                data[field.html_name] = '' if value is None else value
        identity = self.person.identity_uuid
        data.update(city='SYNTHETIC updated city', audit_reason='SYNTHETIC complete-profile update')
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.person.refresh_from_db(); self.career.refresh_from_db()
        self.assertEqual(self.person.identity_uuid, identity)
        self.assertEqual(self.person.city, 'SYNTHETIC updated city')
        self.assertEqual(self.career.skills_interests, 'Blueprint reading')
        self.assertEqual(self.career.years_of_experience, 0)
        self.assertIs(self.career.willing_to_travel, False)
        reopened = self.client.get(url)
        self.assertEqual(reopened.context['career_form']['skills_interests'].value(), 'Blueprint reading')

    def test_profile_save_still_requires_reason_and_retains_invalid_entries(self):
        url = reverse('participants:staff-edit', args=[self.person.pk])
        response = self.client.get(url)
        data = {field.html_name: (field.value() if field.value() is not None else '') for form in (response.context['form'], response.context['career_form']) for field in form}
        data.update(first_name='SYNTHETIC retained draft', audit_reason='')
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context['form']['audit_reason'].errors)
        self.assertContains(response, 'SYNTHETIC retained draft', status_code=400)
        self.person.refresh_from_db()
        self.assertEqual(self.person.first_name, 'SYNTHETIC Jordan')

    def test_qualification_pages_retain_every_record_once_without_reclassification(self):
        records = [ParticipantQualification.objects.create(participant=self.person, custom_name=f'SYNTHETIC {i:02d}', reported_kind=('skill' if i % 2 else '')) for i in range(28)]
        url = reverse('participants:staff-profile', args=[self.person.pk])
        seen = []
        for page in (1, 2):
            response = self.client.get(url, {'section':'qualifications', 'page':page})
            self.assertEqual(response.status_code, 200)
            scoped = response.context['qualifications']
            seen.extend(row.pk for row in scoped)
            for row in scoped: self.assertContains(response, row.custom_name, count=1)
            self.assertContains(response, 'Groups show this page only.')
        self.assertCountEqual(seen, [row.pk for row in records])
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(ParticipantQualification.objects.filter(reported_kind='').count(), 14)

    def test_directory_uses_native_primary_link_and_independent_details(self):
        response = self.client.get(reverse('participants:staff-directory'), {'q':'Jordan', 'state':'GA'})
        html = response.content.decode()
        start = html.index('p3-directory-row')
        row = html[start:html.index('</article>', start)]
        self.assertIn('class="btw-record-primary"', row)
        self.assertLess(row.index('</a>'), row.index('<details'))
        self.assertNotIn('onclick=', row)
        self.assertIn('data-ui-menu', row)
        self.assertContains(response, 'Registrations')
        self.assertContains(response, 'Attendance recorded separately')

    def test_filters_preserve_every_authorized_field_and_reader_restrictions(self):
        url = reverse('participants:staff-directory')
        for user in (self.owner, self.reader):
            self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
            response = self.client.get(url, {'q':'Jordan'})
            controls = Controls(response.content.decode())
            for field in response.context['filter_form']:
                self.assertGreaterEqual(controls.names[field.html_name], 1, field.html_name)
            if user == self.reader:
                self.assertNotIn('document_status', controls.names)
                self.assertNotIn('qualification', controls.names)
                self.assertEqual(self.client.get(url, {'document_status':'present'}).status_code, 403)

    def test_all_existing_profile_destinations_and_secondary_portal_tool(self):
        url = reverse('participants:staff-profile', args=[self.person.pk])
        response = self.client.get(url)
        html = response.content.decode()
        for section in ('overview','career','qualifications','documents','assessments','events','notes','acquisition','activity','tasks'):
            self.assertIn('section='+section, html)
        hero = html[html.index('p3-profile-hero'):html.index('people-workspace event-workspace-stack')]
        self.assertIn('More actions', hero)
        self.assertIn('Participant portal invitation', hero)
        self.client.force_login(self.reader, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(url)
        self.assertNotContains(response, 'Participant portal invitation')
        for section in ('notes','documents','qualifications','events','assessments','activity'):
            self.assertEqual(self.client.get(url, {'section':section}).status_code, 403)

    def test_grouped_read_only_gets_leave_domain_records_and_revision_unchanged(self):
        ParticipantQualification.objects.create(participant=self.person, custom_name='SYNTHETIC legacy', reported_kind='')
        before = (profile_revision(self.person), list(ParticipantQualification.objects.values()))
        for params in ({}, {'section':'qualifications'}, {'section':'career'}):
            self.assertEqual(self.client.get(reverse('participants:staff-profile', args=[self.person.pk]), params).status_code, 200)
        self.person.refresh_from_db()
        self.assertEqual((profile_revision(self.person), list(ParticipantQualification.objects.values())), before)


class ContinuousProfileCorrectionsTests(TestCase):
    """Exercise real GET context, permission gates, and non-page-based previews."""
    @classmethod
    def setUpTestData(cls):
        ParticipantsPass3Tests.setUpTestData.__func__(cls)
        from datetime import date, timedelta
        from django.utils import timezone
        from apps.events.models import Event, EventRegistration
        from apps.participants.participant_document_models import ParticipantDocument, ParticipantDocumentCategory
        from apps.participants.workspace_models import ParticipantNote, ParticipantWorkExperience
        cls.event = Event.objects.create(name='SYNTHETIC continuous profile event', start_at=timezone.now()+timedelta(days=1), end_at=timezone.now()+timedelta(days=2), status='upcoming')
        EventRegistration.objects.create(event=cls.event, participant=cls.person, source='staff')
        cls.training = ParticipantQualification.objects.create(participant=cls.person, custom_name='SYNTHETIC older training', reported_kind='training', reported_provider='SYNTHETIC College')
        cls.credential = ParticipantQualification.objects.create(participant=cls.person, custom_name='SYNTHETIC older license', reported_kind='credential')
        cls.legacy = ParticipantQualification.objects.create(participant=cls.person, custom_name='SYNTHETIC ambiguous legacy', reported_kind='')
        for i in range(30):
            ParticipantQualification.objects.create(participant=cls.person, custom_name=f'SYNTHETIC newer skill {i:02d}', reported_kind='skill')
        category = ParticipantDocumentCategory.objects.get(slug='resume')
        cls.document = ParticipantDocument.objects.create(participant=cls.person, category=category, original_filename='SYNTHETIC old resume.txt', file='synthetic/not-a-public-path.txt', content_type='text/plain', file_size=12, uploaded_by=cls.owner)
        cls.note = ParticipantNote.objects.create(participant=cls.person, author=cls.owner, content='SYNTHETIC private continuous note')
        cls.work = ParticipantWorkExperience.objects.create(participant=cls.person, employer='SYNTHETIC employer', role='SYNTHETIC trainee', source='staff', started_on=date(2022,1,1), created_by=cls.owner)

    def setUp(self):
        self.client.force_login(self.owner,backend='django.contrib.auth.backends.ModelBackend')

    def profile(self, user=None, **params):
        self.client.force_login(user or self.owner, backend='django.contrib.auth.backends.ModelBackend')
        return self.client.get(reverse('participants:staff-profile', args=[self.person.pk]), params)

    def test_default_page_exposes_ordinary_evidence_without_section_changes(self):
        response = self.profile()
        self.assertEqual(response.status_code, 200)
        for text in ('SYNTHETIC old resume.txt', 'SYNTHETIC private continuous note',
                     'SYNTHETIC older training', 'SYNTHETIC older license',
                     'SYNTHETIC ambiguous legacy', 'SYNTHETIC employer',
                     'Assessment results', 'No linked simulator results recorded.',
                     'SYNTHETIC continuous profile event', 'Recent factual activity'):
            self.assertContains(response,text)
        self.assertContains(response,'Registration does not prove attendance.')
        self.assertContains(response,'No official experience sessions recorded.')
        self.assertContains(response,'Add internal note')
        self.assertContains(response,reverse('participants:staff-document-download',args=[self.document.uuid]))

    def test_old_categories_are_visible_independently_of_requested_page(self):
        response=self.profile(page='99')
        groups={g['key']:g for g in response.context['profile_qualification_groups']}
        self.assertEqual({k:g['total'] for k,g in groups.items()}, {'skills':30,'education':1,'credentials':1,'other':1})
        self.assertEqual(len(groups['skills']['items']),5)
        for row in (self.training,self.credential,self.legacy):
            self.assertContains(response,row.custom_name)
        self.assertNotContains(response,'No education or training recorded.')
        self.training.refresh_from_db(); self.assertEqual(self.training.reported_kind,'training')

    def test_restricted_role_neither_fetches_nor_discloses_private_collections(self):
        from unittest.mock import patch
        with patch('apps.participants.workspace_views.factual_timeline') as timeline:
            response=self.profile(self.reader)
        self.assertEqual(response.status_code,200)
        timeline.assert_not_called()
        for text in (self.note.content,self.document.original_filename,self.credential.custom_name,
                     self.event.name,'Download securely','Add internal note'):
            self.assertNotContains(response,text)
        for key in ('profile_notes','profile_documents','profile_qualification_groups','profile_activity','recorded_sessions','legacy_attendance'):
            self.assertIsNone(response.context.get(key),key)
        self.assertContains(response,'Assessment results are unavailable to your staff role.')

    def test_preview_limits_and_resume_are_independent_of_recent_document_page(self):
        from apps.participants.participant_document_models import ParticipantDocument, ParticipantDocumentCategory
        from apps.participants.workspace_models import ParticipantNote, ParticipantWorkExperience
        cat=ParticipantDocumentCategory.objects.create(name='SYNTHETIC supporting files',slug='synthetic-other')
        for i in range(8):
            ParticipantDocument.objects.create(participant=self.person,category=cat,original_filename=f'SYNTHETIC newer file {i}.txt',file='synthetic/private.txt',content_type='text/plain',file_size=12,uploaded_by=self.owner)
            ParticipantNote.objects.create(participant=self.person,author=self.owner,content=f'SYNTHETIC recent note {i}')
            ParticipantWorkExperience.objects.create(participant=self.person,employer=f'SYNTHETIC work {i}',source='staff',created_by=self.owner)
        response=self.profile(page='99',archived='1')
        self.assertEqual(len(response.context['profile_documents']),3)
        self.assertEqual(response.context['profile_documents'][0].pk,self.document.pk)
        self.assertEqual(len(response.context['profile_notes']),3)
        self.assertEqual(response.context['profile_note_total'],9)
        self.assertEqual(len(response.context['profile_work_history']),5)
        self.assertEqual(response.context['profile_work_total'],9)
        self.assertLessEqual(len(response.context['profile_activity']),10)
        self.assertContains(response,'3 of 9 active documents')

    def test_native_jump_links_have_real_focusable_on_page_targets(self):
        response=self.profile()
        class Links(HTMLParser):
            def __init__(self):super().__init__();self.ids={};self.jumps=[]
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if attrs.get('id','').startswith('profile-'):self.ids[attrs['id']]=attrs
                if tag=='a' and attrs.get('href','').startswith('#profile-'):self.jumps.append(attrs['href'][1:])
        parsed=Links();parsed.feed(response.content.decode())
        self.assertGreaterEqual(len(set(parsed.jumps)),12)
        for target in parsed.jumps:
            self.assertIn(target,parsed.ids)
            self.assertEqual(parsed.ids[target].get('tabindex'),'-1')
        self.assertContains(response,'Jump to section')
        self.assertContains(response,'Detailed management')
        self.assertNotContains(response,'onchange=')

    def test_overview_get_does_not_write_domain_records(self):
        from apps.core.models import AuditLog
        from apps.participants.participant_document_models import ParticipantDocument
        from apps.participants.workspace_models import ParticipantNote,ParticipantWorkExperience
        models=(Participant,ParticipantQualification,ParticipantDocument,ParticipantNote,ParticipantWorkExperience,AuditLog)
        before={m.__name__:list(m.objects.values()) for m in models}
        self.assertEqual(self.profile().status_code,200)
        self.assertEqual(before,{m.__name__:list(m.objects.values()) for m in models})

    def test_archived_profile_keeps_evidence_but_disables_mutation_actions(self):
        from django.utils import timezone
        self.person.archived_at=timezone.now();self.person.save(update_fields=['archived_at'])
        response=self.profile()
        self.assertContains(response,'staff profile management is read-only')
        self.assertContains(response,self.document.original_filename)
        self.assertNotContains(response,'>Edit profile</a>')
        self.assertNotContains(response,'>Add internal note</a>')
        self.assertNotContains(response,'>Add work experience</a>')

    def test_sparse_profile_has_truthful_empty_and_zero_states(self):
        person=Participant.objects.create(first_name='SYNTHETIC sparse',last_name='Example')
        ParticipantCareerProfile.objects.create(participant=person,years_of_experience=0,willing_to_travel=False)
        response=self.client.get(reverse('participants:staff-profile',args=[person.pk]))
        for text in ('No active documents recorded.','No active internal notes recorded.',
                     'No education or training recorded.','No licenses or certifications recorded.',
                     'No ambiguous qualification records.','No event registrations recorded.',
                     '0.0 years of reported overall experience'):
            self.assertContains(response,text)
