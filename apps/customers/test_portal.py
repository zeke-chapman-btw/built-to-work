"""Build 14 realm, tenancy, workflow and disclosure contracts on real Django views."""
import json
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.participants.models import Participant, ParticipantAccount
from apps.customers.test_domain import CustomerFixtures, WHY
from apps.customers.models import CustomerIdentity, CustomerMembership as Membership, CustomerAccessToken as Token, WorkforceNeed as Need, WorkforceNeedRevision as Revision, CustomerFeedback, Campaign
from apps.customers import services as s, invitations as i, projections as p, directory_policy as directory
from apps.customers.portal_policy import require_membership, eligible_memberships
from apps.customers.collaboration import campaign_submission, publish_feedback

User = get_user_model()
PASSWORD = 'Build14-synthetic-testing-password!27'
BACKEND = 'apps.customers.auth.CustomerAuthenticationBackend'


class PortalFixtures(CustomerFixtures):
    def portal_org(self, **kwargs):
        org = self.org(**kwargs)
        org = s.save_organization(actor=self.admin, org_id=org.pk, expected_revision=org.revision, data={'status': 'active'}, why=WHY)
        return org

    def issue(self, member, actor=None):
        member.refresh_from_db()
        org = member.organization
        org.refresh_from_db()
        raw = uuid.uuid4().hex + uuid.uuid4().hex
        with patch('apps.customers.invitations.secrets.token_urlsafe', return_value=raw):
            item = i.invite_membership(actor=actor or self.admin, org_id=org.pk, expected_org_revision=org.revision,
                membership_id=member.pk, expected_revision=member.revision, origin='https://testserver')
        return item, raw

    def activate(self, member):
        token, raw = self.issue(member)
        i.accept_invitation(raw=raw, password=PASSWORD)
        member.refresh_from_db()
        return User.objects.get(pk=member.identity.user_id)

    def lead(self, org=None):
        org = org or self.portal_org()
        member = self.membership(org)
        user = self.activate(member)
        org.refresh_from_db()
        return org, member, user

    def customer_login(self, user):
        self.client.force_login(user, backend=BACKEND)

    def submit_campaign(self, org, campaign, actor, action='submit'):
        org.refresh_from_db(); campaign.refresh_from_db()
        return campaign_submission(actor=actor, org_id=org.pk, expected_org_revision=org.revision,
            campaign_id=campaign.pk, expected_revision=campaign.revision, action=action)

    def save_customer_need(self, org, actor, data, need=None):
        org.refresh_from_db()
        if need: need.refresh_from_db()
        return s.save_hiring_draft(actor=actor, org_id=org.pk, expected_org_revision=org.revision, data=data,
            need_id=need.pk if need else None, expected_need_revision=need.revision if need else None,
            expected_working_id=need.working_revision_id if need else None, why=WHY)


