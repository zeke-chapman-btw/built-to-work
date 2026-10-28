"""Pass E presentation contracts over existing facts, sources and route guards."""
from datetime import datetime, timezone as dt_timezone
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.template.loader import render_to_string
from django.urls import reverse
from apps.events.models import Event
from apps.operations.overview_presentation import overview_rows, resource_overview_rows
from apps.participants.models import Participant, ParticipantCareerProfile
from apps.participants.qualification_models import ParticipantQualification
from apps.staffing.models import StaffProfile, StaffAssignment


class FinalRefinementETests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.admin = U.objects.create_superuser('synthetic-e-admin', email='e@example.invalid', password=None)
        cls.worker = U.objects.create_user('synthetic-e-worker', is_staff=True)
        cls.staff = StaffProfile.objects.create(user=cls.worker, first_name='SYNTHETIC', last_name='Worker')
        cls.events = [Event.objects.create(name=f'SYNTHETIC E event {n}', status='upcoming',
            start_at=datetime(2030, 7, n, 12, tzinfo=dt_timezone.utc),
            end_at=datetime(2030, 7, n, 14, tzinfo=dt_timezone.utc)) for n in (1, 2)]
        StaffAssignment.objects.create(event=cls.events[0], staff=cls.staff, status='confirmed')

    def login(self, user=None):
        self.client.force_login(user or self.admin, backend='django.contrib.auth.backends.ModelBackend')

    def test_readiness_dates_keep_years_and_event_local_calendar_boundaries(self):
        cases = [
            ('2026-10-28T12:00:00+00:00', '2026-10-28T14:00:00+00:00', 'UTC', ['Oct 28, 2026']),
            ('2027-01-21T12:00:00+00:00', '2027-01-21T14:00:00+00:00', 'UTC', ['Jan 21, 2027']),
            ('2030-07-01T12:00:00+00:00', '2030-07-02T14:00:00+00:00', 'UTC', ['Jul 1, 2030', 'Jul 2, 2030']),
            ('2026-12-31T12:00:00+00:00', '2027-01-01T14:00:00+00:00', 'UTC', ['Dec 31, 2026', 'Jan 1, 2027']),
            ('2027-01-01T01:00:00+00:00', '2027-01-01T03:00:00+00:00', 'America/Los_Angeles', ['Dec 31, 2026']),
        ]
        for start, end, zone, labels in cases:
            with self.subTest(start=start, zone=zone):
                event = Event(pk=99, name='SYNTHETIC Dates', status='upcoming', start_at=datetime.fromisoformat(start), end_at=datetime.fromisoformat(end), timezone_name=zone)
                before = (event.start_at, event.end_at, event.status)
                html = render_to_string('operations/resource_overview_row.html', {'item': {'event': event, 'url': '/event/', 'resource_checks': []}})
                for label in labels: self.assertIn(label, html)
                self.assertEqual(before, (event.start_at, event.end_at, event.status))
                self.assertEqual(html.count(labels[0]), 1)

    def test_directory_retains_years_in_both_table_and_phone_card(self):
        self.login()
        response = self.client.get(reverse('events:event_list'), {'view': 'all'})
        self.assertContains(response, 'Jul 1, 2030', count=2)
        self.assertContains(response, 'Jul 2, 2030', count=2)

    def test_four_summary_and_structured_evidence_combinations_remain_independent(self):
        self.login()
        for has_summary, has_record in [(False, True), (True, False), (False, False), (True, True)]:
            with self.subTest(summary=has_summary, record=has_record):
                person = Participant.objects.create(first_name='SYNTHETIC', last_name='Skills')
                career = ParticipantCareerProfile.objects.create(participant=person, skills_interests='SYNTHETIC Blueprint reading' if has_summary else '')
                if has_record:
                    ParticipantQualification.objects.create(participant=person, custom_name='SYNTHETIC Welding', reported_kind='skill', source='participant', is_self_reported=True)
                before = list(ParticipantQualification.objects.filter(participant=person).values())
                response = self.client.get(reverse('participants:staff-profile', args=[person.pk]))
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertNotIn('Skills not recorded.', html)
                self.assertEqual('Profile skills summary not provided.' in html, not has_summary)
                self.assertEqual('SYNTHETIC Blueprint reading' in html, has_summary)
                self.assertEqual('SYNTHETIC Welding' in html, has_record)
                if has_record:
                    self.assertIn('Unverified', html); self.assertIn('Participant reported', html); self.assertIn('Self-reported', html)
                self.assertEqual(before, list(ParticipantQualification.objects.filter(participant=person).values()))
                career.refresh_from_db()
                self.assertEqual(career.skills_interests, 'SYNTHETIC Blueprint reading' if has_summary else '')

    def test_two_event_rows_distinguish_shared_library_from_guarded_event_execution(self):
        rows = resource_overview_rows(self.admin, overview_rows(self.admin, self.events))
        library = reverse('participants:intake-config-index')
        for row, event in zip(rows, self.events):
            html = render_to_string('operations/resource_overview_row.html', {'item': row})
            self.assertIn('Forms library', html)
            self.assertIn(library, html)
            self.assertIn('Open shared Form Builder library.', html)
            self.assertIn('for ' + event.name, html)
            self.assertIn(reverse('operations:execution', args=[event.pk]), html)
            self.assertNotIn(reverse('operations:execution', args=[self.events[1 if event == self.events[0] else 0].pk]), html)
        self.login()
        self.assertEqual(self.client.get(library).status_code, 200)
        for event in self.events:
            self.assertEqual(self.client.get(reverse('operations:execution', args=[event.pk])).status_code, 200)

    def test_restricted_worker_keeps_own_execution_link_and_no_library_link(self):
        row = resource_overview_rows(self.worker, overview_rows(self.worker, [self.events[0]]))[0]
        html = render_to_string('operations/resource_overview_row.html', {'item': row})
        self.assertNotIn(reverse('participants:intake-config-index'), html)
        self.assertIn(reverse('operations:execution', args=[self.events[0].pk]), html)
        self.assertIn('Restricted', html)
        self.login(self.worker)
        self.assertEqual(self.client.get(reverse('participants:intake-config-index')).status_code, 403)
        self.assertEqual(self.client.get(reverse('operations:execution', args=[self.events[1].pk])).status_code, 403)

    def test_active_secondary_filters_reopen_without_losing_values_or_clear(self):
        self.login()
        response = self.client.get(reverse('operations:tasks'), {'view': 'all', 'priority': 'high'})
        self.assertContains(response, 'class="filter-advanced" open')
        self.assertContains(response, 'Filter tasks · Active filters')
        self.assertContains(response, 'value="high" selected')
        self.assertContains(response, 'Clear filters')
        self.assertNotContains(self.client.get(reverse('operations:tasks')), 'class="filter-advanced" open')
