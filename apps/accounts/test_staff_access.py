from datetime import timedelta
from io import StringIO
import json
from django.test import TestCase,Client,override_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group,Permission
from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.staffing.models import StaffProfile
from apps.participants.models import Participant,ParticipantAccount
from .roles import ROLE_GROUPS,has_role,role_configuration,roles_for
from .staff_services import save_account,revision,set_active,issue_invitation,invitation_token,read_invitation,accept_invitation,rate_limit
from .models import StaffInvitation,StaffAccessAttempt
U=get_user_model()
class StaffFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        role_configuration(apply=True)
        cls.admin=U.objects.create_superuser('synthetic-access-admin','admin@example.test',None)
        cls.manager=U.objects.create_user('synthetic-access-manager',email='manager@example.test',is_staff=True)
        cls.manager.groups.add(Group.objects.get(name=ROLE_GROUPS['manager']))
        cls.worker=U.objects.create_user('synthetic-access-worker',email='worker@example.test',is_staff=True)
        cls.worker.groups.add(Group.objects.get(name=ROLE_GROUPS['event_staff']))
        cls.recruiter=U.objects.create_user('synthetic-access-recruiter',email='recruiter@example.test',is_staff=True)
        cls.recruiter.groups.add(Group.objects.get(name=ROLE_GROUPS['recruiter']))
        for user in [cls.manager,cls.worker,cls.recruiter]:StaffProfile.objects.create(user=user,first_name='SYNTHETIC',last_name=user.username)
    def data(self,user=None,roles=None):
        return dict(username=user.username if user else 'synthetic-new-account',email=user.email if user else 'new@example.test',first_name='SYNTHETIC',last_name='Example',roles=roles or ['event_staff'],confirm_identity=True,reason='SYNTHETIC authorized account review',revision=revision(user) if user else '',is_active=user.is_active if user else False)
    def login(self,user):self.client.force_login(user,backend='django.contrib.auth.backends.ModelBackend')
    def test_role_definitions_are_idempotent_and_never_enroll_existing_users(self):
        before=list(U.groups.through.objects.order_by('pk').values());count=Group.objects.count()
        report=role_configuration();self.assertTrue(all(not x['add'] and not x['remove'] for x in report))
        role_configuration(apply=True);self.assertEqual(count,Group.objects.count());self.assertEqual(before,list(U.groups.through.objects.order_by('pk').values()))
    def test_administrator_and_explicit_worker_are_independent(self):
        self.assertTrue(has_role(self.admin,'administrator'));self.assertFalse(has_role(self.admin,'event_staff'))
        self.assertTrue(has_role(self.worker,'event_staff'));self.assertFalse(has_role(self.worker,'manager'))
    def test_legacy_session_hash_is_preserved_until_security_event(self):
        self.assertEqual(self.worker._get_session_auth_hash(),AbstractBaseUser._get_session_auth_hash(self.worker))
        self.worker.security_revision=1;self.assertNotEqual(self.worker._get_session_auth_hash(),AbstractBaseUser._get_session_auth_hash(self.worker))
    def test_manager_creates_inactive_ordinary_account_with_unusable_password(self):
        user=save_account(actor=self.manager,data=self.data())
        self.assertFalse(user.is_active);self.assertFalse(user.has_usable_password());self.assertFalse(user.is_superuser)
        self.assertEqual(roles_for(user),{'event_staff'});self.assertTrue(StaffProfile.objects.filter(user=user).exists())
    def test_manager_cannot_create_or_promote_manager_or_administrator(self):
        for role in ['manager','administrator']:
            with self.subTest(role=role),self.assertRaises(PermissionDenied):save_account(actor=self.manager,data=self.data(roles=[role]))
        self.assertFalse(U.objects.filter(username='synthetic-new-account').exists())
    def test_forged_privilege_fields_are_rejected(self):
        for field in ['groups','user_permissions','is_superuser','is_staff','security_revision','password']:
            data=self.data();data[field]='forged'
            with self.subTest(field=field),self.assertRaises(PermissionDenied):save_account(actor=self.manager,data=data)
    def test_manager_cannot_edit_protected_or_inactive_manager_account(self):
        for user in [self.admin,self.manager]:
            with self.assertRaises(PermissionDenied):save_account(actor=self.manager,user_id=user.pk,data=self.data(user))
        self.manager.is_active=False;self.manager.save(update_fields=['is_active'])
        other=U.objects.create_user('synthetic-other-manager',is_staff=True);other.groups.add(Group.objects.get(name=ROLE_GROUPS['manager']))
        with self.assertRaises(PermissionDenied):save_account(actor=other,user_id=self.manager.pk,data=self.data(self.manager))
    def test_unexpected_legacy_direct_grant_requires_admin_review(self):
        self.worker.user_permissions.add(Permission.objects.get(content_type__app_label='accounts',codename='change_user'))
        with self.assertRaises(PermissionDenied):save_account(actor=self.manager,user_id=self.worker.pk,data=self.data(self.worker))
        self.assertTrue(self.worker.user_permissions.exists())
    def test_recruiter_and_worker_cannot_manage_accounts(self):
        for user in [self.worker,self.recruiter]:
            with self.assertRaises(PermissionDenied):save_account(actor=user,data=self.data())
            self.login(user);self.assertEqual(self.client.get(reverse('accounts:staff-index')).status_code,403)
    def test_manager_cannot_use_raw_user_or_group_admin_even_with_direct_permissions(self):
        self.manager.user_permissions.add(*Permission.objects.filter(content_type__app_label='accounts'),*Permission.objects.filter(content_type__app_label='auth',content_type__model='group'))
        self.login(self.manager)
        for url in [reverse('admin:accounts_user_changelist'),reverse('admin:auth_group_changelist'),reverse('admin:accounts_user_change',args=[self.admin.pk])]:self.assertEqual(self.client.get(url).status_code,403)
    def test_stale_revision_is_rejected(self):
        old=self.data(self.worker);self.worker.first_name='Changed';self.worker.save(update_fields=['first_name'])
        with self.assertRaises(ValidationError):save_account(actor=self.manager,user_id=self.worker.pk,data=old)
    def test_profile_hijack_rolls_back_new_identity(self):
        data=self.data();data['profile']=StaffProfile.objects.get(user=self.recruiter).pk
        with self.assertRaises(PermissionDenied):save_account(actor=self.manager,data=data)
        self.assertFalse(U.objects.filter(username=data['username']).exists())
    def test_staff_identity_cannot_be_merged_with_participant_account(self):
        user=U.objects.create_user('synthetic-participant',email='portal@example.test')
        person=Participant.objects.create(first_name='SYNTHETIC',last_name='Portal')
        ParticipantAccount.objects.create(user=user,participant=person,login_email=user.email)
        with self.assertRaises(PermissionDenied):save_account(actor=self.admin,user_id=user.pk,data=self.data(user))
        user.refresh_from_db();self.assertFalse(user.is_staff)
    def test_role_removal_revokes_session(self):
        self.worker.groups.add(Group.objects.get(name=ROLE_GROUPS['recruiter']));self.login(self.worker)
        self.assertEqual(self.client.get(reverse('internal:home')).status_code,200)
        save_account(actor=self.manager,user_id=self.worker.pk,data=self.data(self.worker,roles=['recruiter']))
        self.assertEqual(self.client.get(reverse('internal:home')).status_code,302)
    def test_deactivation_and_reactivation_do_not_restore_old_session(self):
        self.login(self.worker);cookie=self.client.cookies['sessionid'].value
        set_active(actor=self.manager,user_id=self.worker.pk,active=False,expected_revision=revision(self.worker),reason='SYNTHETIC deactivate')
        self.worker.refresh_from_db();self.assertEqual(self.client.get(reverse('internal:home')).status_code,302)
        set_active(actor=self.manager,user_id=self.worker.pk,active=True,expected_revision=revision(self.worker),reason='SYNTHETIC reactivate')
        old=Client();old.cookies['sessionid']=cookie;self.assertEqual(old.get(reverse('internal:home')).status_code,302)
    def test_last_active_administrator_is_protected(self):
        with self.assertRaises(ValidationError):set_active(actor=self.admin,user_id=self.admin.pk,active=False,expected_revision=revision(self.admin),reason='SYNTHETIC last admin')
    def pending(self):
        return save_account(actor=self.manager,data=self.data())
    def test_invitation_is_expiring_single_use_and_password_is_set_by_holder(self):
        user=self.pending();item=issue_invitation(user=user,actor=self.manager,expected_revision=revision(user),reason='SYNTHETIC setup');token=invitation_token(item)
        self.assertIsNotNone(read_invitation(token));result=accept_invitation(token=token,password='SYNTHETIC-test-setup-valid-7842')
        self.assertTrue(result.is_active);self.assertTrue(result.check_password('SYNTHETIC-test-setup-valid-7842'));self.assertIsNone(read_invitation(token))
        with self.assertRaises(ValidationError):accept_invitation(token=token,password='SYNTHETIC-test-setup-valid-7842')
    def test_invitation_expiry_resend_and_state_changes_invalidate(self):
        user=self.pending();first=issue_invitation(user=user,actor=self.manager,expected_revision=revision(user),reason='SYNTHETIC setup');token=invitation_token(first)
        second=issue_invitation(user=user,actor=self.manager,expected_revision=revision(user),reason='SYNTHETIC resend');self.assertIsNone(read_invitation(token))
        second.expires_at=timezone.now()-timedelta(seconds=1);second.save();self.assertIsNone(read_invitation(invitation_token(second)))
        third=issue_invitation(user=user,actor=self.manager,expected_revision=revision(user),reason='SYNTHETIC new');user.security_revision+=1;user.save();self.assertIsNone(read_invitation(invitation_token(third)))
    def test_invitation_issuer_must_retain_authority(self):
        user=self.pending();item=issue_invitation(user=user,actor=self.manager,expected_revision=revision(user),reason='SYNTHETIC setup');token=invitation_token(item)
        self.manager.is_active=False;self.manager.save();self.assertIsNone(read_invitation(token))
    def test_audit_records_do_not_contain_password_or_token(self):
        user=self.pending();item=issue_invitation(user=user,actor=self.manager,expected_revision=revision(user),reason='SYNTHETIC setup');token=invitation_token(item)
        accept_invitation(token=token,password='SYNTHETIC-test-setup-valid-7842')
        payload=json.dumps(list(AuditLog.objects.filter(action__startswith='staff.account.').values('old_data','new_data')),default=str)
        self.assertNotIn(token,payload);self.assertNotIn('SYNTHETIC-test-setup-valid-7842',payload);self.assertNotIn('password',payload)
    def test_throttle_is_hashed_and_bounded(self):
        self.assertTrue(rate_limit('test','sensitive@example.test',2));self.assertTrue(rate_limit('test','sensitive@example.test',2));self.assertFalse(rate_limit('test','sensitive@example.test',2))
        self.assertNotIn('sensitive',StaffAccessAttempt.objects.get().key)
    def test_forged_account_post_is_forbidden(self):
        self.login(self.manager);data=self.data();data['is_superuser']='true'
        self.assertEqual(self.client.post(reverse('accounts:staff-create'),data).status_code,403)
    def test_account_interface_and_native_admin_compatibility(self):
        self.login(self.manager);self.assertEqual(self.client.get(reverse('accounts:staff-index')).status_code,200)
        self.assertEqual(self.client.get(reverse('accounts:staff-edit',args=[self.worker.pk])).status_code,200)
        self.login(self.admin);self.assertEqual(self.client.get(reverse('admin:accounts_user_changelist')).status_code,200)

    def test_password_change_invalidates_an_issued_recovery_link(self):
        item=issue_invitation(user=self.worker,purpose='reset')
        token=invitation_token(item)
        self.worker.set_password('Changed-SYNTHETIC-Password-7');self.worker.save()
        self.assertIsNone(read_invitation(token))
    def test_staff_and_participant_emails_remain_separate(self):
        U.objects.create_user('synthetic-participant-email',email='new@example.test')
        staff=save_account(actor=self.manager,data=self.data())
        self.assertTrue(staff.is_staff)
        self.assertEqual(U.objects.filter(email='new@example.test').count(),2)
    def test_private_rollout_inventory_is_read_only(self):
        before=list(U.objects.order_by('pk').values_list('pk','security_revision'))
        output=StringIO();call_command('review_staff_access',stdout=output)
        self.assertEqual(json.loads(output.getvalue())['automatic_changes'],0)
        self.assertEqual(before,list(U.objects.order_by('pk').values_list('pk','security_revision')))

    def test_native_admin_rechecks_last_administrator_under_lock(self):
        from .admin import CustomUserAdmin
        from django.contrib import admin
        from django.test import RequestFactory
        from django.db import transaction
        request=RequestFactory().post('/admin/');request.user=self.admin
        changed=U.objects.get(pk=self.admin.pk);changed.is_active=False
        with transaction.atomic(),self.assertRaises(ValidationError):
            CustomUserAdmin(U,admin.site).save_model(request,changed,None,True)
        self.admin.refresh_from_db();self.assertTrue(self.admin.is_active)

    def test_native_admin_rejects_actor_revoked_after_form_opened(self):
        from .admin import CustomUserAdmin
        from django.contrib import admin
        from django.test import RequestFactory
        from django.db import transaction
        request=RequestFactory().post('/admin/');request.user=self.admin
        U.objects.filter(pk=self.admin.pk).update(is_active=False)
        changed=U.objects.get(pk=self.worker.pk);changed.is_superuser=True
        with transaction.atomic(),self.assertRaises(PermissionDenied):
            CustomUserAdmin(U,admin.site).save_model(request,changed,None,True)
        self.worker.refresh_from_db();self.assertFalse(self.worker.is_superuser)
