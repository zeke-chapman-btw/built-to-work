"""Settings presentation must preserve current role and realm boundaries."""
from django.test import TestCase
from django.urls import reverse
from apps.customers.test_portal import PortalFixtures, BACKEND
from apps.participants.models import Participant, ConsentAcceptance
from apps.operations.models import Task


class WorkspaceSettingsAlignmentTests(PortalFixtures, TestCase):
    def staff_login(self, user=None):
        self.client.force_login(user or self.admin, backend='django.contrib.auth.backends.ModelBackend')

    def test_administrator_settings_preserves_existing_destinations_without_sidebar_caption(self):
        self.staff_login()
        response = self.client.get(reverse('internal:settings'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_title'], 'Settings')
        self.assertContains(response, 'aria-label="Settings sections"')
        for key in ['account', 'staff', 'portal', 'compensation', 'recruiting', 'advanced']:
            self.assertContains(response, 'data-settings-tab="' + key + '"')
        self.assertNotContains(response, '<p class="nav-group-label">Internal</p>', html=True)
        self.assertContains(response, reverse('accounts:password-change'))
        self.assertContains(response, reverse('workdays:mine'))
        self.assertContains(response, reverse('staffing:schedule'))

    def test_event_staff_cannot_reveal_administrator_tools_using_query_parameters(self):
        self.staff_login(self.worker)
        for section in ['staff', 'portal', 'compensation', 'recruiting', 'advanced']:
            response = self.client.get(reverse('internal:settings'), {'tab': section})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['settings_section'], 'account')
            for hidden in ['staff', 'portal', 'compensation', 'recruiting']:
                self.assertNotContains(response, 'data-settings-tab="' + hidden + '"')
            self.assertNotContains(response, 'href="' + reverse('accounts:staff-index') + '"')

    def test_manager_staff_tools_keep_the_current_administrator_only_boundaries(self):
        self.staff_login(self.manager)
        response = self.client.get(reverse('internal:settings'), {'tab': 'staff'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="' + reverse('accounts:staff-index') + '"')
        self.assertNotContains(response, 'data-settings-tab="portal"')
        self.assertNotContains(response, 'data-settings-tab="compensation"')
        self.assertNotContains(response, 'data-settings-tab="recruiting"')

    def test_staff_and_compensation_routes_keep_navigation_without_duplicate_workday_menu(self):
        self.staff_login()
        staff = self.client.get(reverse('accounts:staff-index'))
        self.assertEqual(staff.status_code, 200)
        self.assertContains(staff, 'data-settings-tab="staff" aria-current="page"')
        compensation = self.client.get(reverse('workdays:settings'))
        self.assertEqual(compensation.status_code, 200)
        self.assertContains(compensation, 'data-settings-tab="compensation" aria-current="page"')
        self.assertNotContains(compensation, 'aria-label="Staff workday navigation"')

    def test_unknown_section_is_not_reflected_or_used_as_a_destination(self):
        self.staff_login()
        response = self.client.get(reverse('internal:settings'), {'tab': 'https://outside.example/unsafe'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['settings_section'], 'account')
        self.assertNotContains(response, 'outside.example')

    def test_settings_sections_do_not_create_participants_consents_or_tasks(self):
        self.staff_login()
        before = (Participant.objects.count(), ConsentAcceptance.objects.count(), Task.objects.count())
        for section in ['account', 'staff', 'recruiting', 'advanced']:
            self.assertEqual(self.client.get(reverse('internal:settings'), {'tab': section}).status_code, 200)
        self.assertEqual(before, (Participant.objects.count(), ConsentAcceptance.objects.count(), Task.objects.count()))

    def test_customer_frame_uses_staff_geometry_without_staff_navigation_or_authority(self):
        org, member, customer = self.lead()
        self.client.force_login(customer, backend=BACKEND)
        response = self.client.get(reverse('customer_portal:workspace', args=['overview']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'has-customer-shell')
        self.assertNotContains(response, 'data-settings-tab=')
        denied = self.client.get(reverse('internal:settings'))
        self.assertEqual(denied.status_code, 403)

    def test_customer_candidate_access_description_matches_the_implemented_release_model(self):
        org, member, customer = self.lead()
        self.client.force_login(customer, backend=BACKEND)
        response = self.client.get(reverse('customer_portal:workspace', args=['access']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'only candidates explicitly released by BTW')
        self.assertNotContains(response, 'profile disclosure and contact are unavailable in this release')
