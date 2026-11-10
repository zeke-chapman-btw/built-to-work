from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.staffing.models import StaffProfile
from apps.reporting.access import own_pay_profile
from apps.reporting.payroll import own_statement
from .roles import role_configuration, ROLE_GROUPS, roles_for
from .models import StaffInvitation
from .staff_onboarding import add_staff_member, selection_token, set_own_pay_access
from .personal_profile import save_personal_profile, profile_revision


class StaffSetupTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username='owner3-admin', email='admin@example.com', is_staff=True, is_superuser=True)
        role_configuration(apply=True)
        self.manager = self.user('manager', 'manager')
        self.recruiter = self.user('recruiter', 'recruiter')
        self.event_staff = self.user('event', 'event_staff')

    def user(self, name, role):
        user = get_user_model().objects.create_user(username='owner3-' + name, email=name+'@example.com', is_staff=True)
        user.groups.add(Group.objects.get(name=ROLE_GROUPS[role]))
        return user

    def data(self, **changes):
        result = dict(first_name='Fictional', last_name='Person', email='person@example.com', phone='404-555-0101',
            shirt_size='L', is_active=True, login_mode='none', my_pay_access='no', confirm_identity=True)
        result.update(changes)
        return result

    def add(self, actor=None, **changes):
        return add_staff_member(actor=actor or self.admin, data=self.data(**changes))

    def test_profile_without_login_defaults_to_no_pay(self):
        count = get_user_model().objects.count()
        profile, invitation, form = self.add()
        self.assertIsNotNone(profile, form.errors)
        self.assertIsNone(profile.user_id)
        self.assertFalse(profile.my_pay_enabled)
        self.assertIsNone(invitation)
        self.assertEqual(get_user_model().objects.count(), count)

    def test_new_staff_sign_in_is_unusable_until_invited_and_has_only_selected_roles(self):
        profile, invitation, form = self.add(login_mode='new', username='owner3-person', login_email='signin@example.com', roles=['event_staff'], my_pay_access='yes')
        self.assertIsNotNone(profile, form.errors)
        profile.refresh_from_db()
        self.assertEqual(profile.email, 'person@example.com')
        self.assertEqual(profile.user.email, 'signin@example.com')
        self.assertFalse(profile.user.is_active)
        self.assertFalse(profile.user.has_usable_password())
        self.assertTrue(profile.my_pay_enabled)
        self.assertFalse(profile.user.can_approve_weekly_payroll)
        self.assertFalse(profile.user.can_finalize_event_reports)
        self.assertEqual(roles_for(profile.user), {'event_staff'})

    def test_optional_invitation_is_returned_without_sending_from_service(self):
        with patch('django.core.mail.send_mail') as send:
            profile, invitation, form = self.add(login_mode='new', username='owner3-person', login_email='signin@example.com', roles=['recruiter'], send_invitation=True)
        self.assertIsNotNone(profile, form.errors)
        self.assertEqual(invitation.user_id, profile.user_id)
        self.assertEqual(StaffInvitation.objects.count(), 1)
        send.assert_not_called()

    def test_manager_cannot_create_administrator_or_manager(self):
        for role in ('administrator', 'manager'):
            profile, invitation, form = self.add(actor=self.manager, login_mode='new', username='new-'+role, login_email='new@example.com', roles=[role])
            self.assertIsNone(profile)
            self.assertIn('roles', form.errors)
        self.assertFalse(StaffProfile.objects.exists())

    def test_event_staff_and_recruiter_cannot_create_or_enable_pay(self):
        profile = StaffProfile.objects.create(first_name='Existing', last_name='Person')
        for actor in (self.event_staff, self.recruiter):
            with self.assertRaises(PermissionDenied):
                self.add(actor=actor)
            with self.assertRaises(PermissionDenied):
                set_own_pay_access(actor=actor, profile_id=profile.pk, enabled=True, expected_revision=profile.updated_at.isoformat())

    def test_failed_account_validation_rolls_back_profile(self):
        before = StaffProfile.objects.count()
        with self.assertRaises(ValidationError):
            self.add(login_mode='new', username=self.event_staff.username, login_email='unique@example.com', roles=['event_staff'])
        self.assertEqual(StaffProfile.objects.count(), before)
        self.assertFalse(StaffInvitation.objects.exists())

    def test_existing_profile_is_explicit_and_contact_is_not_overwritten(self):
        profile = StaffProfile.objects.create(first_name='Existing', last_name='Person', email='kept@example.com', shirt_size='5XL')
        saved, invitation, form = self.add(existing_profile=selection_token(self.admin, profile), first_name='', last_name='', email='ignored@example.com', login_mode='new', username='linked', login_email='linked@example.com', roles=['event_staff'])
        self.assertIsNotNone(saved, form.errors)
        self.assertEqual(saved.pk, profile.pk)
        profile.refresh_from_db()
        self.assertEqual((profile.first_name, profile.email, profile.shirt_size), ('Existing', 'kept@example.com', '5XL'))
        self.assertEqual(StaffProfile.objects.count(), 1)

    def test_existing_account_connection_preserves_identity_roles_and_designations(self):
        original = (self.event_staff.username, self.event_staff.email, self.event_staff.first_name, self.event_staff.is_active)
        profile, invitation, form = self.add(login_mode='existing', existing_account=selection_token(self.admin, self.event_staff))
        self.assertIsNotNone(profile, form.errors)
        self.event_staff.refresh_from_db()
        self.assertEqual(profile.user_id, self.event_staff.pk)
        self.assertEqual((self.event_staff.username, self.event_staff.email, self.event_staff.first_name, self.event_staff.is_active), original)
        self.assertEqual(roles_for(self.event_staff), {'event_staff'})

    def test_duplicate_contact_requires_explicit_existing_profile(self):
        StaffProfile.objects.create(first_name='Existing', last_name='Person', email='person@example.com')
        with self.assertRaisesMessage(ValidationError, 'Select the existing profile'):
            self.add()
        self.assertEqual(StaffProfile.objects.count(), 1)

    def test_stale_or_other_actor_selection_cannot_link(self):
        profile = StaffProfile.objects.create(first_name='Existing', last_name='Person')
        token = selection_token(self.admin, profile)
        with self.assertRaises(ValidationError):
            self.add(actor=self.manager, existing_profile=token)
        profile.phone = 'changed'
        profile.save()
        with self.assertRaisesMessage(ValidationError, 'record changed'):
            self.add(existing_profile=token)

    def test_manager_cannot_enable_pay_for_protected_target(self):
        profile = StaffProfile.objects.create(first_name='Admin', last_name='Person', user=self.admin)
        with self.assertRaises(PermissionDenied):
            set_own_pay_access(actor=self.manager, profile_id=profile.pk, enabled=True, expected_revision=profile.updated_at.isoformat())

    def test_pay_default_and_immediate_revocation_even_with_stale_user(self):
        profile = StaffProfile.objects.create(first_name='Staff', last_name='Person', user=self.event_staff)
        with self.assertRaises(PermissionDenied):
            own_pay_profile(self.event_staff)
        set_own_pay_access(actor=self.admin, profile_id=profile.pk, enabled=True, expected_revision=profile.updated_at.isoformat())
        self.assertEqual(own_pay_profile(self.event_staff).pk, profile.pk)
        profile.refresh_from_db()
        set_own_pay_access(actor=self.admin, profile_id=profile.pk, enabled=False, expected_revision=profile.updated_at.isoformat())
        with self.assertRaises(PermissionDenied):
            own_pay_profile(self.event_staff)
        with self.assertRaises(PermissionDenied):
            own_statement(self.event_staff, self.admin.pk)

    def test_own_pay_flag_is_distinct_from_operational_profile_status(self):
        profile = StaffProfile.objects.create(first_name='Past', last_name='Staff', user=self.event_staff,
            is_active=False, my_pay_enabled=True)
        self.assertEqual(own_pay_profile(self.event_staff).pk, profile.pk)
        self.event_staff.is_active = False
        self.event_staff.save()
        with self.assertRaises(PermissionDenied):
            own_pay_profile(self.event_staff)

    def test_personal_contact_save_cannot_change_sign_in_or_access(self):
        profile = StaffProfile.objects.create(first_name='Staff', last_name='Person', user=self.event_staff, internal_notes='retained', my_pay_enabled=False)
        old_email, old_revision = self.event_staff.email, self.event_staff.security_revision
        saved, form = save_personal_profile(actor=self.event_staff, data=dict(first_name='New', last_name='Name', contact_email='contact@example.com', phone='404-555-0102', shirt_size='XS', revision=profile_revision(self.event_staff, profile), my_pay_enabled=True, is_superuser=True, email='attack@example.com'))
        self.assertTrue(saved, form.errors)
        self.event_staff.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual(self.event_staff.email, old_email)
        self.assertEqual(self.event_staff.security_revision, old_revision)
        self.assertFalse(self.event_staff.is_superuser)
        self.assertFalse(profile.my_pay_enabled)
        self.assertEqual(profile.internal_notes, 'retained')
        self.assertEqual((profile.email, profile.shirt_size), ('contact@example.com', 'XS'))

    def test_missing_profile_does_not_create_one(self):
        saved, form = save_personal_profile(actor=self.recruiter, data=dict(first_name='Own', last_name='Name', revision=profile_revision(self.recruiter, None), contact_email='ignored@example.com', shirt_size='L'))
        self.assertTrue(saved, form.errors)
        self.assertFalse(StaffProfile.objects.exists())

    def test_contact_only_edit_preserves_distinct_account_display_name_and_legacy_size(self):
        self.event_staff.first_name = 'Account display'
        self.event_staff.last_name = 'Name'
        self.event_staff.save()
        profile = StaffProfile.objects.create(first_name='Staff contact', last_name='Name',
            user=self.event_staff, shirt_size='Legacy', email='old@example.com')
        saved, form = save_personal_profile(actor=self.event_staff, data=dict(first_name=profile.first_name,
            last_name=profile.last_name, contact_email='new@example.com', phone='404-555-0104',
            shirt_size='Legacy', revision=profile_revision(self.event_staff, profile)))
        self.assertTrue(saved, form.errors)
        profile.refresh_from_db(); self.event_staff.refresh_from_db()
        self.assertEqual(self.event_staff.first_name, 'Account display')
        self.assertEqual(profile.first_name, 'Staff contact')
        self.assertEqual(profile.shirt_size, 'Legacy')
        self.assertEqual(profile.email, 'new@example.com')

    def test_missing_profile_retains_supported_long_user_names(self):
        name = 'N' * 140
        saved, form = save_personal_profile(actor=self.recruiter, data=dict(first_name=name,
            last_name='Name', revision=profile_revision(self.recruiter, None)))
        self.assertTrue(saved, form.errors)
        self.recruiter.refresh_from_db()
        self.assertEqual(self.recruiter.first_name, name)
        self.assertFalse(StaffProfile.objects.exists())

    def test_stale_personal_profile_does_not_overwrite(self):
        profile = StaffProfile.objects.create(first_name='Staff', last_name='Person', user=self.event_staff)
        token = profile_revision(self.event_staff, profile)
        profile.phone='changed'; profile.save()
        saved, form = save_personal_profile(actor=self.event_staff, data=dict(first_name='Bad', last_name='Overwrite', revision=token))
        self.assertFalse(saved)
        self.event_staff.refresh_from_db()
        self.assertNotEqual(self.event_staff.first_name, 'Bad')

    def test_directory_first_and_private_pay_discovery_without_sidebar(self):
        profile = StaffProfile.objects.create(first_name='Staff', last_name='Person', user=self.event_staff, my_pay_enabled=True)
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.assertRedirects(self.client.get(reverse('internal:settings')+'?tab=staff'), reverse('staffing:directory'))
        self.client.force_login(self.event_staff, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('internal:settings'))
        self.assertContains(response, reverse('reporting:my-pay'))
        from apps.internal_ui.navigation import visible_workspaces
        self.assertNotIn('my-pay', [x['id'] for x in visible_workspaces(self.event_staff)])
        profile.my_pay_enabled=False; profile.save()
        self.assertEqual(self.client.get(reverse('reporting:my-pay')).status_code, 403)
        for fmt in ('', '?format=csv', '?format=xlsx', '?format=pdf'):
            self.assertEqual(self.client.get(reverse('reporting:my-statement', args=[self.admin.pk])+fmt).status_code, 403)