class ActivationTests(PortalFixtures, TestCase):
    def test_system_delivery_events_render_in_existing_staff_customer_history(self):
        org = self.portal_org(); member = self.membership(org)
        with self.captureOnCommitCallbacks(execute=True):
            self.issue(member)
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        for tab in ('overview', 'history', 'access'):
            response = self.client.get(reverse('customers:workspace', args=[org.pk, tab]))
            self.assertEqual(response.status_code, 200)
        self.assertContains(self.client.get(reverse('customers:workspace', args=[org.pk, 'history'])), 'System')

    def test_link_identity_and_consumed_outcome_cannot_be_rewritten(self):
        org = self.portal_org(); member = self.membership(org); item, raw = self.issue(member)
        another = self.membership(org, role='member')
        self.db_rejects(lambda: Token.objects.filter(pk=item.pk).update(membership=another))
        i.accept_invitation(raw=raw, password=PASSWORD)
        self.db_rejects(lambda: Token.objects.filter(pk=item.pk).update(consumed_at=None))

    def test_activation_verifies_identity_and_accepts_one_reserved_seat(self):
        org = self.portal_org(); member = self.membership(org)
        before = s.seat_usage(org)
        item, raw = self.issue(member)
        self.assertNotIn(raw, json.dumps(list(AuditLog.objects.values('reason', 'new_data'))))
        self.assertNotEqual(item.token_digest, raw)
        i.accept_invitation(raw=raw, password=PASSWORD)
        member.refresh_from_db(); item.refresh_from_db()
        self.assertIsNotNone(member.accepted_at); self.assertIsNotNone(item.consumed_at)
        self.assertEqual(s.seat_usage(org), before)
        user = authenticate(customer_email=member.identity.email, password=PASSWORD)
        self.assertEqual(user.pk, member.identity.user_id)
        self.assertIsNone(authenticate(username=user.username, password=PASSWORD))
        self.assertFalse(user.is_staff or user.is_superuser or user.groups.exists())
        self.assertEqual(user.email, '')
        with self.assertRaises(ValidationError): i.accept_invitation(raw=raw, password=PASSWORD)

    def test_invitation_expiry_reserves_seat_without_granting_access(self):
        org = self.portal_org(); member = self.membership(org); item, raw = self.issue(member)
        with patch('django.utils.timezone.now', return_value=item.expires_at + timedelta(seconds=1)):
            with self.assertRaises(ValidationError): i.accept_invitation(raw=raw, password=PASSWORD)
        member.refresh_from_db(); self.assertIsNone(member.accepted_at)
        self.assertEqual(s.seat_usage(org)['lead'], 1)

    def test_resend_revokes_old_link_and_does_not_reserve_twice(self):
        org = self.portal_org(); member = self.membership(org)
        _, old = self.issue(member); _, new = self.issue(member)
        with self.assertRaises(ValidationError): i.valid_token(old, 'invite')
        self.assertIsNotNone(i.valid_token(new, 'invite'))
        self.assertEqual(s.seat_usage(org)['lead'], 1)

    def test_revocation_retains_reservation_and_primary_lead(self):
        org = self.portal_org(); member = self.membership(org); _, raw = self.issue(member)
        org.refresh_from_db()
        i.revoke_invitation(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision, membership_id=member.pk, expected_revision=member.revision)
        with self.assertRaises(ValidationError): i.valid_token(raw, 'invite')
        org.refresh_from_db(); self.assertEqual(org.primary_lead_id, member.pk)
        self.assertEqual(s.seat_usage(org)['lead'], 1)

    def test_existing_identity_joins_another_company_only_on_acceptance(self):
        a, member, user = self.lead()
        b = self.portal_org(name='SYNTHETIC second company')
        second = self.membership(b, identity=member.identity)
        _, raw = self.issue(second)
        self.assertEqual([m.organization_id for m in eligible_memberships(user)], [a.pk])
        with self.assertRaises(PermissionDenied): i.accept_invitation(raw=raw, password=PASSWORD)
        with self.assertRaises(PermissionDenied): i.accept_invitation(raw=raw, actor=self.admin)
        password_hash = user.password
        i.accept_invitation(raw=raw, actor=user)
        user.refresh_from_db(); self.assertEqual(user.password, password_hash)
        self.assertEqual(set(m.organization_id for m in eligible_memberships(user)), {a.pk, b.pk})

    def test_delivery_capture_and_failure_are_explicit_without_provider_secrets(self):
        org = self.portal_org(); member = self.membership(org)
        with self.captureOnCommitCallbacks(execute=True): item, raw = self.issue(member)
        item.refresh_from_db(); self.assertEqual(item.delivery, 'captured')
        self.assertIn(raw, mail.outbox[-1].body)
        with patch('apps.customers.invitations.send_mail', side_effect=RuntimeError('PRIVATE PROVIDER SECRET')):
            with self.captureOnCommitCallbacks(execute=True): failed, _ = self.issue(member)
        failed.refresh_from_db(); self.assertEqual(failed.delivery, 'failed')
        self.assertFalse(AuditLog.objects.filter(reason__contains='PRIVATE PROVIDER').exists())

    def test_weak_password_cannot_activate(self):
        org = self.portal_org(); member = self.membership(org); _, raw = self.issue(member)
        with self.assertRaises(ValidationError): i.accept_invitation(raw=raw, password='123')
        member.identity.refresh_from_db(); self.assertIsNone(member.identity.activated_at)

    def test_customer_cannot_gain_staff_flags_grants_or_participant_link_after_activation(self):
        org, member, user = self.lead()
        for data in ({'is_staff': True}, {'is_superuser': True}, {'email': 'unsafe@example.test'}):
            self.db_rejects(lambda data=data: User.objects.filter(pk=user.pk).update(**data))
        self.db_rejects(lambda: user.groups.add(self.manager.groups.first()))
        participant = Participant.objects.create(first_name='SYNTHETIC')
        self.db_rejects(lambda: ParticipantAccount.objects.create(user=user, participant=participant, login_email='unsafe@example.test'))
        self.db_rejects(lambda: CustomerIdentity.objects.filter(pk=member.identity_id).update(user=self.admin))
        self.db_rejects(lambda: CustomerIdentity.objects.filter(pk=member.identity_id).update(email='new@example.test'))

    def test_inactive_company_and_suspended_membership_invitations_cannot_activate(self):
        org = self.portal_org(); member = self.membership(org); _, raw = self.issue(member)
        s.save_organization(actor=self.admin, org_id=org.pk, expected_revision=org.revision, data={'status': 'inactive'}, why=WHY)
        with self.assertRaises(ValidationError): i.accept_invitation(raw=raw, password=PASSWORD)


