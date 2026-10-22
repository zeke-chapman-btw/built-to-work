"""Whole intake persistence, review decisions and provenance with synthetic data."""
from datetime import date
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase
from django.utils import timezone
from apps.events.models import ParticipantMatchReview, ParticipantIdentityLink, ParticipantMatchDecision
from .models import (Participant, ParticipantCareerProfile, ConsentDocumentVersion,
                     RegistrationSubmission, ConsentAcceptance, IntakeSession)
from .intake_lifecycle import create_form, publish, revision, assign
from .intake_submission import start_session, complete_session
from .intake_review import decide, review_revision
from .identity import canonical_id, identity_context
from . import test_intake_contract_11b as _contract_tests


class IntakeSubmission11BTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="synthetic-admin", email="admin@example.test", password="synthetic-test-only")
        self.document = ConsentDocumentVersion.objects.create(
            key="btw-intake", version="synthetic-v1", title="SYNTHETIC consent",
            body="SYNTHETIC test-only terms", content_hash="a"*64, is_approved=True)
        draft = create_form(actor=self.admin, name="SYNTHETIC Unified Intake")
        self.version = publish(actor=self.admin, version_id=draft.pk, expected_revision=revision(draft))
        self.assignment = assign(actor=self.admin, version_id=self.version.pk, channel="online")

    def answers(self, **changes):
        return _contract_tests.IntakeContract11BTests().answers(**changes)

    def session(self, **changes):
        session = start_session(assignment=self.assignment, browser_key="a"*64)
        session.answers = self.answers(**changes)
        session.current_step = 4
        session.consent_accepted_at = timezone.now()
        session.save()
        return session

    def submit(self, **changes):
        return complete_session(session_id=self.session(**changes).pk)

    def legacy(self):
        return Participant.objects.create(first_name="SYNTHETIC", last_name="Example",
            contact_email="synthetic@example.test", contact_phone="5550101234",
            date_of_birth=date(2004, 1, 1), city="PROTECTED LEGACY CITY")

    def action(self, review, action, target=None, **changes):
        args = dict(actor=self.admin, review_id=review.pk, action=action,
                    target_id=target.pk if target else None, reason="SYNTHETIC confirmed review",
                    expected_revision=review_revision(review), confirmed=True)
        args.update(changes)
        return decide(**args)

    def test_atomic_completion_and_retry_preserve_origin_and_identity(self):
        session = self.session(travel_willingness="maybe", travel_range="regional")
        one = complete_session(session_id=session.pk)
        two = complete_session(session_id=session.pk)
        self.assertEqual(one.pk, two.pk)
        self.assertEqual(Participant.objects.count(), 1)
        self.assertEqual(ConsentAcceptance.objects.count(), 1)
        self.assertEqual(one.participant.acquisition.channel, "online")
        uuid = one.participant.identity_uuid
        original = one.participant.acquisition.acquired_at
        later = self.submit(city="NEW INTAKE CITY")
        self.assertEqual(later.participant.identity_uuid, uuid)
        later.participant.refresh_from_db()
        self.assertEqual(later.participant.city, "NEW INTAKE CITY")
        self.assertEqual(later.participant.acquisition.acquired_at, original)
        self.assertEqual(later.participant.career_profile.intake_data["travel_willingness"], "no")

    def test_manual_and_legacy_values_never_silently_overwritten(self):
        old = self.legacy()
        ParticipantCareerProfile.objects.create(participant=old, willing_to_travel=True)
        source = self.submit(city="NEW SELF REPORT", travel_willingness="maybe", travel_range="regional")
        old.refresh_from_db()
        self.assertEqual(source.participant_id, old.pk)
        self.assertEqual(old.city, "PROTECTED LEGACY CITY")
        self.assertNotIn("travel_willingness", old.career_profile.intake_data)
        self.assertEqual(source.answers["travel_willingness"], "maybe")

    def test_manual_save_protects_a_previously_intake_owned_field(self):
        source = self.submit()
        person = source.participant
        person.city = "STAFF REVIEWED CITY"
        person.save()
        self.submit(city="NEW UNREVIEWED CITY", preferred_name="NEW REPORTED NICKNAME")
        person.refresh_from_db()
        self.assertEqual(person.city, "STAFF REVIEWED CITY")
        self.assertEqual(person.preferred_name, "NEW REPORTED NICKNAME")

    def test_possible_duplicate_completes_without_current_profile_updates(self):
        target = self.legacy()
        source = self.submit(phone="5550109876", city="UNREVIEWED CITY")
        review = ParticipantMatchReview.objects.get(source_submission=source)
        self.assertEqual(review.status, "open")
        self.assertNotEqual(source.participant_id, target.pk)
        self.assertEqual(ConsentAcceptance.objects.filter(submission=source).count(), 1)
        self.assertFalse(ParticipantCareerProfile.objects.filter(participant=source.participant).exists())
        target.refresh_from_db()
        self.assertEqual(target.city, "PROTECTED LEGACY CITY")

    def test_link_only_keeps_profile_and_source_relationships_intact(self):
        target = self.legacy()
        source = self.submit(phone="5550109876", city="UNREVIEWED CITY")
        review = ParticipantMatchReview.objects.get(source_submission=source)
        original_id = source.participant_id
        self.action(review, "link_only", target)
        source.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(source.participant_id, original_id)
        self.assertEqual(canonical_id(original_id), target.pk)
        self.assertEqual(target.city, "PROTECTED LEGACY CITY")
        self.assertFalse(ParticipantCareerProfile.objects.filter(participant=target).exists())

    def test_associate_update_uses_fresh_projection_and_respects_protection(self):
        target = self.legacy()
        source = self.submit(phone="5550109876", travel_willingness="maybe", travel_range="regional")
        review = ParticipantMatchReview.objects.get(source_submission=source)
        with identity_context():
            self.action(review, "associate_update", target)
        target.refresh_from_db()
        self.assertEqual(target.city, "PROTECTED LEGACY CITY")
        self.assertEqual(target.career_profile.intake_data["travel_willingness"], "maybe")
        self.assertEqual(ParticipantIdentityLink.objects.get(review=review).source_participant_id, source.participant_id)

    def test_review_later_and_keep_separate_preserve_answers_and_manual_edits(self):
        self.legacy()
        source = self.submit(phone="5550109876", city="INTAKE CITY")
        review = ParticipantMatchReview.objects.get(source_submission=source)
        self.action(review, "review_later")
        review.refresh_from_db()
        self.assertEqual(review.status, "later")
        person = source.participant
        person.city = "STAFF EDIT WHILE PENDING"
        person.save()
        self.action(review, "separate")
        person.refresh_from_db()
        self.assertEqual(person.city, "STAFF EDIT WHILE PENDING")
        self.assertEqual(person.acquisition.channel, "online")
        self.assertFalse(ParticipantIdentityLink.objects.filter(review=review).exists())
        source.refresh_from_db()
        self.assertEqual(source.answers["city"], "INTAKE CITY")

    def test_review_permissions_stale_revision_and_idempotent_decision(self):
        target = self.legacy()
        source = self.submit(phone="5550109876")
        review = ParticipantMatchReview.objects.get(source_submission=source)
        with self.assertRaises(ValidationError):
            self.action(review, "link_only", target, expected_revision="stale")
        with self.assertRaises(ValidationError):
            self.action(review, "link_only", target, confirmed=False)
        self.action(review, "link_only", target)
        self.action(review, "link_only", target)
        self.assertEqual(ParticipantMatchDecision.objects.filter(review=review).count(), 1)

    def test_submission_snapshot_and_identity_are_immutable(self):
        source = self.submit()
        source.answers["city"] = "TAMPERED"
        with self.assertRaises(ValidationError):
            source.save()
        source.refresh_from_db()
        self.assertNotEqual(source.answers["city"], "TAMPERED")

    def event_session(self, event, **changes):
        assignment = assign(actor=self.admin, version_id=self.version.pk, channel="event", event=event)
        session = start_session(assignment=assignment, browser_key="b"*64, event=event)
        session.answers = self.answers(**changes)
        session.current_step = 4
        session.consent_accepted_at = timezone.now()
        session.save()
        return session

    def test_pending_duplicate_finishes_when_event_capacity_is_full(self):
        from datetime import timedelta
        from apps.events.models import Event, EventRegistration, QrTicket
        from apps.events.services import register_participant
        target = self.legacy()
        event = Event.objects.create(name="SYNTHETIC Full Event", status="upcoming",
            start_at=timezone.now()+timedelta(days=1), end_at=timezone.now()+timedelta(days=2),
            public_registration_status="open", public_consent_document=self.document, registration_capacity=1)
        existing, _ = register_participant(event=event, participant=target, actor=self.admin)
        original_ticket = QrTicket.objects.get(registration=existing, is_current=True)
        source = complete_session(session_id=self.event_session(event, phone="5550109876").pk)
        review = ParticipantMatchReview.objects.get(source_submission=source)
        self.assertEqual(EventRegistration.objects.filter(event=event).count(), 1)
        self.assertEqual(source.intake_metadata["registration_status"], "pending_identity_review")
        from apps.events.models import EventPublicSlug
        from django.urls import reverse
        EventPublicSlug.objects.create(event=event, slug="synthetic-full-intake", is_current=True)
        response = self.client.get(reverse("public-event-registration", args=["synthetic-full-intake"]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start intake")
        self.action(review, "link_only", target)
        review.refresh_from_db()
        self.assertEqual(review.evidence["registration_id"], str(existing.pk))
        self.assertEqual(QrTicket.objects.get(registration=existing, is_current=True).pk, original_ticket.pk)
        self.assertEqual(EventRegistration.objects.filter(event=event).count(), 1)

    def test_event_linked_new_acquisition_is_event_not_attendance(self):
        from datetime import timedelta
        from apps.events.models import Event, Attendance
        event = Event.objects.create(name="SYNTHETIC Event Source", status="upcoming",
            start_at=timezone.now()+timedelta(days=1), end_at=timezone.now()+timedelta(days=2),
            public_registration_status="open", public_consent_document=self.document)
        source = complete_session(session_id=self.event_session(event).pk)
        self.assertEqual(source.participant.acquisition.channel, "event")
        self.assertEqual(source.participant.acquisition.event_id, event.pk)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_changed_consent_blocks_completion_without_partial_records(self):
        session = self.session()
        ConsentDocumentVersion.objects.filter(pk=self.document.pk).update(body="CHANGED SYNTHETIC TEXT")
        with self.assertRaises(ValidationError):
            complete_session(session_id=session.pk)
        self.assertEqual(RegistrationSubmission.objects.count(), 0)
        self.assertEqual(Participant.objects.count(), 0)
        session.refresh_from_db()
        self.assertEqual(session.state, "open")
        self.assertTrue(session.answers["email"])
