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
        expected = {'profile-identity', 'profile-documents', 'profile-notes', 'profile-skills', 'profile-education', 'profile-credentials', 'profile-work', 'profile-preferences', 'profile-assessments', 'profile-events', 'profile-activity', 'profile-source', 'profile-recruiting', 'btw-interactions', 'employer-interactions'}
        self.assertTrue(expected <= nav.ids, expected - nav.ids)
        self.assertContains(response, 'Registration does not prove attendance')
        self.assertContains(response, 'no combined score')
        self.assertContains(response, 'Source, intake &amp; correction history')
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
