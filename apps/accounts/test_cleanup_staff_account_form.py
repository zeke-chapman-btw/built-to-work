"""Identity and ordinary roles precede exceptional authority without changing grants."""
from html.parser import HTMLParser
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from apps.accounts.roles import role_configuration, ROLE_GROUPS
from apps.accounts.staff_forms import StaffAccountForm
from apps.reporting.access import can_finalize, can_approve_payroll


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = []

    def handle_starttag(self, tag, attrs):
        if tag in ('input', 'select', 'textarea'):
            name = dict(attrs).get('name')
            if name:
                self.names.append(name)


class StaffAccountGroupingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        role_configuration(apply=True)
        cls.admin = get_user_model().objects.create_superuser('synthetic-account-layout-admin', 'account-admin@example.test', 'synthetic-password')
        cls.manager = get_user_model().objects.create_user('synthetic-account-layout-manager', password='synthetic-password', is_staff=True)
        cls.manager.groups.add(Group.objects.get(name=ROLE_GROUPS['manager']))

    def test_identity_roles_designations_and_confirmation_render_in_order(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('accounts:staff-edit', args=[self.admin.pk]))
        self.assertEqual(response.status_code, 200)
        controls = Controls()
        controls.feed(response.content.decode())
        names = controls.names
        self.assertLess(names.index('email'), names.index('roles'))
        self.assertLess(names.index('roles'), names.index('can_finalize_event_reports'))
        self.assertLess(names.index('can_approve_weekly_payroll'), names.index('confirm_identity'))
        for name in ('username', 'email', 'profile', 'can_finalize_event_reports', 'can_approve_weekly_payroll', 'reason', 'confirm_identity'):
            self.assertEqual(names.count(name), 1, name)

    def test_connected_creation_keeps_own_pay_separate_from_financial_authority(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('accounts:staff-create'))
        controls = Controls()
        controls.feed(response.content.decode())
        names = controls.names
        self.assertLess(names.index('email'), names.index('login_mode'))
        self.assertLess(names.index('roles'), names.index('my_pay_access'))
        self.assertLess(names.index('my_pay_access'), names.index('confirm_identity'))
        self.assertNotIn('can_finalize_event_reports', names)
        self.assertNotIn('can_approve_weekly_payroll', names)
        for name in ('existing_profile', 'existing_account', 'login_mode', 'my_pay_access', 'confirm_identity'):
            self.assertEqual(names.count(name), {'login_mode': 3, 'my_pay_access': 2}.get(name, 1), name)

    def test_grouping_preserves_every_visible_field_once(self):
        form = StaffAccountForm(actor=self.admin)
        grouped = [field.name for group in form.field_groups for field in group['fields']]
        self.assertEqual(sorted(grouped), sorted(field.name for field in form.visible_fields()))
        self.assertEqual(len(grouped), len(set(grouped)))

    def test_ordinary_roles_do_not_enable_financial_designations_by_default(self):
        form = StaffAccountForm({'username': 'synthetic-new-account', 'email': 'new@example.test', 'first_name': 'SYNTHETIC', 'last_name': 'Account', 'roles': ['administrator'], 'reason': 'SYNTHETIC test', 'confirm_identity': 'on'}, actor=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIs(form.cleaned_data['can_finalize_event_reports'], False)
        self.assertIs(form.cleaned_data['can_approve_weekly_payroll'], False)
        self.assertFalse(can_finalize(self.admin))
        self.assertFalse(can_approve_payroll(self.admin))

    def test_manager_cannot_see_or_submit_exceptional_authority_fields(self):
        self.client.force_login(self.manager, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('accounts:staff-create'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="can_finalize_event_reports"')
        self.assertNotContains(response, 'name="can_approve_weekly_payroll"')
        self.assertNotContains(response, 'Exceptional financial authority')
