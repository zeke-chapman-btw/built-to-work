"""Corrective Pass 2 presentation and scope boundaries over existing services."""
from datetime import date, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from apps.internal_ui.home_data import home_data
from apps.participants.models import Participant, ParticipantCareerProfile
from apps.participants.ui_presentation import profile_preferences
from apps.participants.intake_defaults import default_contract
from apps.events.models import Event


class SummaryFactsTests(SimpleTestCase):
    def test_unknown_preferences_grouped_and_custom_answers_not_reinterpreted(self):
        value = profile_preferences(dict(career=None, current_travel='Unknown',
            intake_profile_rows=[dict(label='Custom original heading', value='Retained custom value')]))
        self.assertEqual(value['profile_preference_rows'], [])
        self.assertEqual(len(value['profile_missing_preferences']), 5)
        self.assertEqual(value['profile_intake_preferences'], [])

    def test_accepted_value_and_protection_are_preserved_for_display(self):
        definition = next(q for q in default_contract()['questions'] if q['destination']=='career_timeline')
        row = dict(destination='career_timeline', label='Customized timing question', value='Just exploring', source='synthetic-source', protected=True)
        result = profile_preferences(dict(intake_profile_rows=[row]))
        self.assertEqual(result['profile_intake_preferences'][0]['value'], row['value'])
        self.assertEqual(result['profile_intake_preferences'][0]['source'], row['source'])
        self.assertTrue(result['profile_intake_preferences'][0]['protected'])
        self.assertEqual(row['label'], 'Customized timing question')


class CorrectiveP2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.admin=U.objects.create_superuser('p2-synthetic-admin','p2@example.test','synthetic-only')
        cls.reader=U.objects.create_user('p2-synthetic-reader',is_staff=True)
        cls.reader.user_permissions.add(Permission.objects.get(content_type__app_label='participants',codename='view_participant'))
        cls.person=Participant.objects.create(first_name='Synthetic',last_name='P2 Person',city='Augusta',state='GA',date_of_birth=date(2009,1,1))
        ParticipantCareerProfile.objects.create(participant=cls.person,career_interests='Welding',skills_interests='Blueprint reading',years_of_experience=0)
        now=timezone.now()
        cls.event=Event.objects.create(name='SYNTHETIC P2 visible event',status='upcoming',start_at=now+timedelta(days=1),end_at=now+timedelta(days=2))
        cls.draft=Event.objects.create(name='SYNTHETIC P2 secret draft',status='draft',start_at=now+timedelta(days=1),end_at=now+timedelta(days=2))

    def login(self,user=None):
        self.client.force_login(user or self.admin,backend='django.contrib.auth.backends.ModelBackend')

    def test_directory_native_primary_and_independent_details_retain_query(self):
        self.login();r=self.client.get(reverse('participants:staff-directory'),{'q':'Synthetic','state':'GA'})
        self.assertEqual(r.status_code,200)
        self.assertContains(r,'btw-record-primary')
        self.assertContains(r,'p2-record-details')
        import re
        from html import unescape
        from urllib.parse import urlsplit, parse_qs
        link = re.search(r'class="btw-record-primary" href="([^"]+)"', r.content.decode()).group(1)
        origin = parse_qs(urlsplit(unescape(link)).query)['from'][0]
        self.assertEqual(origin, reverse('participants:staff-directory')+'?q=Synthetic&state=GA')
        self.assertNotContains(r,'onclick=')

    def test_restricted_overview_no_dob_notes_or_forbidden_links(self):
        self.login(self.reader);r=self.client.get(reverse('participants:staff-profile',args=[self.person.pk]))
        self.assertEqual(r.status_code,200)
        for label in ('Who they are','What they can do','What they are looking for','Blueprint reading'):
            self.assertContains(r,label)
        self.assertNotContains(r,'2009-01-01')
        self.assertNotContains(r,'DOB:')
        self.assertNotContains(r,'>Event history</a>')
        self.assertEqual(self.client.get(reverse('participants:staff-profile',args=[self.person.pk]),{'section':'notes'}).status_code,403)
        self.assertEqual(self.client.get(reverse('participants:staff-profile',args=[self.person.pk]),{'section':'documents'}).status_code,403)

    def test_home_events_use_existing_scope_and_never_show_drafts(self):
        admin=home_data(self.admin);reader=home_data(self.reader)
        self.assertIn(self.event,[*admin['home_events']])
        self.assertNotIn(self.draft,[*admin['home_events']])
        self.assertEqual(reader['home_events'],[])

    def test_failed_work_and_event_reads_are_unavailable_not_zero(self):
        with patch('apps.internal_ui.home_data._work_data',side_effect=DatabaseError('synthetic')):
            data=home_data(self.reader)
        self.assertEqual(data['home_work_state'],'unavailable');self.assertIsNone(data['home_task_count'])
        with patch('apps.internal_ui.home_data.task_access.events_for',side_effect=DatabaseError('synthetic')):
            self.assertEqual(home_data(self.reader)['home_events_state'],'unavailable')

    def test_home_more_shortcuts_preserves_all_personalized_choices(self):
        self.login();r=self.client.get(reverse('internal:home'))
        self.assertEqual(r.status_code,200)
        self.assertContains(r,'More shortcuts')
        self.assertContains(r,reverse('internal:quick-actions-edit'))
        for action in r.context['quick_actions']:
            self.assertContains(r,action['url'])

    def test_resources_distinguish_no_records_from_no_matches(self):
        self.login()
        for name in ('stations:list','operations:templates','operations:trailers'):
            empty=self.client.get(reverse(name));filtered=self.client.get(reverse(name),{'q':'no-synthetic-match'})
            self.assertEqual(empty.status_code,200);self.assertEqual(filtered.status_code,200)
            self.assertContains(filtered,'Clear filters')
            self.assertNotEqual(empty.content,filtered.content)
