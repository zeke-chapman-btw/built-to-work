from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event
from apps.staffing.models import AvailabilityRequest, StaffAssignment, StaffingPlan, StaffProfile


class HomeV2Tests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('home-v2', 'home-v2@example.test', 'test-only-password', first_name='Zeke')
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        now = timezone.now()
        self.event = Event.objects.create(name='SYNTHETIC v2 event', start_at=now + timedelta(days=3), end_at=now + timedelta(days=3, hours=6), status='upcoming', city='Augusta', state='GA')
        self.worker = StaffProfile.objects.create(first_name='SYNTHETIC', last_name='Worker')

    def test_greeting_attention_rows_and_staffing_column(self):
        StaffingPlan.objects.create(event=self.event, required_staff=3)
        StaffAssignment.objects.create(event=self.event, staff=self.worker)
        AvailabilityRequest.objects.create(event=self.event, staff=StaffProfile.objects.create(first_name='SYNTHETIC', last_name='Asker'))
        response = self.client.get(reverse('internal:home'))
        self.assertContains(response, 'Good ')
        self.assertContains(response, ', Zeke</h1>')
        ids = [row['id'] for row in response.context['home_attention']]
        self.assertIn('staff-requests', ids)
        self.assertIn('short-staffed', ids)
        self.assertContains(response, 'Staff requests to approve')
        self.assertContains(response, 'Upcoming events short on staff')
        self.assertContains(response, '1 / 3')
        self.assertContains(response, 'Augusta, GA')

    def test_staff_without_staffing_access_see_no_staffing_rows(self):
        AvailabilityRequest.objects.create(event=self.event, staff=self.worker)
        user = get_user_model().objects.create_user('home-v2-staff', is_staff=True)
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('internal:home'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('staff-requests', [row['id'] for row in response.context['home_attention']])
        self.assertNotContains(response, 'Staff requests to approve')
