"""Opportunity presentation preserves real associations and separate permissions."""
from django.test import TestCase
from django.urls import reverse
from apps.customers.models import Campaign
from apps.recruiting.models import OpportunityCollaboration
from apps.recruiting.test_employer import EmployerFixtures
from apps.recruiting import workflow

class ParticipantConnectionsPresentationTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.url=reverse('participants:staff-profile', args=[self.person.pk])+'?section=recruiting'

    def test_only_actual_need_campaign_is_present_and_title_stage_are_not_repeated(self):
        campaign=Campaign.objects.create(organization=self.organization,name='SYNTHETIC actual need campaign')
        absent=self.client.get(self.url)
        self.assertNotContains(absent,campaign.name)
        self.need.campaign=campaign;self.need.save(update_fields=['campaign'])
        response=self.client.get(self.url)
        self.assertContains(response,campaign.name)
        self.assertContains(response,reverse('customers:campaign',args=[self.organization.pk,campaign.pk]))
        self.assertContains(response,reverse('customers:need',args=[self.organization.pk,self.need.pk]))
        self.assertContains(response,self.need.title,count=1)
        self.assertContains(response,self.candidate.stage.label,count=1)
        self.assertContains(response,'Recruiting management',count=1)
        self.assertNotContains(response,'Open recruiting management')
        self.assertNotContains(response,'>Position</dt>')
        self.assertNotContains(response,'>Opportunity stage</dt>')

    def test_delegated_recruiter_retains_connection_without_customer_management_links(self):
        campaign=Campaign.objects.create(organization=self.organization,name='SYNTHETIC delegated campaign')
        self.need.campaign=campaign;self.need.save(update_fields=['campaign'])
        OpportunityCollaboration.objects.create(opportunity=self.opportunity,user=self.recruiter,can_manage=False,granted_by=self.admin)
        self.client.force_login(self.recruiter,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,campaign.name)
        self.assertContains(response,reverse('recruiting:candidate-detail',args=[self.candidate.pk]))
        for dest in [reverse('customers:workspace',args=[self.organization.pk,'overview']),reverse('customers:campaign',args=[self.organization.pk,campaign.pk]),reverse('customers:need',args=[self.organization.pk,self.need.pk])]:
            self.assertNotContains(response,'href="'+dest+'"')

    def test_hiring_history_is_explicit_and_get_does_not_change_outcome(self):
        row=workflow.transition(actor=self.admin,candidate_id=self.candidate.pk,stage_key='hired',expected_revision=self.candidate.revision,why='SYNTHETIC explicit confirmed fixture outcome',confirm_hired=True)
        before=(row.stage_id,row.active,row.outcome,row.revision)
        response=self.client.get(self.url)
        self.assertContains(response,'Historical record')
        self.assertContains(response,'Retained outcome: Hired')
        row.refresh_from_db()
        self.assertEqual((row.stage_id,row.active,row.outcome,row.revision),before)
