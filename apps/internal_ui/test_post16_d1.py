"""Staff attention and profile context share responsibility-scoped recruiting reads."""
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse
from apps.recruiting.test_employer import EmployerFixtures
from apps.recruiting import selection, workflow
from apps.recruiting.read_models import staff_queues, participant_context
from apps.internal_ui.home_data import home_data
from apps.recruiting.models import CandidateOpportunity, RecruitingTaskLink
from apps.operations.models import Task


class Post16D1Tests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()

    def attention(self, actor):
        return {r['id']: r for r in home_data(actor)['home_attention'] if r['id'].startswith('recruiting-')}

    def test_home_and_overview_share_actual_queue_rules_and_review_clears_only_response(self):
        self.publish()
        row = self.interest()
        grant = self.release()
        event = self.respond(grant)
        before = Task.objects.count()
        data = self.attention(self.admin)
        self.assertEqual(data['recruiting-interest']['count'], 1)
        self.assertEqual(data['recruiting-response']['count'], 1)
        self.assertIn('?status=received', data['recruiting-interest']['url'])
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        overview = self.client.get(reverse('recruiting:overview'))
        self.assertEqual(overview.context['interest_count'], 1)
        self.assertEqual(overview.context['pending_response_count'], 1)
        from apps.recruiting.employer import review_feedback
        review_feedback(actor=self.admin, feedback_id=event.pk, why='SYNTHETIC recorded review')
        self.assertNotIn('recruiting-response', self.attention(self.admin))
        self.assertIn('recruiting-interest', self.attention(self.admin))
        self.assertEqual(Task.objects.count(), before)
        self.candidate.refresh_from_db()
        self.assertNotEqual(self.candidate.outcome, 'hired')

    def test_recruiter_counts_and_profile_rows_never_leak_other_responsibility(self):
        self.publish();self.interest();self.respond(self.release())
        self.assertEqual(self.attention(self.recruiter), {})
        context = participant_context(self.recruiter, self.person)
        self.assertEqual(context['profile_recruiting_records'].paginator.count, 0)
        self.assertEqual(context['profile_recruiting_interests'], [])
        self.opportunity.refresh_from_db()
        workflow.delegate(actor=self.admin, opportunity_id=self.opportunity.pk, user_id=self.recruiter.pk,
            mode='view', expected_revision=self.opportunity.revision, why='SYNTHETIC scoped collaboration')
        self.assertEqual(self.attention(self.recruiter)['recruiting-interest']['count'], 1)
        self.assertEqual(participant_context(self.recruiter, self.person)['profile_recruiting_records'].paginator.count, 1)

    def test_limited_staff_and_portal_accounts_do_not_gain_recruiting_reads(self):
        limited = get_user_model().objects.create_user(username='synthetic-post16-limited-d1', is_staff=True)
        self.assertEqual(home_data(limited)['home_recruiting_state'], 'restricted')
        self.assertEqual(self.attention(limited), {})
        for actor in [limited, self.customer, self.portal]:
            with self.assertRaises(PermissionDenied): staff_queues(actor)
            with self.assertRaises(PermissionDenied): participant_context(actor, self.person)

    def test_multiple_positions_remain_separate_without_a_global_status_or_side_effects(self):
        other_need = self.approved_need(self.organization)
        other = selection.create_opportunity(actor=self.admin, need_id=other_need.pk, owner_id=self.admin.pk)
        second = self.select(opportunity=other)
        before = list(CandidateOpportunity.objects.order_by('pk').values('pk','stage_id','active','outcome','revision'))
        context = participant_context(self.admin, self.person)
        self.assertEqual(context['profile_recruiting_records'].paginator.count, 2)
        self.assertEqual({r.pk for r in context['profile_recruiting_records']}, {self.candidate.pk, second.pk})
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('participants:staff-profile', args=[self.person.pk]))
        self.assertContains(response, 'BTW relationship &amp; recruiting')
        self.assertContains(response, 'No single relationship status is configured')
        self.assertContains(response, reverse('recruiting:candidate-detail', args=[self.candidate.pk]))
        self.assertContains(response, reverse('recruiting:candidate-detail', args=[second.pk]))
        self.assertEqual(list(CandidateOpportunity.objects.order_by('pk').values('pk','stage_id','active','outcome','revision')), before)

    def test_recruiting_database_failure_is_unavailable_not_zero_or_restricted(self):
        with patch('apps.recruiting.read_models.staff_queues', side_effect=DatabaseError('SYNTHETIC unavailable')):
            result = home_data(self.admin)
        self.assertEqual(result['home_recruiting_state'], 'unavailable')
        self.assertIn('Recruiting review check unavailable', result['home_attention_unavailable'])
        self.assertFalse(any(r['id'].startswith('recruiting-') for r in result['home_attention']))

    def test_operations_configuration_does_not_claim_execution_or_hardware_readiness(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('internal:workspace', args=['operations']))
        self.assertContains(response, 'Resource configuration')
        self.assertNotContains(response, '<h2>Event Readiness</h2>')
        self.assertContains(response, 'Device and server observations belong in Trailer Control')

    def test_home_does_not_repeat_unused_followup_task_scope_queries(self):
        with patch('apps.recruiting.read_models.tasks_for') as unused:
            self.attention(self.admin)
        unused.assert_not_called()
        with patch('apps.recruiting.read_models.staff_queues') as restricted:
            self.assertEqual(home_data(self.worker)['home_recruiting_state'], 'restricted')
        restricted.assert_not_called()