class AuthenticationTests(PortalFixtures, TestCase):
    def test_unified_login_all_account_types_and_safe_legacy_urls(self):
        for realm in ('staff', 'customer', 'participant'):
            response = self.client.get(reverse('accounts:login'), {'account_type': realm})
            self.assertEqual(response.status_code, 200)
            for label in ('BTW Staff', 'Customer', 'Participant'):
                self.assertContains(response, label)
        self.assertEqual(self.client.get(reverse('participants:login')).status_code, 200)

    def test_identical_email_realms_are_distinct_and_do_not_convert_or_merge(self):
        org = self.portal_org()
        self.admin.email = 'shared@example.test'; self.admin.save(update_fields=['email'])
        participant_user = User.objects.create_user('synthetic-same-email-participant', email='shared@example.test', password=PASSWORD)
        person = Participant.objects.create(first_name='SYNTHETIC', contact_email='shared@example.test')
        account = ParticipantAccount.objects.create(user=participant_user, participant=person, login_email='shared@example.test', status='active', email_verified_at=timezone.now())
        member = s.save_membership(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision, role='lead', designate_primary=True,
            identity_data={'first_name': 'SYNTHETIC', 'email': 'shared@example.test'}, why=WHY)
        customer = self.activate(member)
        self.assertEqual(authenticate(customer_email='SHARED@example.test', password=PASSWORD).pk, customer.pk)
        self.assertEqual(authenticate(participant_email='shared@example.test', password=PASSWORD).pk, participant_user.pk)
        self.assertEqual(len({customer.pk, self.admin.pk, participant_user.pk}), 3)
        self.assertIsNone(authenticate(customer_email='shared@example.test', password='Synthetic-test-password-123'))

    def test_customer_login_rejects_unsafe_return_and_routes_to_portal(self):
        org, member, user = self.lead()
        for destination in ('https://evil.example/', '//evil.example/', '/admin/', '/internal/', '/customer/auth/logout/'):
            self.client.logout()
            response = self.client.post(reverse('accounts:login'), {'account_type':'customer', 'email':member.identity.email, 'password':PASSWORD, 'next':destination})
            self.assertRedirects(response, reverse('customer_portal:home'), fetch_redirect_response=False)

    def test_customer_and_participant_cannot_use_staff_login(self):
        org, member, user = self.lead()
        response = self.client.post(reverse('accounts:login'), {'username':user.username, 'password':PASSWORD})
        self.assertEqual(response.status_code, 200); self.assertNotIn('_auth_user_id', self.client.session)

    def test_failed_login_and_recovery_do_not_enumerate_accounts(self):
        org, member, user = self.lead()
        bodies = []
        for email in (member.identity.email, 'not-a-customer@example.test'):
            bodies.append(str(self.client.post(reverse('accounts:login'), {'account_type':'customer','email':email,'password':'wrong'}).context['form'].non_field_errors()))
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(self.client.post(reverse('customer_portal:recovery'), {'email':member.identity.email}).content,
                         self.client.post(reverse('customer_portal:recovery'), {'email':'missing@example.test'}).content)

    def test_login_throttle_denies_correct_password_after_limit(self):
        org, member, user = self.lead()
        with patch('apps.customers.portal_auth.rate_limit', return_value=False):
            self.client.post(reverse('accounts:login'), {'account_type':'customer','email':member.identity.email,'password':PASSWORD})
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_csrf_and_post_only_logout(self):
        org, member, user = self.lead()
        client = Client(enforce_csrf_checks=True); client.force_login(user, backend=BACKEND)
        self.assertEqual(client.get(reverse('customer_portal:logout')).status_code, 405)
        self.assertEqual(client.post(reverse('customer_portal:logout')).status_code, 403)
        self.assertEqual(client.post(reverse('customer_portal:switch'), {'organization':str(org.pk)}).status_code, 403)
        self.assertEqual(client.post(reverse('accounts:login'), {}).status_code, 403)

    def test_reset_revokes_sessions_and_links_and_is_single_use(self):
        org, member, user = self.lead(); self.customer_login(user)
        raw = uuid.uuid4().hex * 2
        with patch('apps.customers.invitations.secrets.token_urlsafe', return_value=raw):
            i.request_recovery(email=member.identity.email, origin='https://testserver')
        i.reset_password(raw=raw, password=PASSWORD + 'New')
        self.assertEqual(self.client.get(reverse('customer_portal:home')).status_code, 302)
        with self.assertRaises(ValidationError): i.reset_password(raw=raw, password=PASSWORD)
        with self.assertRaises(PermissionDenied): require_membership(user, org.pk)
        self.assertIsNotNone(authenticate(customer_email=member.identity.email, password=PASSWORD+'New'))

    def test_reset_expiry_and_wrong_purpose(self):
        org, member, user = self.lead(); raw = uuid.uuid4().hex*2
        with patch('apps.customers.invitations.secrets.token_urlsafe', return_value=raw): i.request_recovery(email=member.identity.email, origin='https://testserver')
        item = Token.objects.get(token_digest=i.digest(raw))
        with patch('django.utils.timezone.now', return_value=item.expires_at + timedelta(seconds=1)):
            with self.assertRaises(ValidationError): i.reset_password(raw=raw, password=PASSWORD+'New')
        with self.assertRaises(ValidationError): i.valid_token(raw, 'invite')

    def test_cross_realm_routes_are_denied(self):
        org, member, user = self.lead(); self.customer_login(user)
        for url in ('/internal/', '/internal/customers/', '/participants/profile/', '/admin/'):
            self.assertIn(self.client.get(url).status_code, (302,403,404))
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('customer_portal:home')).status_code, 403)


