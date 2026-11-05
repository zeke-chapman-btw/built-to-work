"""Participant-owned next steps reuse existing requests, interests and identity guards."""
import uuid
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied, ValidationError
from apps.recruiting.test_workflow import WorkflowFixtures
from apps.recruiting import jobs, workflow, foundation
from apps.recruiting.models import CandidateInterest, InterestReview, ProfileUpdateRequest
from apps.recruiting.workflow_forms import ReviewForm
from apps.operations.models import Task


class Post16C1Tests(WorkflowFixtures, TestCase):
    def setUp(self):
        self.workflow_setup()
        self.publish()

    def sign_in(self):
        self.client.force_login(self.portal, backend='apps.participants.auth.ParticipantAuthenticationBackend')

    def test_home_is_distinct_uses_current_preferences_and_omits_irrelevant_cards(self):
        self.sign_in()
        response = self.client.get(reverse('participants:portal-home'))
        self.assertContains(response, 'Welcome,')
        self.assertContains(response, 'Keep your profile current')
        self.assertContains(response, 'Current employment situation')
        self.assertNotContains(response, 'My upcoming events')
        self.assertNotContains(response, 'My job interests')
        self.assertContains(self.client.get(reverse('participants:profile')), 'My Profile')

    def test_normal_unified_login_lands_on_home_and_preserves_explicit_profile_next(self):
        response = self.client.post(reverse('accounts:login'), {
            'identifier': self.account.login_email, 'password': 'synthetic-test-only'})
        self.assertRedirects(response, reverse('participants:portal-home'), fetch_redirect_response=False)
        self.client.logout()
        response = self.client.post(reverse('accounts:login')+'?next='+reverse('participants:profile'), {
            'identifier': self.account.login_email, 'password': 'synthetic-test-only', 'next': reverse('participants:profile')})
        self.assertRedirects(response, reverse('participants:profile'), fetch_redirect_response=False)

    def test_explicit_public_request_never_exposes_private_note_or_duplicates_on_replay(self):
        row = self.interest()
        operation = uuid.uuid4()
        before_tasks = Task.objects.count()
        self.review(row, status='information', note='SYNTHETIC private staff-only reasoning',
            participant_message='Please review your current availability.', operation_id=operation)
        self.review(row, status='information', note='SYNTHETIC replay',
            participant_message='Please review your current availability.', operation_id=operation)
        self.assertEqual(ProfileUpdateRequest.objects.count(), 1)
        self.assertEqual(InterestReview.objects.filter(operation_id=operation).count(), 1)
        self.assertEqual(Task.objects.count(), before_tasks)
        self.sign_in()
        for url in [reverse('participants:portal-home'), reverse('recruiting:portal-interest', args=[row.pk]), reverse('recruiting:portal-preferences')]:
            response = self.client.get(url)
            self.assertContains(response, 'Please review your current availability.')
            self.assertNotContains(response, 'SYNTHETIC private staff-only reasoning')
        self.person.refresh_from_db()
        foundation.confirm_profile(actor=self.portal, participant_id=self.person.pk,
            expected_fingerprint=foundation.profile_fingerprint(self.person), participant_owned=True)
        self.assertFalse(ProfileUpdateRequest.objects.filter(fulfilled_by__isnull=True).exists())
        self.assertContains(self.client.get(reverse('recruiting:portal-interest', args=[row.pk])), 'Your current profile was confirmed')
        self.assertContains(self.client.get(reverse('participants:portal-home')), 'Awaiting BTW review')

    def test_staff_form_requires_separate_participant_explanation_and_retains_it_on_error(self):
        row = self.interest()
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('recruiting:interest-review', args=[row.pk]))
        form = response.context['form']
        data = {key: value for key, value in form.initial.items()}
        data.update(status='information', reason='SYNTHETIC private note', operation_id=uuid.uuid4())
        response = self.client.post(reverse('recruiting:interest-review', args=[row.pk]), data)
        self.assertContains(response, 'Explain what the Participant should review or update.')
        self.assertEqual(ProfileUpdateRequest.objects.count(), 0)
        data.update(participant_message='SYNTHETIC public request')
        response = self.client.post(reverse('recruiting:interest-review', args=[row.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ProfileUpdateRequest.objects.get().message, 'SYNTHETIC public request')

    def test_unpublished_retained_snapshot_and_withdrawal_remain_private_and_idempotent(self):
        row = self.interest()
        self.publish(action='unpublish')
        self.sign_in()
        response = self.client.get(reverse('recruiting:portal-interest', args=[row.pk]))
        self.assertContains(response, 'SYNTHETIC approved participant-facing opportunity')
        self.assertContains(response, 'no longer published')
        self.assertEqual(self.client.get(reverse('recruiting:portal-job', args=[self.opportunity.pk])).status_code, 404)
        jobs.withdraw_interest(actor=self.portal, interest_id=row.pk)
        jobs.withdraw_interest(actor=self.portal, interest_id=row.pk)
        self.assertEqual(CandidateInterest.objects.count(), 1)
        self.assertContains(self.client.get(reverse('recruiting:portal-interest', args=[row.pk])), 'This interest was withdrawn')
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('recruiting:portal-interest', args=[row.pk])).status_code, 403)

    def test_closed_job_is_truthful_legacy_information_has_safe_fallback(self):
        row = self.interest()
        self.review(row, status='information', note='SYNTHETIC never public')
        self.publish(action='close')
        self.sign_in()
        response = self.client.get(reverse('recruiting:portal-interest', args=[row.pk]))
        self.assertContains(response, 'Contact BTW for the details')
        self.assertNotContains(response, 'SYNTHETIC never public')
        self.assertContains(self.client.get(reverse('recruiting:portal-job', args=[self.opportunity.pk])), 'View my submitted interest')

    def test_preview_hides_recruiting_writes_and_existing_guards_still_reject_them(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from django.template.loader import render_to_string
        from django.test import RequestFactory
        request = RequestFactory().get('/')
        request.user = self.admin
        request.portal_preview = {'synthetic': True}
        # Template-only context cannot grant a service permission.
        for template, context in [
            ('recruiting/portal/job.html', {'job': jobs.projection(self.opportunity), 'form': None}),
            ('recruiting/portal_preferences.html', {'account': self.account, 'form': None, 'freshness': {}})]:
            html = render_to_string(template, context, request=request)
            self.assertNotIn("I'm interested</button>", html)
            self.assertNotIn('Save preferences</button>', html)
            self.assertIn('real Participant account', html)
        with patch('apps.participants.portal_preview.current_fixture', return_value=True):
            with self.assertRaises(ValidationError): self.interest()

    def test_out_of_scope_review_cannot_create_profile_request(self):
        row = self.interest()
        with self.assertRaises(PermissionDenied):
            self.review(row, actor=self.recruiter, status='information', participant_message='SYNTHETIC denied')
        self.assertEqual(ProfileUpdateRequest.objects.count(), 0)

    def test_request_does_not_imply_selection_disclosure_or_automatic_task(self):
        row = self.interest()
        prior = Task.objects.count()
        self.review(row, status='information', participant_message='SYNTHETIC review availability')
        row.refresh_from_db()
        self.assertIsNone(row.candidate_id)
        self.assertEqual(Task.objects.count(), prior)
        with self.assertRaises(ValidationError):
            self.review(row, status='review', participant_message='SYNTHETIC wrong status')

    def test_retained_interest_get_cannot_read_another_participant(self):
        from apps.participants.models import Participant
        other = Participant.objects.create(kind='person', first_name='SYNTHETIC', last_name='Private Other')
        foreign = CandidateInterest.objects.create(opportunity=self.opportunity, publication=self.opportunity.publication,
            participant=other, source_participant=other, submitted_by=self.portal)
        self.sign_in()
        self.assertEqual(self.client.get(reverse('recruiting:portal-interest', args=[foreign.pk])).status_code, 404)
