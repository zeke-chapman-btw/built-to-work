"""Employer UI uses only exact-release projections and unchanged write authority."""
from urllib.parse import urlencode
from django.test import TestCase
from django.urls import reverse
from apps.customers.test_portal import BACKEND
from apps.recruiting.test_employer import EmployerFixtures
from apps.recruiting import employer
from . import test_portal as portal_tests
from . import services
from .models import Interaction


class EmployerPresentationUxTests(EmployerFixtures, TestCase):
    setUp=portal_tests.EmployerCrmTests.setUp
    url=portal_tests.EmployerCrmTests.url
    data=portal_tests.EmployerCrmTests.data
    record=portal_tests.EmployerCrmTests.record

    def candidate_url(self):
        return reverse('customer_portal:recruiting-candidate',args=[self.grant.pk])

    def test_header_summary_and_editor_use_approved_candidate_projection(self):
        row=self.record()
        response=self.client.get(self.candidate_url())
        self.assertEqual(response.status_code,200)
        projected=response.context['candidate']['candidate']
        self.assertContains(response,projected['name'])
        self.assertContains(response,'href="#company-interactions"')
        self.assertContains(response,'Last company interaction')
        self.assertEqual(response.context['crm_company_last']['id'],row.pk)
        self.assertIsInstance(response.context['crm_company_last'],dict)
        editor=self.client.get(self.url())
        self.assertContains(editor,projected['name'])
        self.assertNotContains(editor,'responsible_staff')
        self.assertNotContains(editor,'payload_fingerprint')

    def test_recorded_next_action_excludes_archived_and_changes_no_recruiting_decision(self):
        before=(self.candidate.stage_id,self.candidate.outcome,self.candidate.revision)
        response=self.client.post(self.url(),self.data(next_step='SYNTHETIC discuss the site visit',follow_up_on='2026-12-01'))
        self.assertEqual(response.status_code,302)
        page=self.client.get(self.candidate_url())
        self.assertContains(page,'SYNTHETIC discuss the site visit')
        self.candidate.refresh_from_db()
        self.assertEqual(before,(self.candidate.stage_id,self.candidate.outcome,self.candidate.revision))
        row=Interaction.objects.get(audience='employer')
        services.archive(actor=self.customer,organization_id=self.organization.pk,interaction_id=row.pk,expected_revision=1,why='SYNTHETIC archive')
        page=self.client.get(self.candidate_url(),{'archived':'1'})
        self.assertIsNone(page.context['crm_company_last'])
        self.assertIsNone(page.context['crm_company_next'])

    def test_normal_member_read_only_then_explicit_responding_authority(self):
        member=self.membership(self.organization,role='member');user=self.activate(member)
        self.client.force_login(user,backend=BACKEND)
        page=self.client.get(self.candidate_url())
        self.assertEqual(page.status_code,200)
        self.assertFalse(page.context['crm_can_record'])
        self.assertNotContains(page,'>Log interaction</a>')
        self.assertEqual(self.client.get(self.url()).status_code,403)
        employer.member_permission(actor=self.admin,disclosure_id=self.grant.pk,membership_id=member.pk,
            can_respond=True,expected_revision=0,why='SYNTHETIC scoped response permission')
        self.assertTrue(self.client.get(self.candidate_url()).context['crm_can_record'])
        self.assertEqual(self.client.get(self.url()).status_code,200)

    def test_foreign_company_cannot_open_refined_candidate_or_editor(self):
        _,_,user=self.lead();self.client.force_login(user,backend=BACKEND)
        self.assertEqual(self.client.get(self.candidate_url()).status_code,403)
        self.assertEqual(self.client.get(self.url()).status_code,403)

    def test_suspended_membership_cannot_open_refined_candidate(self):
        member=self.membership(self.organization,role='member');user=self.activate(member)
        self.client.force_login(user,backend=BACKEND)
        member.state='suspended';member.save(update_fields=['state'])
        self.assertEqual(self.client.get(self.candidate_url()).status_code,403)
        self.assertEqual(self.client.get(self.url()).status_code,403)

    def test_revoked_disclosure_cannot_open_refined_candidate(self):
        employer.revoke(actor=self.admin,disclosure_id=self.grant.pk,expected_revision=self.grant.revision,why='SYNTHETIC revoke')
        self.assertEqual(self.client.get(self.candidate_url()).status_code,403)
        self.assertEqual(self.client.get(self.url()).status_code,403)

    def test_withdrawn_consent_cannot_open_refined_candidate(self):
        employer.record_withdrawal(actor=self.admin,participant_id=self.person.pk,why='SYNTHETIC recorded withdrawal')
        self.assertEqual(self.client.get(self.candidate_url()).status_code,403)
        self.assertEqual(self.client.get(self.url()).status_code,403)

    def test_candidate_pagination_origin_is_retained_and_external_return_rejected(self):
        back=self.candidate_url()+'?'+urlencode({'archived':'1','activity_page':'2'})
        page=self.client.get(self.url()+'?'+urlencode({'return_to':back}))
        self.assertEqual(page.context['back'],back+'#company-interactions')
        response=self.client.post(self.url()+'?'+urlencode({'return_to':back}),self.data())
        self.assertEqual(response.url,back+'#company-interactions')
        page=self.client.get(self.url(),{'return_to':'https://foreign.example/private'})
        self.assertEqual(page.context['back'],self.candidate_url()+'#company-interactions')