class MembershipTests(PortalFixtures, TestCase):
    def test_pending_join_does_not_disclose_another_company_identity_name(self):
        a, lead, user = self.lead()
        b, other_lead, other_user = self.lead(self.portal_org(name='SYNTHETIC other company'))
        CustomerIdentity.objects.filter(pk=other_lead.identity_id).update(first_name='PRIVATE-OTHER-COMPANY-NAME')
        a.refresh_from_db()
        i.invite_member(actor=user, org_id=a.pk, expected_org_revision=a.revision, email=other_lead.identity.email, first_name='Invited', origin='https://testserver')
        self.customer_login(user)
        response = self.client.get(reverse('customer_portal:workspace', args=['users']))
        self.assertNotContains(response, 'PRIVATE-OTHER-COMPANY-NAME')
        self.assertContains(response, 'Invited Member')

    def test_lead_invites_read_only_member_with_single_reservation(self):
        org, lead, user = self.lead(); org.refresh_from_db()
        item = i.invite_member(actor=user, org_id=org.pk, expected_org_revision=org.revision, email='member@example.test', first_name='SYNTHETIC', origin='https://testserver')
        m=item.membership
        self.assertEqual(m.role,'member'); self.assertFalse(m.manage_needs or m.manage_campaigns)
        self.assertIsNone(m.accepted_at); self.assertEqual(s.seat_usage(org)['member'],1)
        org.refresh_from_db()
        with self.assertRaises(ValidationError): i.invite_member(actor=user, org_id=org.pk, expected_org_revision=org.revision, email='member@example.test', first_name='SYNTHETIC', origin='https://testserver')

    def test_lead_cannot_create_leads_or_transfer_primary_or_change_plan(self):
        org, lead, user=self.lead()
        with self.assertRaises(PermissionDenied): s.save_membership(actor=user,org_id=org.pk,expected_org_revision=org.revision,role='lead',identity_data={'first_name':'Denied','email':'denied@example.test'},why=WHY)
        with self.assertRaises(PermissionDenied): s.transfer_primary(actor=user,org_id=org.pk,expected_org_revision=org.revision,target_id=lead.pk,why=WHY)
        with self.assertRaises(PermissionDenied): s.save_access_plan(actor=user,org_id=org.pk,expected_org_revision=org.revision,expected_revision=org.access_plan.revision,data={'member_limit':100},why=WHY)
        with self.assertRaises(PermissionDenied): i.manage_member(actor=user,org_id=org.pk,expected_org_revision=org.revision,membership_id=lead.pk,expected_revision=lead.revision,state='suspended',manage_needs=True,manage_campaigns=True)

    def test_independent_member_permissions_and_organization_read_access(self):
        org, lead, user=self.lead(); member=self.membership(org,role='member'); member_user=self.activate(member)
        self.assertTrue(p.needs(member_user,org.pk)==[])
        with self.assertRaises(PermissionDenied): require_membership(member_user,org.pk,action='needs')
        with self.assertRaises(PermissionDenied): require_membership(member_user,org.pk,action='campaigns')
        org.refresh_from_db()
        i.manage_member(actor=user,org_id=org.pk,expected_org_revision=org.revision,membership_id=member.pk,expected_revision=member.revision,state='enabled',manage_needs=True,manage_campaigns=False)
        self.assertEqual(require_membership(member_user,org.pk,action='needs').pk,member.pk)
        with self.assertRaises(PermissionDenied): require_membership(member_user,org.pk,action='campaigns')
        org.refresh_from_db(); member.refresh_from_db()
        i.manage_member(actor=user,org_id=org.pk,expected_org_revision=org.revision,membership_id=member.pk,expected_revision=member.revision,state='enabled',manage_needs=False,manage_campaigns=True)
        self.assertEqual(require_membership(member_user,org.pk,action='campaigns').pk,member.pk)
        with self.assertRaises(PermissionDenied): require_membership(member_user,org.pk,action='needs')
        with self.assertRaises(PermissionDenied): require_membership(member_user,org.pk,action='members')

    def test_cap_reduction_preserves_existing_access_but_denies_increases(self):
        org, lead, user=self.lead(); member=self.membership(org,role='member'); mu=self.activate(member)
        self.plan(org,member_limit=0)
        self.assertEqual(require_membership(mu,org.pk).pk,member.pk)
        with self.assertRaises(ValidationError): i.invite_member(actor=user,org_id=org.pk,expected_org_revision=org.revision,email='denied@example.test',first_name='Denied',origin='https://testserver')
        self.assertEqual(s.seat_usage(org)['member'],1)

    def test_suspension_is_company_specific_and_stale_selection_fails_closed(self):
        a, lead, user=self.lead(); b=self.portal_org(name='SYNTHETIC company B'); second=self.membership(b,identity=lead.identity)
        _,raw=self.issue(second); i.accept_invitation(raw=raw,actor=user)
        self.customer_login(user); session=self.client.session; session['customer_organization']=str(a.pk);session.save()
        a.refresh_from_db(); s.save_organization(actor=self.admin,org_id=a.pk,expected_revision=a.revision,data={'status':'inactive'},why=WHY)
        self.assertEqual(self.client.get(reverse('customer_portal:home')).status_code,403)
        self.assertEqual(self.client.post(reverse('customer_portal:switch'),{'organization':str(b.pk)}).status_code,302)
        self.assertEqual(self.client.get(reverse('customer_portal:home')).status_code,200)
        user.refresh_from_db(); self.assertTrue(user.is_active)

    def test_related_organizations_do_not_inherit_access(self):
        a, lead, user=self.lead(); b=self.portal_org(name='SYNTHETIC related company')
        s.save_relationship(actor=self.admin,org_id=a.pk,target_id=b.pk,kind='connected',active=True,expected_org_revision=a.revision,why=WHY)
        with self.assertRaises(PermissionDenied): require_membership(user,b.pk)
        self.customer_login(user)
        self.assertEqual(self.client.post(reverse('customer_portal:switch'),{'organization':str(b.pk)}).status_code,403)

    def test_member_revocation_immediately_blocks_stale_browser_and_service(self):
        org, lead, user=self.lead(); member=self.membership(org,role='member'); mu=self.activate(member); self.customer_login(mu)
        org.refresh_from_db()
        i.manage_member(actor=user,org_id=org.pk,expected_org_revision=org.revision,membership_id=member.pk,expected_revision=member.revision,state='removed',manage_needs=False,manage_campaigns=False)
        self.assertEqual(self.client.get(reverse('customer_portal:home')).status_code,403)
        with self.assertRaises(PermissionDenied): require_membership(mu,org.pk)
        mu.refresh_from_db(); self.assertTrue(mu.is_active)


