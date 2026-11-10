"""Synthetic universal recovery, collision denial and legacy token compatibility."""
import re
from urllib.parse import urlsplit
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase,Client,override_settings
from django.urls import reverse
from django.utils import timezone
from apps.customers.test_portal import PortalFixtures,PASSWORD
from apps.customers import test_portal_preview as preview_fixtures
from apps.customers.models import CustomerAccessToken
from apps.participants.models import Participant,ParticipantAccount
from .models import StaffInvitation
from . import unified_recovery as recovery

User=get_user_model()

class UnifiedRecoveryTests(PortalFixtures,TestCase):
    def setUp(self):
        self.org1,self.member,self.customer=self.lead()
        self.customer_email=self.member.identity.email
        self.person=Participant.objects.create(first_name='SYNTHETIC',last_name='Recovery',contact_email='synthetic-contact-only@example.test')
        self.participant=User.objects.create_user('synthetic-recovery-participant',password=PASSWORD)
        self.account=ParticipantAccount.objects.create(user=self.participant,participant=self.person,
            login_email='synthetic-recovery-portal@example.test',status='active',email_verified_at=timezone.now())
        mail.outbox=[]

    def request_reset(self,email,username='',route='accounts:recovery'):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse(route),{'email':email,'username':username})

    def reset_path(self):
        urls=re.findall(r'https?://[^\s]+',mail.outbox[-1].body)
        self.assertTrue(urls,'Expected one synthetic recovery URL')
        return urlsplit(urls[0]).path

    def collide(self):
        self.admin.email=self.customer_email;self.admin.save(update_fields=['email'])
        self.account.login_email=self.customer_email;self.account.save()

    def test_all_recovery_entry_points_render_one_form_without_realm_selector(self):
        bodies=[]
        for route in ('accounts:recovery','customer_portal:recovery','participants:password-reset'):
            response=self.client.get(reverse(route));bodies.append(response.context['page_title'])
            self.assertContains(response,'Reset your password');self.assertContains(response,'Account email')
            self.assertContains(response,'Username (optional)');self.assertContains(response,'Back to sign in')
            self.assertContains(response, 'id="id_username_helptext"')
            self.assertNotContains(response,'name="account_type"');self.assertEqual(response['Referrer-Policy'],'same-origin')
        self.assertEqual(len(set(bodies)),1)

    def test_unique_staff_email_reuses_staff_invitation_reset(self):
        response=self.request_reset(self.admin.email)
        self.assertContains(response,'Check your email');self.assertEqual(len(mail.outbox),1)
        self.assertEqual(mail.outbox[0].to,[self.admin.email])
        self.assertTrue(StaffInvitation.objects.filter(user=self.admin,purpose='reset').exists())
        self.assertTrue(self.reset_path().startswith('/accounts/activate/'))

    def test_unique_customer_email_reuses_customer_token(self):
        self.request_reset(self.customer_email)
        self.assertEqual(len(mail.outbox),1);self.assertEqual(mail.outbox[0].to,[self.customer_email])
        self.assertTrue(CustomerAccessToken.objects.filter(identity=self.member.identity,purpose='reset').exists())
        self.assertTrue(self.reset_path().startswith('/customer/auth/recovery/'))

    def test_unique_participant_email_uses_verified_login_address_not_contact(self):
        self.request_reset(self.person.contact_email);self.assertEqual(len(mail.outbox),0)
        self.request_reset(self.account.login_email)
        self.assertEqual(len(mail.outbox),1);self.assertEqual(mail.outbox[0].to,[self.account.login_email])
        self.assertTrue(self.reset_path().startswith('/participants/password-reset/confirm/'))

    def test_unknown_known_and_ambiguous_receive_identical_confirmation(self):
        known=self.request_reset(self.admin.email)
        unknown=self.request_reset('synthetic-missing@example.test')
        self.collide();ambiguous=self.request_reset(self.customer_email)
        self.assertEqual(known.content,unknown.content);self.assertEqual(known.content,ambiguous.content)
        self.assertEqual(len(mail.outbox),1)

    def test_cross_realm_collision_does_not_send_or_issue_any_reset(self):
        self.collide();self.request_reset(self.customer_email)
        self.assertEqual(len(mail.outbox),0)
        self.assertFalse(StaffInvitation.objects.filter(purpose='reset').exists())
        self.assertFalse(CustomerAccessToken.objects.filter(purpose='reset').exists())

    def test_existing_username_and_email_pair_can_identify_staff_among_collisions(self):
        self.collide();self.request_reset(self.customer_email,self.admin.username)
        self.assertEqual(len(mail.outbox),1)
        self.assertTrue(StaffInvitation.objects.filter(user=self.admin,purpose='reset').exists())
        self.assertFalse(CustomerAccessToken.objects.filter(purpose='reset').exists())

    def test_wrong_username_never_falls_back_to_customer_or_participant(self):
        self.request_reset(self.customer_email,'synthetic-wrong-username')
        self.request_reset(self.account.login_email,self.customer.username)
        self.assertEqual(len(mail.outbox),0)

    def test_duplicate_staff_email_requires_more_precise_existing_username(self):
        other=User.objects.create_user('synthetic-recovery-second-staff',email=self.admin.email,is_staff=True,password=PASSWORD)
        self.request_reset(self.admin.email);self.assertEqual(len(mail.outbox),0)
        self.request_reset(self.admin.email,other.username)
        self.assertEqual(len(mail.outbox),1)
        self.assertTrue(StaffInvitation.objects.filter(user=other,purpose='reset').exists())

    def test_inactive_staff_customer_and_disabled_participant_receive_no_email(self):
        self.admin.is_active=False;self.admin.save(update_fields=['is_active'])
        self.customer.is_active=False;self.customer.save(update_fields=['is_active'])
        self.account.status='disabled';self.account.save()
        for email in (self.admin.email,self.customer_email,self.account.login_email):
            self.request_reset(email)
        self.assertEqual(len(mail.outbox),0)

    def test_unactivated_identity_and_unverified_participant_do_not_reset(self):
        pending=self.membership(self.org1,role='member')
        self.account.email_verified_at=None;self.account.save()
        self.request_reset(pending.identity.email);self.request_reset(self.account.login_email)
        self.assertEqual(len(mail.outbox),0)

    def test_recovery_does_not_grant_customer_membership_or_company_access(self):
        self.org1.status='inactive';self.org1.save()
        self.request_reset(self.customer_email)
        self.assertEqual(len(mail.outbox),1)
        response=self.client.post(reverse('accounts:login'),{'username':self.customer_email,'password':PASSWORD})
        self.assertEqual(response.status_code,200)
        self.assertNotIn('_auth_user_id',self.client.session)

    def test_collision_or_eligibility_change_during_resolution_sends_nothing(self):
        target={'realm':'staff','user':self.admin.pk,'email':self.admin.email,'username':self.admin.username}
        with patch('apps.accounts.unified_recovery.candidates',side_effect=[[target],[]]):
            self.request_reset(self.admin.email)
        self.assertEqual(len(mail.outbox),0)

    def test_shared_throttle_across_all_legacy_recovery_routes(self):
        for route in ('accounts:recovery','customer_portal:recovery','participants:password-reset','accounts:recovery'):
            self.request_reset(self.account.login_email,route=route)
        self.assertEqual(len(mail.outbox),3)

    def test_recovery_has_no_authentication_side_effect(self):
        self.request_reset(self.customer_email)
        self.assertNotIn('_auth_user_id',self.client.session)
        self.assertNotIn('customer_organization',self.client.session)

    def test_csrf_required_for_all_entry_points(self):
        strict=Client(enforce_csrf_checks=True)
        for route in ('accounts:recovery','customer_portal:recovery','participants:password-reset'):
            self.assertEqual(strict.post(reverse(route),{'email':self.admin.email}).status_code,403)
        self.assertEqual(len(mail.outbox),0)

    @override_settings(**preview_fixtures.OPTIONS)
    def test_both_administrator_preview_modes_block_recovery(self):
        for entry in ('internal:portal-preview-customer-enter','internal:portal-preview-enter'):
            self.client=Client(HTTP_AUTHORIZATION=preview_fixtures.GATE)
            self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
            self.assertEqual(self.client.post(reverse(entry)).status_code,302)
            for route in ('accounts:recovery','customer_portal:recovery','participants:password-reset'):
                self.assertEqual(self.client.get(reverse(route)).status_code,403)
                self.assertEqual(self.request_reset(self.admin.email,route=route).status_code,403)
        self.assertEqual(len(mail.outbox),0)

    def test_staff_reset_confirmation_remains_single_use(self):
        self.request_reset(self.admin.email,self.admin.username);path=self.reset_path()
        values={'new_password1':PASSWORD+'New','new_password2':PASSWORD+'New'}
        self.assertEqual(self.client.post(path,values).status_code,302)
        self.assertContains(self.client.get(path),'invalid, expired or already used')

    def test_customer_reset_remains_single_use_and_revokes_existing_session(self):
        other=Client();other.force_login(self.customer,backend='apps.customers.auth.CustomerAuthenticationBackend')
        self.request_reset(self.customer_email);path=self.reset_path()
        values={'new_password1':PASSWORD+'New','new_password2':PASSWORD+'New'}
        self.assertEqual(self.client.post(path,values).status_code,302)
        self.assertEqual(self.client.get(path).status_code,400)
        self.assertEqual(other.get(reverse('customer_portal:home')).status_code,302)

    def test_participant_reset_remains_single_use_and_revokes_existing_session(self):
        other=Client();other.force_login(self.participant,backend='apps.participants.auth.ParticipantAuthenticationBackend')
        self.request_reset(self.account.login_email);path=self.reset_path()
        values={'password1':PASSWORD+'New','password2':PASSWORD+'New'}
        self.assertEqual(self.client.post(path,values).status_code,302)
        self.assertEqual(self.client.get(path).status_code,400)
        self.assertEqual(other.get(reverse('participants:profile')).status_code,302)

    def test_missing_and_malformed_email_show_validation_without_delivery(self):
        for value in ('','not-an-email'):
            response=self.request_reset(value)
            self.assertEqual(response.status_code,200);self.assertTrue(response.context['form'].errors)
        self.assertEqual(len(mail.outbox),0)
