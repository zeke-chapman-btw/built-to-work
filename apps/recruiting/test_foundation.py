"""Build 15A boundaries, real intake integrity, original records and policy history."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.customers.models import CustomerOrganization, Campaign
from apps.participants.models import Participant, ParticipantCareerProfile, ConsentAcceptance, RegistrationSubmission
from apps.participants.identity import canonical_id, identity_members
from apps.participants.workspace_models import ParticipantNote, SavedParticipantView
from apps.participants.workspace_services import create_person
from apps.participants import test_intake_submission_11b as intake_baseline
from .models import (RecruitingProfile, Classification, CandidateList, CandidateListEntry,
    ProfileUpdateRequest, IdentityConsolidation, CampaignSource)
from . import foundation as service


class FoundationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username='synthetic-build15-admin', password='synthetic-only-test')
        self.other = User.objects.create_superuser(username='synthetic-build15-other', password='synthetic-only-test')
        self.person = Participant.objects.create(first_name='SYNTHETIC', last_name='Build15 person', contact_email='synthetic15@example.test')
        self.org = CustomerOrganization.objects.create(name='SYNTHETIC company A')
        self.other_org = CustomerOrganization.objects.create(name='SYNTHETIC company B')

    def test_staff_creation_uses_existing_person_duplicate_guard_and_lead_metadata(self):
        with self.assertRaises(ValidationError):
            create_person(actor=self.admin, data={'first_name': 'SYNTHETIC', 'last_name': 'Duplicate',
                'contact_email': self.person.contact_email, 'audit_reason': 'Synthetic duplicate test'})
        added = create_person(actor=self.admin, data={'first_name': 'SYNTHETIC', 'last_name': 'Lead',
            'contact_email': 'synthetic15-new@example.test', 'audit_reason': 'Synthetic referral'})
        self.assertTrue(added.recruiting_profile.is_lead)
        self.assertEqual(added.recruiting_profile.recorded_by, self.admin)
        self.assertFalse(service.readiness(added)['eligible'])
        self.assertFalse(RegistrationSubmission.objects.filter(participant=added).exists())

    def test_profile_coverage_account_and_old_age_do_not_substitute_for_intake(self):
        ParticipantCareerProfile.objects.create(participant=self.person, years_of_experience=10, employment_interest='seeking')
        self.assertFalse(service.readiness(self.person)['eligible'])

    def test_candidate_lists_private_shared_and_owner_management(self):
        private = service.save_list(actor=self.admin, name='SYNTHETIC private')
        shared = service.save_list(actor=self.admin, name='SYNTHETIC shared', shared=True)
        self.assertFalse(service.visible_lists(self.other).filter(pk=private.pk).exists())
        self.assertTrue(service.visible_lists(self.other).filter(pk=shared.pk).exists())
        one = service.change_list_member(actor=self.admin, list_id=shared.pk, participant_id=self.person.pk)
        two = service.change_list_member(actor=self.admin, list_id=shared.pk, participant_id=self.person.pk)
        self.assertEqual(one.pk, two.pk)
        service.change_list_member(actor=self.admin, list_id=shared.pk, participant_id=self.person.pk, remove=True, why='Synthetic removal')
        one.refresh_from_db()
        self.assertEqual(one.removed_by, self.admin)
        self.assertIsNotNone(one.removed_at)
        self.assertTrue(CandidateListEntry.objects.filter(pk=one.pk).exists())

    def test_archived_lists_retain_memberships_and_revision_conflicts(self):
        row = service.save_list(actor=self.admin, name='SYNTHETIC history')
        service.change_list_member(actor=self.admin, list_id=row.pk, participant_id=self.person.pk)
        with self.assertRaises(ValidationError):
            service.save_list(actor=self.admin, name=row.name, object_id=row.pk, expected_revision=0, archive=True)
        service.save_list(actor=self.admin, name=row.name, object_id=row.pk, expected_revision=1, archive=True)
        self.assertEqual(CandidateListEntry.objects.count(), 1)

    def test_explicit_confirmation_unrelated_update_and_relevant_changes(self):
        self.assertTrue(service.freshness(self.person)['needs_confirmation'])
        event = service.confirm_profile(actor=self.admin, participant_id=self.person.pk,
            expected_fingerprint=service.profile_fingerprint(self.person), why='Synthetic staff-assisted review')
        self.assertFalse(service.freshness(self.person)['needs_confirmation'])
        old = self.person.recruiting_profile.last_relevant_update_at
        self.person.save(update_fields=['updated_at'])
        self.assertFalse(service.freshness(self.person)['needs_confirmation'])
        self.person.recruiting_profile.refresh_from_db()
        self.assertEqual(self.person.recruiting_profile.last_relevant_update_at, old)
        self.person.city = 'SYNTHETIC new city'; self.person.save()
        self.assertTrue(service.freshness(self.person)['needs_confirmation'])
        self.assertEqual(event.actor, self.admin)
        with self.assertRaises(ValidationError):
            service.confirm_profile(actor=self.admin, participant_id=self.person.pk, expected_fingerprint=event.fingerprint, why='Stale')

    def test_update_requests_resolve_only_on_explicit_confirmation(self):
        row = service.request_update(actor=self.admin, participant_id=self.person.pk, message='Confirm availability')
        self.person.city = 'SYNTHETIC updated'; self.person.save()
        row.refresh_from_db(); self.assertIsNone(row.fulfilled_by_id)
        service.confirm_profile(actor=self.admin, participant_id=self.person.pk,
            expected_fingerprint=service.profile_fingerprint(self.person), why='Confirmed with person')
        row.refresh_from_db(); self.assertIsNotNone(row.fulfilled_by_id)

    def test_retired_classifications_preserve_existing_values_and_relocation_separate(self):
        cls = Classification.objects.create(kind='employment', key='nearing-retirement', label='Nearing Retirement')
        data = {'employment_classification': cls, 'availability_classification': None, 'willing_to_relocate': True}
        row = service.save_preferences(actor=self.admin, participant_id=self.person.pk, data=data, expected_revision=0, why='Synthetic preference')
        cls.is_active = False; cls.save()
        row = service.save_preferences(actor=self.admin, participant_id=self.person.pk, data=data, expected_revision=1, why='Retain historical value')
        self.assertEqual(row.employment_classification, cls)
        career = ParticipantCareerProfile.objects.filter(participant=self.person).first()
        self.assertTrue(career is None or career.willing_to_travel is None)
        other = Participant.objects.create(first_name='SYNTHETIC', last_name='Other')
        with self.assertRaises(ValidationError):
            service.save_preferences(actor=self.admin, participant_id=other.pk, data=data, expected_revision=0, why='Retired new assignment')

    def test_planned_campaign_dates_and_missing_duration_never_expire_exclusivity(self):
        campaign = Campaign.objects.create(organization=self.org, name='SYNTHETIC campaign', end_date=timezone.localdate()-timedelta(days=100))
        source = service.source_campaign(actor=self.admin, campaign_id=campaign.pk, participant_id=self.person.pk)
        self.assertFalse(service.exclusive_sources(self.person, self.org.pk))
        self.assertTrue(service.exclusive_sources(self.person, self.other_org.pk)[0]['setup_required'])
        campaign.status='completed'; campaign.save()
        service.configure_campaign(actor=self.admin, campaign_id=campaign.pk, days=30,
            actual_completion=timezone.now()-timedelta(days=10), why='Actual completion verified')
        self.assertTrue(service.exclusive_sources(self.person, self.other_org.pk))
        self.assertFalse(service.exclusive_sources(self.person, self.other_org.pk, at=timezone.now()+timedelta(days=21)))
        service.release_exclusivity(actor=self.admin, source_id=source.pk, why='Owner authorized early release')
        self.assertFalse(service.exclusive_sources(self.person, self.other_org.pk))
        source.refresh_from_db(); self.assertEqual(source.source_participant, self.person)

    def test_consolidation_keeps_original_facts_notes_ids_and_source_history(self):
        source = Participant.objects.create(first_name='SYNTHETIC', last_name='Alias', city='Original source city')
        note = ParticipantNote.objects.create(participant=source, content='SYNTHETIC preserved note', author=self.admin)
        listing = service.save_list(actor=self.admin, name='SYNTHETIC aliases')
        service.change_list_member(actor=self.admin, list_id=listing.pk, participant_id=source.pk)
        service.change_list_member(actor=self.admin, list_id=listing.pk, participant_id=self.person.pk)
        snapshot = service.consolidation_preview(source.pk, self.person.pk)
        decision = service.consolidate(actor=self.admin, source_id=source.pk, target_id=self.person.pk,
            expected_fingerprint=snapshot['fingerprint'], confirmed=True, why='Staff compared established identities')
        self.assertEqual(canonical_id(source.pk), self.person.pk)
        self.assertIn(source.pk, identity_members(self.person.pk))
        source.refresh_from_db();note.refresh_from_db()
        self.assertEqual(source.city, 'Original source city')
        self.assertEqual(note.participant_id, source.pk)
        self.assertEqual(listing.entries.filter(removed_at__isnull=True).count(), 1)
        self.assertEqual(listing.entries.count(), 2)
        service.reverse_consolidation(actor=self.admin, consolidation_id=decision.pk, why='Review changed history')
        decision.refresh_from_db();self.assertTrue(decision.reversal_review_required)

    def test_consolidation_rechecks_fingerprint_and_confirmation(self):
        source = Participant.objects.create(first_name='SYNTHETIC', last_name='Stale source')
        snapshot = service.consolidation_preview(source.pk, self.person.pk)
        with self.assertRaises(ValidationError):
            service.consolidate(actor=self.admin, source_id=source.pk, target_id=self.person.pk,
                expected_fingerprint=snapshot['fingerprint'], confirmed=False, why='No confirmation')
        source.city='Changed during review';source.save()
        with self.assertRaises(ValidationError):
            service.consolidate(actor=self.admin, source_id=source.pk, target_id=self.person.pk,
                expected_fingerprint=snapshot['fingerprint'], confirmed=True, why='Stale source')
        self.assertFalse(IdentityConsolidation.objects.exists())

    def test_nonstaff_and_stale_staff_cannot_mutate_recruiting(self):
        user = get_user_model().objects.create_user(username='synthetic15-no-access', password='synthetic-only-test')
        with self.assertRaises(PermissionDenied):
            service.save_list(actor=user, name='Denied')
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            service.save_list(actor=self.admin, name='Stale administrator')

    def test_shared_saved_view_reexecutes_current_permissions(self):
        saved = SavedParticipantView.objects.create(owner=self.admin, name='SYNTHETIC shared', shared=True, criteria={'q': 'Build15 person'})
        self.client.force_login(self.other, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('participants:staff-directory'), {'saved': saved.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Build15 person')
        saved.shared=False;saved.save()
        self.assertEqual(self.client.get(reverse('participants:staff-directory'), {'saved': saved.pk}).status_code, 404)

    def test_new_staff_screens_render_and_require_csrf(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        for url in [reverse('recruiting:lists'), reverse('recruiting:candidate', args=[self.person.pk]), reverse('recruiting:classifications')]:
            self.assertEqual(self.client.get(url).status_code, 200)
        from django.test import Client
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(strict.post(reverse('recruiting:lists'), {'name':'SYNTHETIC denied'}).status_code,403)

    def test_participant_confirmation_uses_own_identity_and_current_account(self):
        from apps.participants.models import ParticipantAccount
        user=get_user_model().objects.create_user(username='synthetic15-own-portal',password='synthetic-only-test')
        ParticipantAccount.objects.create(participant=self.person,user=user,login_email='synthetic15-own@example.test',status='active',email_verified_at=timezone.now())
        other=Participant.objects.create(first_name='SYNTHETIC',last_name='Not owned')
        with self.assertRaises(PermissionDenied):
            service.confirm_profile(actor=user,participant_id=other.pk,expected_fingerprint=service.profile_fingerprint(other),participant_owned=True)
        self.client.force_login(user,backend='apps.participants.auth.ParticipantAuthenticationBackend')
        response=self.client.get(reverse('recruiting:portal-preferences'))
        self.assertEqual(response.status_code,200)
        response=self.client.post(reverse('recruiting:portal-preferences'),{'operation':'confirm',
            'fingerprint':service.profile_fingerprint(self.person),'participant_id':other.pk})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.person.recruiting_confirmations.count(),1)
        self.assertFalse(other.recruiting_confirmations.exists())
        get_user_model().objects.filter(pk=user.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            service.confirm_profile(actor=user,participant_id=self.person.pk,expected_fingerprint=service.profile_fingerprint(self.person),participant_owned=True)


class RecordedIntakeReadinessTests(TestCase):
    setUp=intake_baseline.IntakeSubmission11BTests.setUp
    answers=intake_baseline.IntakeSubmission11BTests.answers
    session=intake_baseline.IntakeSubmission11BTests.session
    submit=intake_baseline.IntakeSubmission11BTests.submit

    def test_real_completed_required_intake_is_eligible(self):
        submission=self.submit()
        result=service.readiness(submission.participant)
        self.assertTrue(result['eligible'], result['issues'])

    def test_tampered_contract_metadata_is_not_assumed_complete(self):
        submission=self.submit()
        metadata=dict(submission.intake_metadata);metadata['contract_hash']='tampered'
        RegistrationSubmission.objects.filter(pk=submission.pk).update(intake_metadata=metadata)
        self.assertFalse(service.readiness(submission.participant)['eligible'])