class CollaborationTests(PortalFixtures, TestCase):
    def test_returned_items_and_campaign_submissions_surface_without_private_reasons(self):
        org, lead, user = self.lead(); need = self.draft(org)
        self.transition(org, need, 'submit'); self.transition(org, need, 'return')
        c = self.campaign(org); self.submit_campaign(org, c, user)
        self.customer_login(user)
        response = self.client.get(reverse('customer_portal:home'))
        self.assertContains(response, 'Changes requested')
        self.assertContains(response, 'Submitted')
        self.assertNotContains(response, WHY)

    def test_closed_need_and_forged_campaign_approval_routes_stay_denied(self):
        org, lead, user = self.lead(); need = self.approved_need(org)
        need = self.draft(org, {'worker_quantity': 3}, need)
        org.refresh_from_db(); need.refresh_from_db()
        s.need_status(actor=self.admin, org_id=org.pk, need_id=need.pk, expected_org_revision=org.revision,
                      expected_need_revision=need.revision, action='close', why=WHY)
        with self.assertRaises(ValidationError): self.transition(org, need, 'submit', actor=user)
        c = self.campaign(org); self.customer_login(user)
        self.assertEqual(self.client.get(reverse('customer_portal:campaign-action', args=[c.pk, 'approve_activate'])).status_code, 403)

    def test_need_draft_submit_return_feedback_resubmit_and_effective_immutability(self):
        org,lead,user=self.lead(); payload=self.payload(org)
        need=self.save_customer_need(org,user,payload)
        self.transition(org,need,'submit',actor=user)
        with self.assertRaises(PermissionDenied): self.transition(org,need,'review',actor=user)
        self.transition(org,need,'review'); self.transition(org,need,'approve')
        effective=need.effective_revision_id
        need=self.save_customer_need(org,user,{'pay_minimum':25},need)
        self.transition(org,need,'submit',actor=user)
        org.refresh_from_db();need.refresh_from_db()
        publish_feedback(actor=self.admin,org_id=org.pk,expected_org_revision=org.revision,kind='need',record_id=need.pk,expected_revision=need.revision,body='Please clarify the shift.',action='return')
        need=self.save_customer_need(org,user,{'schedule_details':'Monday-Friday day shift'},need)
        self.transition(org,need,'submit',actor=user)
        need.refresh_from_db();self.assertEqual(need.effective_revision_id,effective)
        self.assertEqual(need.working_revision.state,'submitted')
        self.assertEqual(p.feedback(user,org.pk,need_id=need.pk)[0]['body'],'Please clarify the shift.')

    def test_customers_cannot_approve_activate_close_or_change_private_administration(self):
        org,lead,user=self.lead();need=self.draft(org);self.transition(org,need,'submit');self.transition(org,need,'review')
        for action in ('review','approve','return'):
            with self.assertRaises(PermissionDenied): self.transition(org,need,action,actor=user)
        org.refresh_from_db();need.refresh_from_db()
        with self.assertRaises(PermissionDenied): s.need_status(actor=user,org_id=org.pk,need_id=need.pk,expected_org_revision=org.revision,expected_need_revision=need.revision,action='close',why=WHY)
        with self.assertRaises(PermissionDenied): s.update_need_administration(actor=user,org_id=org.pk,need_id=need.pk,expected_org_revision=org.revision,expected_need_revision=need.revision,data={'internal_note':'Forged'},why=WHY)

    def test_stale_hiring_edit_and_disabled_service_are_rejected(self):
        org,lead,user=self.lead();need=self.save_customer_need(org,user,{'title':'Draft'})
        old_org,old_need=org.revision,need.revision
        self.save_customer_need(org,user,{'title':'New draft'},need)
        with self.assertRaises(ValidationError): s.save_hiring_draft(actor=user,org_id=org.pk,expected_org_revision=old_org,need_id=need.pk,expected_need_revision=old_need,expected_working_id=need.working_revision_id,data={'title':'Stale'},why=WHY)
        self.plan(org,workforce_need_creation=False)
        with self.assertRaises(PermissionDenied): self.save_customer_need(org,user,{'title':'Denied'},need)
        self.assertTrue(p.needs(user,org.pk))

    def test_campaign_submission_freeze_return_resubmit_and_approval_invalidation(self):
        org,lead,user=self.lead();org.refresh_from_db()
        c=s.save_campaign(actor=user,org_id=org.pk,expected_org_revision=org.revision,data={'name':'SYNTHETIC campaign','description':'Hiring effort','objectives':'Fill authorized needs'},why=WHY)
        self.submit_campaign(org,c,user)
        org.refresh_from_db();c.refresh_from_db()
        with self.assertRaises(ValidationError): s.save_campaign(actor=user,org_id=org.pk,expected_org_revision=org.revision,campaign_id=c.pk,expected_revision=c.revision,data={'name':'Frozen'},why=WHY)
        with self.assertRaises(PermissionDenied): self.submit_campaign(org,c,user,'return')
        publish_feedback(actor=self.admin,org_id=org.pk,expected_org_revision=org.revision,kind='campaign',record_id=c.pk,expected_revision=c.revision,body='Clarify objectives.',action='return')
        org.refresh_from_db();c.refresh_from_db()
        c=s.save_campaign(actor=user,org_id=org.pk,expected_org_revision=org.revision,campaign_id=c.pk,expected_revision=c.revision,data={'objectives':'Hire welders'},why=WHY)
        self.submit_campaign(org,c,user)
        org.refresh_from_db();c.refresh_from_db()
        s.campaign_action(actor=self.admin,org_id=org.pk,campaign_id=c.pk,expected_org_revision=org.revision,expected_revision=c.revision,action='approve_activate',why=WHY)
        org.refresh_from_db();c.refresh_from_db(); self.assertEqual(c.status,'active')
        c=s.save_campaign(actor=user,org_id=org.pk,expected_org_revision=org.revision,campaign_id=c.pk,expected_revision=c.revision,data={'objectives':'New material scope'},why=WHY)
        self.assertEqual(c.status,'hold');self.assertIsNone(c.approved_planning_version)
        self.assertEqual(c.review_state,'draft')

    def test_member_cannot_call_services_without_specific_management_permission(self):
        org,lead,user=self.lead();member=self.membership(org,role='member');mu=self.activate(member)
        with self.assertRaises(PermissionDenied): self.save_customer_need(org,mu,{'title':'Denied'})
        org.refresh_from_db()
        with self.assertRaises(PermissionDenied): s.save_campaign(actor=mu,org_id=org.pk,expected_org_revision=org.revision,data={'name':'Denied'},why=WHY)
        c=self.campaign(org)
        with self.assertRaises(PermissionDenied): self.submit_campaign(org,c,mu)

    def test_cross_company_ids_fail_in_services_and_routes(self):
        a,lead,user=self.lead();b=self.org();need=self.draft(b);c=self.campaign(b)
        a.refresh_from_db()
        with self.assertRaises(Need.DoesNotExist): self.save_customer_need(a,user,{'title':'Denied'},need)
        self.customer_login(user)
        for name,obj in [('need',need),('campaign',c)]:
            self.assertEqual(self.client.get(reverse('customer_portal:'+name,args=[obj.pk])).status_code,404)

    def test_customer_responses_exclude_private_notes_audit_reasons_and_staff_usernames(self):
        org,lead,user=self.lead();need=self.approved_need(org);c=self.campaign(org)
        org.refresh_from_db();need.refresh_from_db()
        s.update_need_administration(actor=self.admin,org_id=org.pk,need_id=need.pk,expected_org_revision=org.revision,expected_need_revision=need.revision,data={'internal_note':'PRIVATE-NOTE-SECRET','internal_reference':'PRIVATE-REFERENCE-SECRET'},why='PRIVATE-REASON-SECRET')
        self.customer_login(user)
        urls=[reverse('customer_portal:home'),reverse('customer_portal:need',args=[need.pk]),reverse('customer_portal:campaign',args=[c.pk])]+[reverse('customer_portal:workspace',args=[tab]) for tab in ('company','needs','campaigns','users','access','history')]
        for url in urls:
            response=self.client.get(url);self.assertEqual(response.status_code,200)
            for secret in ('PRIVATE-NOTE-SECRET','PRIVATE-REFERENCE-SECRET','PRIVATE-REASON-SECRET',self.admin.username):self.assertNotContains(response,secret)

    def test_feedback_is_explicit_append_only_and_cannot_cross_tenants(self):
        org,lead,user=self.lead();need=self.draft(org);org.refresh_from_db();need.refresh_from_db()
        feedback=publish_feedback(actor=self.admin,org_id=org.pk,expected_org_revision=org.revision,kind='need',record_id=need.pk,expected_revision=need.revision,body='Visible feedback')
        self.assertTrue(AuditLog.objects.filter(pk=feedback.audit_id,action='customer.feedback.published').exists())
        self.db_rejects(lambda: CustomerFeedback.objects.filter(pk=feedback.pk).update(body='Rewrite'))
        other=self.org()
        self.db_rejects(lambda: CustomerFeedback.objects.filter(pk=feedback.pk).update(organization=other))
        with self.assertRaises(PermissionDenied): publish_feedback(actor=user,org_id=org.pk,expected_org_revision=org.revision,kind='need',record_id=need.pk,expected_revision=need.revision,body='Forged')

    def test_member_form_forged_permissions_and_staff_routes_are_denied(self):
        org,lead,user=self.lead();self.customer_login(user)
        response=self.client.post(reverse('customer_portal:invite'),{'org_revision':org.revision,'first_name':'Fake','email':'fake@example.test','role':'lead','manage_needs':True})
        self.assertEqual(response.status_code,200);self.assertContains(response,'Unsupported submitted fields')
        self.assertEqual(self.client.get(reverse('customer_portal:staff-members',args=[org.pk])).status_code,403)

    def test_native_customer_data_admin_is_not_exposed(self):
        org,lead,user=self.lead();self.customer_login(user)
        self.assertEqual(self.client.get('/admin/customers/customeridentity/').status_code,302)


