"""Current preference precedence, evidence retention and safe concurrent updates."""
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase
from django.urls import reverse
from apps.recruiting.test_employer import EmployerFixtures
from apps.recruiting import foundation, matching
from apps.recruiting.preferences import current_preferences, initial_preferences
from apps.recruiting.forms import PreferencesForm
from apps.recruiting.models import Classification, RecruitingProfile
from apps.participants.models import RegistrationSubmission, ParticipantCareerProfile
from apps.participants.portal_forms import PortalCareerForm
from apps.participants.workspace_forms import CareerProfileForm


class Post16A2Tests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        self.employment = Classification.objects.create(kind='employment', key='synthetic-retired', label='Retired and open to opportunities')
        self.availability = Classification.objects.create(kind='availability', key='synthetic-weekends', label='Weekends')
        self.career.employment_status = 'SYNTHETIC previously employed'
        self.career.work_availability = 'SYNTHETIC previous weekdays'
        self.career.save()

    def save(self, **changes):
        initial = initial_preferences(self.person)
        data = {'employment_classification': self.employment, 'availability_classification': self.availability,
            'employment_interest': 'open', 'willing_to_travel': True, 'willing_to_relocate': False,
            'fingerprint': initial['fingerprint']}
        data.update(changes.pop('data', {}))
        return foundation.save_preferences(actor=changes.pop('actor', self.portal), participant_id=self.person.pk,
            data=data, expected_revision=changes.pop('revision', initial['revision']),
            why='SYNTHETIC preference review', participant_owned=changes.pop('participant_owned', True), **changes)

    def test_one_current_contract_preserves_source_text_and_original_submissions(self):
        original = list(RegistrationSubmission.objects.values('pk', 'answers', 'intake_metadata'))
        self.save()
        result = current_preferences(self.person)
        self.assertEqual(result['employment']['value'], self.employment.label)
        self.assertEqual(result['availability']['value'], 'Weekends')
        self.assertEqual(result['interest'], 'Maybe')
        self.assertEqual(ParticipantCareerProfile.objects.get(participant=self.person).employment_interest, 'open')
        self.assertEqual((result['travel'], result['relocation']), ('Yes', 'No'))
        self.assertEqual(result['historical_employment'], 'SYNTHETIC previously employed')
        self.assertEqual(result['historical_availability'], 'SYNTHETIC previous weekdays')
        self.assertEqual(list(RegistrationSubmission.objects.values('pk', 'answers', 'intake_metadata')), original)

    def test_explicit_unknown_does_not_resurrect_conflicting_legacy_values(self):
        self.save(data={'employment_classification': None, 'availability_classification': None,
            'willing_to_travel': None, 'willing_to_relocate': None, 'employment_interest': ''})
        result = current_preferences(self.person)
        self.assertEqual(result['employment']['value'], 'Not recorded')
        self.assertEqual(result['availability']['value'], 'Not recorded')
        self.assertEqual(result['travel'], 'Not recorded')
        self.assertIn('previously employed', result['historical_employment'])

    def test_concurrent_classification_and_legacy_profile_edits_fail_before_writes(self):
        initial = initial_preferences(self.person)
        self.career.skills_interests = 'SYNTHETIC concurrent skill update'
        self.career.save()
        with self.assertRaisesMessage(ValidationError, 'profile changed'):
            self.save(data={'fingerprint': initial['fingerprint']})
        self.assertFalse(RecruitingProfile.objects.filter(participant=self.person).exists())
        self.career.refresh_from_db()
        self.assertNotEqual(self.career.employment_interest, 'open')
        self.save()
        with self.assertRaises(ValidationError):
            self.save(revision=0)

    def test_retired_choice_stays_readable_but_cannot_be_newly_selected(self):
        row = self.save()
        self.employment.is_active = False
        self.employment.save()
        self.assertIn('retired choice retained', current_preferences(self.person)['employment']['source'])
        form = PreferencesForm(initial=initial_preferences(self.person))
        self.assertIn(self.employment, form.fields['employment_classification'].queryset)
        self.save()
        self.save(data={'employment_classification': None})
        with self.assertRaises(ValidationError):
            self.save()

    def test_retirement_and_employment_changes_do_not_reduce_matching_score(self):
        criteria = self.criteria()
        before = matching.score_candidate(self.admin, self.person, criteria)
        self.save()
        after = matching.score_candidate(self.admin, self.person, criteria)
        self.assertEqual((before['score'], before['coverage']), (after['score'], after['coverage']))

    def test_catalog_retirement_after_form_load_is_rechecked_under_policy_lock(self):
        Classification.objects.filter(pk=self.employment.pk).update(is_active=False)
        self.assertTrue(self.employment.is_active)  # The loaded form choice is now stale.
        with self.assertRaises(ValidationError):
            self.save()

    def test_staff_availability_filter_uses_the_same_current_value_and_unknown_precedence(self):
        from apps.participants.discovery import discover
        self.save()
        form, people, _ = discover(self.admin, {'availability': 'Weekends'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn(self.person.pk, people.values_list('pk', flat=True))
        _, old, _ = discover(self.admin, {'availability': 'previous weekdays'})
        self.assertNotIn(self.person.pk, old.values_list('pk', flat=True))
        self.save(data={'availability_classification': None})
        _, old, _ = discover(self.admin, {'availability': 'previous weekdays'})
        self.assertNotIn(self.person.pk, old.values_list('pk', flat=True))

    def test_current_values_agree_on_staff_and_participant_profiles(self):
        self.save()
        self.client.force_login(self.portal, backend='apps.participants.auth.ParticipantAuthenticationBackend')
        response = self.client.get(reverse('participants:profile'))
        self.assertContains(response, self.employment.label)
        self.assertContains(response, 'Weekends')
        self.assertRedirects(self.client.get(reverse('participants:portal-profile-edit', args=['availability'])),
            reverse('recruiting:portal-preferences'))
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('participants:staff-profile', args=[self.person.pk]))
        self.assertContains(response, self.employment.label)
        self.assertContains(response, 'Weekends')
        self.assertContains(response, 'Previously recorded employment')

    def test_legacy_editors_preserve_evidence_and_unrelated_role_cannot_write(self):
        for cls in [PortalCareerForm, CareerProfileForm]:
            form = cls(instance=self.career)
            self.assertTrue(form.fields['employment_status'].disabled)
            self.assertTrue(form.fields['work_availability'].disabled)
        with self.assertRaises(PermissionDenied):
            self.save(actor=self.customer)
        with self.assertRaises(PermissionDenied):
            self.save(actor=self.worker, participant_owned=False)
