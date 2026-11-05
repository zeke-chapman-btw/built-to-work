"""Customer dashboard uses current disclosure, membership and existing CRM policy."""
import uuid
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.recruiting.test_employer import EmployerFixtures
from apps.customers.test_portal import BACKEND
from apps.customers.models import Campaign
from apps.recruiting import employer
from apps.recruiting.models import WorkforceRequest, WorkforceRequestPosition


class Post16B1Tests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        self.grant = self.release()
        self.client.force_login(self.customer, backend=BACKEND)

    def workspace(self, tab='overview'):
        return self.client.get(reverse('customer_portal:workspace', args=[tab]))

    def test_four_main_destinations_and_company_secondary_urls_remain_available(self):
        response = self.workspace()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['tabs'], [('overview', 'Dashboard'), ('recruiting', 'Recruiting'),
            ('candidates', 'Candidates'), ('company', 'Company')])
        for tab in ['recruiting', 'needs', 'campaigns', 'company', 'users', 'access', 'history', 'account']:
            response = self.workspace(tab)
            self.assertEqual(response.status_code, 200, tab)
        self.assertContains(self.workspace('account'), self.lead_member.identity.email)
        self.assertContains(self.workspace(), reverse('customer_portal:workspace', args=['account']) + '#help')

    def test_dashboard_has_only_released_candidates_and_truthful_next_actions(self):
        self.need.internal_note = 'SYNTHETIC PRIVATE STAFF CANARY'
        self.need.save()
        response = self.workspace()
        self.assertContains(response, 'Active recruiting')
        self.assertContains(response, 'Released for your review')
        self.assertContains(response, 'Review released candidate')
        self.assertNotContains(response, 'PRIVATE STAFF CANARY')
        rows = response.context['released_candidates']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], str(self.grant.pk))
        self.assertFalse({'stage', 'match_score', 'private_notes', 'participant_id'} & rows[0].keys())
        self.assertEqual(len(response.context['action_required']), 1)

    def test_reported_hire_remains_distinct_from_btw_confirmed_hire(self):
        feedback = self.respond(self.grant, kind='report_hired')
        self.assertContains(self.workspace(), 'awaiting BTW confirmation')
        self.candidate.refresh_from_db()
        employer.confirm_hire(actor=self.admin, feedback_id=feedback.pk,
            expected_candidate_revision=self.candidate.revision, why='SYNTHETIC explicit BTW confirmation')
        self.assertContains(self.workspace(), 'BTW confirmed the reported hire')

    def test_company_permission_to_manage_positions_does_not_grant_candidate_response(self):
        member = self.membership(self.organization, role='member')
        user = self.activate(member)
        member.manage_needs = True
        member.save()
        self.client.force_login(user, backend=BACKEND)
        response = self.workspace()
        self.assertTrue(response.context['can_needs'])
        self.assertFalse(response.context['released_candidates'][0]['can_respond'])
        self.assertEqual(response.context['action_required'], [])
        self.assertContains(response, 'response permission required')
        with self.assertRaises(PermissionDenied):
            self.respond(self.grant, actor=user)

    def test_revoked_membership_withdrawal_and_other_company_fail_closed(self):
        other, other_member, other_user = self.lead()
        self.client.force_login(other_user, backend=BACKEND)
        response = self.workspace()
        self.assertEqual(response.context['released_candidates'], [])
        self.assertNotContains(response, self.need.title)
        self.client.force_login(self.customer, backend=BACKEND)
        employer.record_withdrawal(actor=self.admin, participant_id=self.person.pk, why='SYNTHETIC actual withdrawal')
        self.assertEqual(self.workspace().context['released_candidates'], [])
        # Revoke a non-primary membership; the Primary Lead replacement guard remains intact.
        member = self.membership(self.organization, role='member')
        member_user = self.activate(member)
        self.client.force_login(member_user, backend=BACKEND)
        self.assertEqual(self.workspace().status_code, 200)
        member.state = 'suspended'
        member.save()
        self.assertEqual(self.workspace().status_code, 403)

    def test_workflow_history_has_safe_subjects_and_direct_links(self):
        response = self.workspace('history')
        self.assertContains(response, self.need.title)
        self.assertContains(response, reverse('customer_portal:need', args=[self.need.pk]))

    def test_existing_employer_crm_is_used_in_dashboard_without_copying_records(self):
        from apps.crm.models import Interaction
        data = {'operation_id': str(uuid.uuid4()), 'activity_type': 'call',
            'summary': 'SYNTHETIC B1 company follow-up',
            'occurred_at': timezone.now().strftime('%Y-%m-%dT%H:%M')}
        response = self.client.post(reverse('crm:employer-new', args=[self.grant.pk]), data)
        self.assertEqual(response.status_code, 302)
        record = Interaction.objects.get(audience='employer')
        response = self.workspace()
        self.assertContains(response, data['summary'])
        self.assertContains(response, reverse('crm:employer-detail', args=[self.grant.pk, record.pk]))
        self.assertEqual(Interaction.objects.count(), 1)

    def test_grouped_requests_reference_positions_without_copying_them(self):
        group = WorkforceRequest.objects.create(organization=self.organization, name='SYNTHETIC hiring engagement', created_by=self.admin)
        WorkforceRequestPosition.objects.create(request=group, need=self.need, added_by=self.admin)
        response = self.workspace('recruiting')
        self.assertContains(response, group.name)
        self.assertContains(response, reverse('customer_portal:need', args=[self.need.pk]), count=1)

    def test_response_validation_keeps_the_choice_and_exposes_an_accessible_summary(self):
        from apps.recruiting.models import EmployerCandidateFeedback
        url = reverse('customer_portal:recruiting-candidate', args=[self.grant.pk])
        before = EmployerCandidateFeedback.objects.count()
        response = self.client.post(url, {'operation_id': str(uuid.uuid4()), 'kind': 'feedback', 'message': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enter feedback for BTW.')
        self.assertContains(response, 'data-error-summary')
        self.assertContains(response, 'data-structured-form')
        self.assertEqual(response.context['feedback_form']['kind'].value(), 'feedback')
        self.assertEqual(EmployerCandidateFeedback.objects.count(), before)

    def test_staff_campaign_detail_uses_existing_responsibility_scope(self):
        campaign = Campaign.objects.create(organization=self.organization, name='SYNTHETIC scoped Campaign')
        self.need.campaign = campaign
        self.need.save()
        url = reverse('recruiting:campaign-detail', args=[campaign.pk])
        self.client.force_login(self.recruiter, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(url)
        self.assertContains(response, campaign.name)
        self.assertContains(response, reverse('recruiting:matching', args=[self.opportunity.pk]))
        self.client.force_login(self.customer, backend=BACKEND)
        self.assertEqual(self.client.get(url).status_code, 403)
