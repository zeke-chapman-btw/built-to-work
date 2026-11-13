from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from .test_portal import PortalFixtures
from .portal_presentation import effective_access, need_prerequisites, record_restriction
from .portal_forms import CampaignForm, NeedForm
from . import services as s, invitations as i
from .models import WorkforceNeedRevision, Campaign

class PortalRefinementTests(PortalFixtures, TestCase):
    def test_campaign_label_does_not_change_need_or_payload_fields(self):
        org, member, user = self.lead()
        self.assertEqual(CampaignForm(actor=user,org_id=org.pk).fields['description'].label,'Campaign description')
        self.assertEqual(NeedForm(actor=user,org_id=org.pk).fields['description'].label,'Role description')

    def test_edit_returns_to_record_new_returns_to_collection_and_ignores_external_return(self):
        org, member, user = self.lead(); need=self.draft(org,data={'title':'SYNTHETIC draft'})
        org.refresh_from_db()
        campaign=s.save_campaign(actor=self.admin,org_id=org.pk,expected_org_revision=org.revision,
            data={'name':'SYNTHETIC planning'},why='Synthetic navigation verification')
        self.customer_login(user)
        for kind,obj in [('need',need),('campaign',campaign)]:
            with self.subTest(kind=kind):
                response=self.client.get(reverse('customer_portal:'+kind+'-edit',args=[obj.pk]),{'next':'https://outside.example/'})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.context['cancel_url'],reverse('customer_portal:'+kind,args=[obj.pk]))
                self.assertContains(response,'data-unsaved-guard')
                response=self.client.get(reverse('customer_portal:'+kind+'-create'))
                self.assertEqual(response.context['cancel_url'],reverse('customer_portal:workspace',args=['needs' if kind=='need' else 'campaigns']))

    def test_seven_groups_chooser_and_invalid_post_preserve_values(self):
        org, member, user=self.lead();self.customer_login(user)
        response=self.client.post(reverse('customer_portal:need-create'),{'org_revision':org.revision,'title':'Keep my typed role','worker_quantity':'invalid'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Keep my typed role')
        self.assertContains(response,'data-step-dot')
        self.assertContains(response,'data-has-errors')
        self.assertContains(response,'data-error-summary')
        self.assertEqual(len(response.context['form'].groups),7)

    def test_access_reasons_match_independent_flags_and_need_entitlement(self):
        org, lead_member, lead=self.lead(); member=self.membership(org,role='member'); user=self.activate(member)
        for needs,campaigns in [(False,False),(True,False),(False,True),(True,True)]:
            org.refresh_from_db();member.refresh_from_db()
            i.manage_member(actor=lead,org_id=org.pk,expected_org_revision=org.revision,membership_id=member.pk,
                expected_revision=member.revision,state='enabled',manage_needs=needs,manage_campaigns=campaigns)
            for entitlement in (True,False,None):
                self.plan(org,workforce_need_creation=entitlement)
                result=effective_access(user,org.pk)
                self.assertEqual(result['needs'],needs and entitlement is True)
                self.assertEqual(result['campaigns'],campaigns)
                self.assertFalse(result['members'])
        self.assertTrue(effective_access(lead,org.pk)['members'])

    def test_prerequisites_are_read_only_and_do_not_invent_contact_or_location(self):
        org,member,user=self.lead(); revision=WorkforceNeedRevision(organization=org)
        before=org.contacts.count(); result=need_prerequisites(org,revision)
        self.assertEqual(result['contacts'],0);self.assertEqual(org.contacts.count(),before)
        self.assertFalse(result['contact_selected'])
        self.assertIn('Hiring contact',[x['label'] for x in result['missing']])
        need=self.draft(org,data={'title':'Incomplete allowed'})
        self.assertEqual(need.working_revision.state,'draft')
        self.assertEqual(org.locations.count(),0)

    def test_prerequisite_modes_reuse_submission_matrix(self):
        org,member,user=self.lead(); payload=self.payload(org)
        for mode,updates in [('remote',{'location_details':'Remote within Georgia'}),
                ('custom',{'location_name':'SYNTHETIC site','address_line1':'Example Road','city':'Example City','location_state':'GA','country':'United States'})]:
            data=dict(payload,location_mode=mode,location=None,**updates)
            need=self.draft(org,data=data)
            setup=need_prerequisites(org,need.working_revision)
            self.assertEqual(setup['missing'],[])
            self.assertEqual(setup['location_mode'],mode)

    def test_record_reasons_do_not_modify_effective_or_frozen_state(self):
        org,member,user=self.lead(); need=self.draft(org)
        self.transition(org,need,'submit');need.refresh_from_db()
        revision=need.working_revision_id
        self.assertIn('frozen',record_restriction(need,'need'))
        self.customer_login(user)
        response=self.client.get(reverse('customer_portal:need-edit',args=[need.pk]))
        self.assertEqual(response.status_code,403)
        need.refresh_from_db();self.assertEqual(need.working_revision_id,revision)

    def test_foreign_org_is_not_a_presentation_backdoor(self):
        org,member,user=self.lead(); other=self.portal_org(name='SYNTHETIC other')
        with self.assertRaises(PermissionDenied):effective_access(user,other.pk)