class DirectoryPrivacyTests(PortalFixtures, TestCase):
    def test_all_future_entitlements_still_disclose_nothing(self):
        org,lead,user=self.lead();self.plan(org,directory_search=True,candidate_matching=True,expanded_candidate_profile=True,authorized_candidate_contact=True)
        Participant.objects.create(first_name='PRIVATE-PARTICIPANT-SENTINEL',contact_email='private@example.test')
        self.assertFalse(directory.visibility_decision(actor=user,organization_id=org.pk).allowed)
        for actor in (user,self.admin,AnonymousUser()):
            with self.assertRaises(PermissionDenied): directory.safe_query(actor=actor,organization_id=org.pk,dimensions={'trade_occupation':'Welding'})
            with self.assertRaises(PermissionDenied): directory.safe_projection(actor=actor,organization_id=org.pk,participant=object())
        self.customer_login(user)
        for tab in ('overview','company','needs','campaigns','users','access','history'):
            response=self.client.get(reverse('customer_portal:workspace',args=[tab]))
            self.assertNotContains(response,'PRIVATE-PARTICIPANT-SENTINEL');self.assertNotContains(response,'private@example.test')
        self.assertEqual(self.client.get('/customer/participants/').status_code,404)

    def test_future_search_contract_is_explicit_and_does_not_query_participants(self):
        self.assertEqual(len(directory.FUTURE_SEARCH_DIMENSIONS),6)
        with self.assertNumQueries(0):
            self.assertFalse(directory.visibility_decision(actor=None,organization_id=None).allowed)
            with self.assertRaises(PermissionDenied): directory.safe_query(actor=None,organization_id=None)
