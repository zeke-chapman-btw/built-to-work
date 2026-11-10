"""Profile refinement preserves destinations, protected evidence and role boundaries."""
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.internal_ui.people_editors import people_return
from apps.participants.models import Participant, ParticipantCareerProfile


class ProfileNavigation(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.in_areas = False; self.areas = []; self.ids = set()
        self.feed(html)

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if attrs.get('id'): self.ids.add(attrs['id'])
        if tag == 'nav' and attrs.get('aria-label') == 'Participant areas': self.in_areas = True
        if tag == 'a' and self.in_areas: self.areas.append(attrs)

    def handle_endtag(self, tag):
        if tag == 'nav': self.in_areas = False


class CrmUxProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.admin = users.objects.create_superuser('synthetic-crmux-admin', 'crmux@example.test', 'synthetic-only')
        cls.reader = users.objects.create_user('synthetic-crmux-reader', is_staff=True)
        cls.reader.user_permissions.add(Permission.objects.get(content_type__app_label='participants', codename='view_participant'))
        cls.person = Participant.objects.create(first_name='SYNTHETIC Profile', last_name='Refinement', contact_email='profile@example.test')
        cls.career = ParticipantCareerProfile.objects.create(participant=cls.person, skills_interests='SYNTHETIC welding', willing_to_travel=False, years_of_experience=0)

    def setUp(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.url = reverse('participants:staff-profile', args=[self.person.pk])

    def test_three_areas_share_identity_and_appropriate_active_navigation(self):
        for section in ['overview', 'relationship', 'recruiting']:
            with self.subTest(section=section):
                response = self.client.get(self.url, {'section': section})
                self.assertEqual(response.status_code, 200)
                nav = ProfileNavigation(response.content.decode())
                self.assertEqual([parse_qs(urlsplit(a['href']).query)['section'][0] for a in nav.areas], ['overview', 'relationship', 'recruiting'])
                current = [a for a in nav.areas if a.get('aria-current') == 'page']
                self.assertEqual(len(current), 1)
                self.assertEqual(parse_qs(urlsplit(current[0]['href']).query)['section'], [section])
                self.assertContains(response, 'class="people-profile-name"', count=1)
                self.assertContains(response, str(self.person))

    def test_continuous_profile_retains_every_evidence_anchor_and_original_destinations(self):
        response = self.client.get(self.url)
        nav = ProfileNavigation(response.content.decode())
        expected = {'profile-identity', 'profile-documents', 'profile-notes', 'profile-skills', 'profile-education', 'profile-credentials', 'profile-work', 'profile-preferences', 'profile-assessments', 'profile-events', 'profile-touchpoints', 'profile-qualifications', 'profile-recruiting', 'btw-interactions', 'employer-interactions'}
        self.assertTrue(expected <= nav.ids, expected - nav.ids)
        self.assertNotContains(response, 'Relationship history &amp; tools')
        self.assertNotContains(response, 'Recruiting information')
        self.assertNotContains(response, 'No ambiguous qualification records.')
        self.assertNotContains(response, 'class="crm-panels"')
        self.assertContains(response, 'data-profile-legacy="btw"')
        relationship = self.client.get(self.url, {'section': 'relationship'})
        self.assertContains(relationship, 'id="btw-interactions"')
        recruiting = self.client.get(self.url, {'section': 'recruiting'})
        self.assertContains(recruiting, 'id="profile-recruiting"')
        self.assertContains(response, 'No events yet.')
        self.assertContains(response, 'Original answers')
        self.assertNotContains(response, 'Recent history')
        self.assertNotContains(response, 'no combined score')
        event_history = self.client.get(self.url, {'section':'events'}, HTTP_X_BTW_EDITOR='1')
        self.assertContains(event_history, 'Registrations — separate from participation')
        source_tools = self.client.get(self.url, {'section':'acquisition','tools':'source'})
        self.assertContains(source_tools, 'Permanent system reference')
        self.assertContains(source_tools, str(self.person.identity_uuid))
        self.assertContains(response, 'SYNTHETIC welding')
        self.assertEqual(Participant.objects.get(pk=self.person.pk).identity_uuid, self.person.identity_uuid)
        self.career.refresh_from_db(); self.assertIs(self.career.willing_to_travel, False)
        self.assertEqual(self.career.years_of_experience, 0)

    def test_all_legacy_section_urls_and_filtered_directory_origin_remain_available(self):
        origin = reverse('participants:staff-directory') + '?q=SYNTHETIC&page=2'
        for section in ['career', 'qualifications', 'documents', 'assessments', 'events', 'acquisition', 'notes', 'activity', 'tasks']:
            with self.subTest(section=section):
                response = self.client.get(self.url, {'section': section, 'from': origin})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['directory_origin'], origin)
                self.assertEqual(parse_qs(urlsplit(ProfileNavigation(response.content.decode()).areas[0]['href']).query)['from'], [origin])

    def test_relationship_and_recruiting_do_not_load_unrelated_assessment_history(self):
        with patch('apps.participants.workspace_views.activity_sources', side_effect=AssertionError('Unrelated evidence scan')):
            self.assertEqual(self.client.get(self.url, {'section': 'relationship'}).status_code, 200)
            self.assertEqual(self.client.get(self.url, {'section': 'recruiting'}).status_code, 200)

    def test_restricted_reader_gains_no_crm_or_recruiting_authority(self):
        self.client.force_login(self.reader, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(self.url)
        self.assertEqual(len(ProfileNavigation(response.content.decode()).areas), 1)
        self.assertNotContains(response, '>Log interaction</a>')
        for section in ['relationship', 'recruiting']:
            self.assertEqual(self.client.get(self.url, {'section': section}).status_code, 403)

    def test_archived_profile_retains_read_destinations_without_create_or_edit_actions(self):
        self.person.archived_at = timezone.now(); self.person.save(update_fields=['archived_at'])
        response = self.client.get(self.url, {'section': 'relationship'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '>Log interaction</a>')
        self.assertNotContains(response, '>Edit profile</a>')
        self.assertContains(response, 'Archived participant')

    def test_edit_return_accepts_new_sections_and_rejects_foreign_destinations(self):
        factory = RequestFactory()
        for section in ['relationship', 'recruiting']:
            url = self.url + '?section=' + section
            self.assertEqual(people_return(factory.get('/', {'return_to': url}), self.person), url)
        self.assertEqual(people_return(factory.get('/', {'return_to': 'https://foreign.example/steal'}), self.person), reverse('participants:staff-directory'))

    def test_full_relationship_summary_is_exclusive_to_relationship_destination(self):
        for section in ['overview', 'recruiting']:
            response = self.client.get(self.url, {'section': section})
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'aria-label="BTW relationship summary"')
        relationship = self.client.get(self.url, {'section': 'relationship'})
        self.assertContains(relationship, 'aria-label="BTW relationship summary"', count=1)
        self.assertContains(relationship, 'Relationship Summary')
        self.assertContains(relationship, 'Interaction History')
        self.assertContains(relationship, 'BTW-recorded customer activity')
        self.assertContains(relationship, 'Employer-owned activity')

    def test_profile_leads_with_personal_facts_and_preserves_separate_reported_skills(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertLess(html.index('id="profile-identity"'), html.index('id="profile-skills"'))
        self.assertContains(response, 'Reported skills')
        self.assertContains(response, 'SYNTHETIC welding')
        self.assertContains(response, '0.0 years reported')
        self.assertContains(response, 'Jump to section')
        self.assertNotContains(response, 'Profile facts &amp; evidence')

    def test_recruiting_remains_opportunity_scoped_without_relationship_summary(self):
        response = self.client.get(self.url, {'section': 'recruiting'})
        self.assertContains(response, 'Customer &amp; job connections')
        self.assertContains(response, 'No recruiting opportunities in your accessible records.')
        self.assertContains(response, 'official outcomes remain explicit BTW decisions.')
        self.assertNotContains(response, 'aria-label="BTW relationship summary"')

    def test_profile_card_edit_replaces_jump_and_retains_authorized_return(self):
        for user, can_edit in [(self.admin, True), (self.reader, False)]:
            self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
            response = self.client.get(self.url)
            header = response.content.decode().split('<section id="profile-identity"', 1)[1].split('</header>', 1)[0]
            self.assertNotIn('Jump to section', header)
            self.assertEqual('aria-label="Manage profile details"' in header, can_edit)
            if can_edit:
                self.assertIn(reverse('participants:profile-card-edit', args=[self.person.pk, 'identity']), header)
                self.assertIn('return_to=', header)
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.person.archived_at = timezone.now()
        self.person.save(update_fields=['archived_at'])
        response = self.client.get(self.url)
        header = response.content.decode().split('<section id="profile-identity"', 1)[1].split('</header>', 1)[0]
        self.assertNotIn('aria-label="Manage profile details"', header)

    def test_lighter_skill_rows_retain_zero_experience_source_and_expandable_evidence(self):
        from apps.participants.qualification_models import ParticipantQualification
        ParticipantQualification.objects.create(participant=self.person, custom_name='SYNTHETIC Review Skill', reported_kind='skill', years_of_experience=0, source='participant', is_self_reported=True, experience_details='SYNTHETIC retained qualification evidence')
        response = self.client.get(self.url)
        self.assertContains(response, 'SYNTHETIC Review Skill')
        self.assertContains(response, '0.0 years')
        self.assertContains(response, 'SYNTHETIC retained qualification evidence')
        self.assertContains(response, 'Details &amp; evidence')
        self.assertContains(response, 'Full qualification &amp; evidence')
        self.assertContains(response, 'Self-reported')

    def test_relationship_has_one_primary_modal_action_and_concise_summary(self):
        import uuid
        from apps.crm.services import create_internal
        create_internal(actor=self.admin, participant_id=self.person.pk, data={'activity_type':'call','summary':'SYNTHETIC retained contact','occurred_at':timezone.now()}, operation_id=uuid.uuid4())
        response=self.client.get(self.url, {'section':'relationship'})
        self.assertContains(response, '>+ Log touchpoint</a>', count=1)
        self.assertContains(response, 'data-record-editor')
        self.assertNotContains(response, 'Interaction shortcuts')
        for label in ['Log call','Record email','Record text','Log meeting','Relationship note']:
            self.assertNotContains(response,label)
        for label in ['Last BTW contact','Recent BTW activity','Next follow-up','BTW-recorded customer activity']:
            self.assertContains(response,label)
        self.assertContains(response,'SYNTHETIC retained contact')
        self.assertContains(response,'Manually recorded')
        self.assertContains(response,'BTW staff')
        self.assertContains(response,'datetime=')
        self.assertContains(response,'crm-channel-icon')
        self.assertContains(response,'UTC')
