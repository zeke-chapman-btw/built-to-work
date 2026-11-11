"""Current-source corrections preserve scope, scheduling instants and role boundaries."""
import re
from datetime import datetime, timezone as utc
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from apps.events.models import Event, EventRegistration
from apps.operations.models import Task
from apps.staffing.models import StaffProfile
from apps.customers.test_portal import BACKEND
from apps.recruiting.test_employer import EmployerFixtures
from apps.recruiting import selection


class Post16A1Tests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()

    def staff_login(self, user=None):
        self.client.force_login(user or self.admin, backend='django.contrib.auth.backends.ModelBackend')

    def participant_login(self):
        self.client.force_login(self.portal, backend='apps.participants.auth.ParticipantAuthenticationBackend')

    def new_timezone_event(self, start, end):
        return Event.objects.create(name='SYNTHETIC local-time boundary', start_at=start,
            end_at=end, timezone_name='America/New_York', status='upcoming')

    def test_customer_matches_reuses_only_authorized_company_positions(self):
        other = self.org(name='SYNTHETIC unrelated employer')
        need = self.approved_need(other)
        foreign = selection.create_opportunity(actor=self.admin, need_id=need.pk, owner_id=self.admin.pk)
        self.staff_login()
        url = reverse('customers:workspace', args=[self.organization.pk, 'matches'])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('recruiting:matching', args=[self.opportunity.pk]))
        self.assertNotContains(response, reverse('recruiting:matching', args=[foreign.pk]))
        self.assertNotContains(response, self.person.contact_email)
        self.assertNotContains(response, 'Candidate matching is not yet available')
        self.assertEqual(list(response.context['recruiting_positions']), [self.opportunity])
        self.client.force_login(self.customer, backend=BACKEND)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_access_copy_does_not_claim_working_recruiting_is_unavailable(self):
        self.staff_login()
        response = self.client.get(reverse('customers:workspace', args=[self.organization.pk, 'access']))
        self.assertContains(response, 'Plan &amp; access')
        self.assertNotContains(response, 'remain unavailable regardless of entitlement')

    def test_participant_history_uses_the_event_local_date_without_changing_instants(self):
        start = datetime(2027, 1, 1, 2, 30, tzinfo=utc.utc)
        end = datetime(2027, 1, 1, 4, 0, tzinfo=utc.utc)
        event = self.new_timezone_event(start, end)
        EventRegistration.objects.create(event=event, participant=self.person)
        self.participant_login()
        response = self.client.get(reverse('participants:portal-activity'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'participants/portal/event_schedule.html')
        self.assertContains(response, 'Dec 31, 2026 · 9:30 PM')
        self.assertContains(response, 'Eastern (EST)')
        self.staff_login()
        overview = self.client.get(reverse('participants:staff-profile', args=[self.person.pk]))
        self.assertContains(overview, 'No events yet.')
        self.assertNotContains(overview, 'Dec 31, 2026 · 9:30 PM')
        for query in ({'section': 'events'},):
            staff = self.client.get(reverse('participants:staff-profile', args=[self.person.pk]), query)
            self.assertContains(staff, 'Dec 31, 2026 · 9:30 PM')
            self.assertContains(staff, 'Eastern (EST)')
        event.refresh_from_db()
        self.assertEqual((event.start_at, event.end_at), (start, end))

    def test_spring_and_fall_dst_labels_identify_the_actual_instants(self):
        spring = self.new_timezone_event(datetime(2027, 3, 14, 6, 30, tzinfo=utc.utc), datetime(2027, 3, 14, 7, 30, tzinfo=utc.utc))
        html = render_to_string('participants/portal/event_schedule.html', {'event': spring, 'show_end': True})
        self.assertIn('1:30 AM', html)
        self.assertIn('3:30 AM', html)
        self.assertIn('Eastern (EST)', html)
        self.assertIn('Eastern (EDT)', html)
        fall = self.new_timezone_event(datetime(2027, 11, 7, 5, 30, tzinfo=utc.utc), datetime(2027, 11, 7, 6, 30, tzinfo=utc.utc))
        html = render_to_string('participants/portal/event_schedule.html', {'event': fall, 'show_end': True})
        self.assertEqual(html.count('1:30 AM'), 2)
        self.assertIn('Eastern (EDT)', html)
        self.assertIn('Eastern (EST)', html)

    def test_preference_validation_keeps_input_and_has_one_h1_and_body_ownership(self):
        self.participant_login()
        url = reverse('recruiting:portal-preferences')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(re.findall(r'<h1\b', response.content.decode())), 1)
        self.assertContains(response, 'ux-card-body')
        invalid = self.client.post(url, {'operation': 'preferences', 'revision': 0,
            'willing_to_relocate': 'yes', 'reason': ''})
        self.assertEqual(invalid.status_code, 200)
        self.assertTrue(invalid.context['form'].errors)
        self.assertEqual(invalid.context['form']['willing_to_relocate'].value(), 'yes')
        self.assertContains(invalid, 'data-error-summary')

    def test_configuration_returns_to_settings_without_weakening_admin_guard(self):
        self.staff_login()
        for name in ['stages', 'classifications']:
            response = self.client.get(reverse('recruiting:' + name), {'from': 'settings'})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, reverse('internal:settings') + '?tab=recruiting')
        self.staff_login(self.recruiter)
        for name in ['stages', 'classifications']:
            self.assertEqual(self.client.get(reverse('recruiting:' + name), {'from': 'settings'}).status_code, 403)

    def test_task_summary_keeps_accessible_totals_separate_from_my_view(self):
        user = get_user_model().objects.create_user(username='synthetic-a1-assignee', is_staff=True)
        person = StaffProfile.objects.create(user=user, first_name='SYNTHETIC', last_name='Assignee')
        Task.objects.create(title='SYNTHETIC delegated task', assigned_staff=person, created_by=self.admin)
        self.staff_login()
        response = self.client.get('/operations/tasks/?view=mine')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Accessible · before filters')
        self.assertEqual(response.context['summary']['open'], 1)
        self.assertEqual(response.context['tasks'].paginator.count, 0)

    def test_actual_event_timestamp_uses_its_own_dst_instant_not_the_scheduled_start(self):
        event = self.new_timezone_event(datetime(2027, 3, 14, 6, 30, tzinfo=utc.utc), datetime(2027, 3, 14, 7, 30, tzinfo=utc.utc))
        actual = datetime(2027, 3, 14, 7, 45, tzinfo=utc.utc)
        html = render_to_string('shared/event_timestamp.html', {'event': event, 'timestamp': actual})
        self.assertIn('3:45 AM', html)
        self.assertIn('Eastern (EDT)', html)
        self.assertNotIn('1:30 AM', html)
