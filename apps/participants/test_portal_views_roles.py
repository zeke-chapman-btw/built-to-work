from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.roles import ROLE_GROUPS, role_configuration
from apps.participants.models import Participant

BACKEND = 'django.contrib.auth.backends.ModelBackend'


@override_settings(BTW_PORTAL_VIEWS_ENABLED=True)
class PortalViewRoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        role_configuration(apply=True)
        User = get_user_model()
        cls.admin = User.objects.create_superuser('portal-views-admin', 'pv-admin@example.test', 'x-test-only')
        cls.recruiter = User.objects.create_user('portal-views-recruiter', password='x-test-only', is_staff=True)
        cls.recruiter.groups.add(Group.objects.get(name=ROLE_GROUPS['recruiter']))
        cls.manager = User.objects.create_user('portal-views-manager', password='x-test-only', is_staff=True)
        cls.manager.groups.add(Group.objects.get(name=ROLE_GROUPS['manager']))
        cls.staff = User.objects.create_user('portal-views-staff', password='x-test-only', is_staff=True)
        cls.staff.groups.add(Group.objects.get(name=ROLE_GROUPS['event_staff']))

    def test_recruiter_sees_participant_portal_only(self):
        self.client.force_login(self.recruiter, backend=BACKEND)
        page = self.client.get(reverse('internal:portal-preview'))
        self.assertContains(page, 'Go to Participant Portal')
        self.assertNotContains(page, 'Go to Customer Portal')
        self.assertNotContains(page, 'Staff view')
        self.assertEqual(self.client.post(reverse('internal:portal-preview-enter')).status_code, 302)
        self.assertEqual(self.client.post(reverse('internal:portal-preview-customer-enter')).status_code, 403)

    def test_manager_sees_both_and_practice_person_stays_out_of_the_list(self):
        self.client.force_login(self.manager, backend=BACKEND)
        page = self.client.get(reverse('internal:portal-preview'))
        self.assertContains(page, 'Go to Participant Portal')
        self.assertContains(page, 'Go to Customer Portal')
        self.assertEqual(self.client.post(reverse('internal:portal-preview-enter')).status_code, 302)
        self.assertTrue(Participant.objects.filter(last_name='Admin Portal Preview').exists())
        self.client.post(reverse('internal:portal-preview-exit'))
        directory = self.client.get(reverse('participants:staff-directory'))
        self.assertNotContains(directory, 'Admin Portal Preview')

    def test_event_staff_cannot_open_portal_views(self):
        self.client.force_login(self.staff, backend=BACKEND)
        self.assertEqual(self.client.get(reverse('internal:portal-preview')).status_code, 403)
        self.assertEqual(self.client.post(reverse('internal:portal-preview-enter')).status_code, 403)

    def test_portals_have_a_help_page(self):
        from apps.training.models import Guide
        Guide.objects.create(slug='p-help', title='SYNTHETIC Sign up for an event', audiences=['participant'])
        Guide.objects.create(slug='c-help', title='SYNTHETIC Look at candidates', audiences=['customer'])
        self.client.force_login(self.manager, backend=BACKEND)
        self.client.post(reverse('internal:portal-preview-enter'))
        page = self.client.get(reverse('participants:portal-help'))
        self.assertContains(page, 'SYNTHETIC Sign up for an event')
        self.assertNotContains(page, 'SYNTHETIC Look at candidates')
        self.assertContains(self.client.get(reverse('participants:portal-help-guide', args=['p-help'])), 'Walk me through it', status_code=200) if Guide.objects.get(slug='p-help').steps.exists() else None
        self.assertEqual(self.client.get(reverse('participants:portal-help-guide', args=['p-help'])).status_code, 200)
        self.client.post(reverse('internal:portal-preview-exit'))
        self.client.post(reverse('internal:portal-preview-customer-enter'))
        page = self.client.get(reverse('customer_portal:workspace', args=['help']))
        self.assertContains(page, 'SYNTHETIC Look at candidates')
        self.assertEqual(self.client.get(reverse('customer_portal:help-guide', args=['c-help'])).status_code, 200)
