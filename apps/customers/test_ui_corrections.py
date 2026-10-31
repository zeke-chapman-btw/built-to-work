"""Presentation contracts exercised through ordinary authenticated customer views."""
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from .test_portal import PortalFixtures, PASSWORD
from . import invitations, presentation
from .models import EDITABLE_TERM_FIELDS, WorkforceNeed


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CustomerUICorrectionsTests(PortalFixtures, TestCase):
    def test_all_four_member_capabilities_with_ordinary_customer_login(self):
        org, lead, lead_user = self.lead()
        self.plan(org, member_limit=4)
        members = []
        for needs, campaigns in [(False, False), (True, False), (False, True), (True, True)]:
            with self.subTest(needs=needs, campaigns=campaigns):
                member = self.membership(org, role='member')
                self.activate(member)
                org.refresh_from_db(); member.refresh_from_db()
                invitations.manage_member(actor=lead_user, org_id=org.pk, expected_org_revision=org.revision,
                    membership_id=member.pk, expected_revision=member.revision, state='enabled',
                    manage_needs=needs, manage_campaigns=campaigns)
                browser = Client()
                response = browser.post(reverse('accounts:login'), {'account_type':'customer',
                    'email':member.identity.email, 'password':PASSWORD})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(browser.session['_auth_user_backend'],'apps.customers.auth.CustomerAuthenticationBackend')
                overview = browser.get(reverse('customer_portal:home'))
                self.assertEqual(overview.status_code,200)
                self.assertEqual(overview.context['can_needs'],needs)
                self.assertEqual(overview.context['can_campaigns'],campaigns)
                self.assertEqual(browser.get(reverse('customer_portal:need-create')).status_code,200 if needs else 403)
                self.assertEqual(browser.get(reverse('customer_portal:campaign-create')).status_code,200 if campaigns else 403)
                self.assertEqual(browser.get(reverse('customer_portal:invite')).status_code,403)
                self.assertEqual(browser.get(reverse('customers:workspace',args=[org.pk,'users'])).status_code,403)
                members.append(member)
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(reverse('customers:workspace',args=[org.pk,'users']))
        self.assertContains(response,'Invitation: Accepted',count=5)
        self.assertNotContains(response,'Prepared users cannot log in')
        rows={row['member'].pk:row for row in response.context['membership_rows']}
        self.assertEqual([(rows[m.pk]['member'].manage_needs,rows[m.pk]['member'].manage_campaigns) for m in members],
                         [(False,False),(True,False),(False,True),(True,True)])
        self.assertContains(response,'Workforce Needs:')
        self.assertContains(response,'Campaigns:')

    def test_invitation_acceptance_and_membership_are_separate_facts(self):
        org, lead, user=self.lead(); member=self.membership(org,role='member')
        pending=next(row for row in presentation.membership_rows(org) if row['member'].pk==member.pk)
        self.assertEqual(pending['invitation'],'Not invited')
        self.assertEqual(pending['access'],'Awaiting invitation acceptance')
        token, raw=self.issue(member)
        with patch('apps.customers.presentation.timezone.now',return_value=token.expires_at+timedelta(seconds=1)):
            row=next(row for row in presentation.membership_rows(org) if row['member'].pk==member.pk)
            self.assertEqual(row['invitation'],'Expired')
            self.assertEqual(row['access'],'Awaiting invitation acceptance')
        org.refresh_from_db();member.refresh_from_db()
        invitations.revoke_invitation(actor=self.admin,org_id=org.pk,expected_org_revision=org.revision,
            membership_id=member.pk,expected_revision=member.revision)
        self.assertEqual(next(row for row in presentation.membership_rows(org) if row['member'].pk==member.pk)['invitation'],'Revoked')

    def test_hiring_form_retains_every_typed_field_and_reveals_error_groups(self):
        org, lead, user=self.lead();self.customer_login(user)
        response=self.client.get(reverse('customer_portal:need-create'))
        form=response.context['form']
        self.assertTrue(set(EDITABLE_TERM_FIELDS).issubset(form.fields))
        represented={bound.name for group in form.groups for bound in group['fields']}
        self.assertEqual(represented,set(EDITABLE_TERM_FIELDS))
        self.assertEqual(len(form.groups),7)
        self.assertContains(response,'Save Draft')
        self.assertContains(response,'Number of workers needed')
        self.assertNotContains(response,'Worker quantity:')
        org.refresh_from_db()
        response=self.client.post(reverse('customer_portal:need-create'),{'org_revision':org.revision,
            'title':'SYNTHETIC retained input','worker_quantity':'invalid-number','pay_minimum':'invalid-pay'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'SYNTHETIC retained input')
        self.assertTrue(response.context['form'].groups[0]['errors'])
        self.assertTrue(response.context['form'].groups[2]['errors'])
        self.assertContains(response,'id="hiring-group-3" open')
        self.assertEqual(WorkforceNeed.objects.filter(organization=org).count(),0)

    def test_incomplete_draft_save_stays_distinct_from_submission(self):
        org, lead, user=self.lead();self.customer_login(user);org.refresh_from_db()
        response=self.client.post(reverse('customer_portal:need-create'),{'org_revision':org.revision,'title':'SYNTHETIC incomplete'})
        self.assertEqual(response.status_code,302)
        need=WorkforceNeed.objects.get(organization=org)
        self.assertEqual(need.working_revision.state,'draft')
        self.assertIsNone(need.effective_revision_id)

    def test_task_specific_submit_labels_do_not_change_operations(self):
        org, lead, user=self.lead();member=self.membership(org,role='member');self.customer_login(user)
        cases=[('customer_portal:need-create',[],'Save Draft'),('customer_portal:campaign-create',[],'Save Planning'),
               ('customer_portal:invite',[],'Send Invitation'),('customer_portal:member-edit',[member.pk],'Save Member Changes')]
        for name,args,label in cases:
            with self.subTest(name=name):self.assertContains(self.client.get(reverse(name,args=args)),label)

    def test_attention_uses_workflow_state_and_excludes_private_reasons(self):
        org, lead, user=self.lead();need=self.draft(org)
        self.customer_login(user)
        self.assertContains(self.client.get(reverse('customer_portal:home')),'Continue draft')
        self.transition(org,need,'submit')
        self.assertContains(self.client.get(reverse('customer_portal:home')),'Awaiting BTW review')
        self.transition(org,need,'return')
        response=self.client.get(reverse('customer_portal:home'))
        self.assertContains(response,'Review BTW feedback')
        self.assertNotContains(response,'Synthetic Build 13 verification')

    def test_access_denied_is_branded_neutral_and_remains_403(self):
        self.client.force_login(self.worker,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(reverse('customers:directory'))
        self.assertEqual(response.status_code,403)
        self.assertContains(response,'This page is unavailable',status_code=403)
        self.assertNotContains(response,'PermissionDenied',status_code=403)

    def test_attention_keeps_five_needs_and_five_campaigns(self):
        org, lead, user=self.lead()
        for index in range(6):
            self.draft(org,{'title':f'SYNTHETIC Need {index}'})
            self.campaign(org,f'SYNTHETIC Campaign {index}')
        self.customer_login(user)
        response=self.client.get(reverse('customer_portal:home'))
        self.assertEqual(len(response.context['attention']),10)
        self.assertEqual(sum('Need ' in row['label'] for row in response.context['attention']),5)
        self.assertEqual(sum('Campaign ' in row['label'] for row in response.context['attention']),5)
