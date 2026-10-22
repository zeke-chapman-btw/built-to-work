"""Typed participant mappings, frozen qualification evidence and privacy."""
from copy import deepcopy
from datetime import date
import json
import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.events.models import ParticipantMatchReview
from .models import (Participant, ParticipantCareerProfile, ParticipantWorkExperience,
                     ParticipantHandsOnExperience, QualificationCategory, QualificationCatalogEntry,
                     ParticipantQualification, RegistrationSubmission)
from .verification_models import ParticipantVerificationRecord
from .intake_lifecycle import save_draft, publish, assign, revision
from .intake_review import decide, review_revision
from .qualification_services import process_frozen_intake_submission
from .discovery import discover
from .identity import identity_context
from . import test_intake_submission_11b as baseline


class IntakeProfile11BTests(TestCase):
    setUp = baseline.IntakeSubmission11BTests.setUp
    answers = baseline.IntakeSubmission11BTests.answers
    session = baseline.IntakeSubmission11BTests.session
    submit = baseline.IntakeSubmission11BTests.submit

    def entry(self, **changes):
        value = {"entry_id": str(uuid.uuid4()), "area": "welding", "source": "school",
                 "paid": "unpaid", "duration": "6_12_months", "skills": ["mig"]}
        value.update(changes)
        return value

    def mappings(self):
        category = QualificationCategory.objects.create(name="SYNTHETIC trade evidence", slug="synthetic-11b")
        self.skill = QualificationCatalogEntry.objects.create(category=category, name="SYNTHETIC MIG",
            identifier="synthetic-11b-mig", kind="skill")
        self.cert = QualificationCatalogEntry.objects.create(category=category, name="SYNTHETIC OSHA 10",
            identifier="synthetic-11b-osha", kind="certification")
        contract = deepcopy(self.version.snapshot)
        contract["qualification_mappings"] = [
            {"question": "skills", "value": "mig", "catalog_uuid": str(self.skill.stable_id)},
            {"question": "certification_choices", "value": "osha10", "catalog_uuid": str(self.cert.stable_id)}]
        draft = save_draft(actor=self.admin, form_id=self.version.form_id, contract=contract,
                           expected_revision=revision(self.version))
        self.version = publish(actor=self.admin, version_id=draft.pk, expected_revision=revision(draft))
        self.assignment = assign(actor=self.admin, version_id=self.version.pk, channel="online")

    def test_alex_minor_cte_hands_on_is_not_fabricated_employment(self):
        dob = date(timezone.localdate().year - 17, 1, 1).isoformat()
        source = self.submit(dob=dob, current_situation="high_school", school_name="SYNTHETIC CTE",
            hands_on="yes", experiences=[self.entry()], industry_interests=["welding"])
        self.assertEqual(source.age_classification, "minor")
        self.assertEqual(ParticipantHandsOnExperience.objects.count(), 1)
        self.assertFalse(ParticipantWorkExperience.objects.exists())
        self.assertFalse(ParticipantQualification.objects.exists())
        with self.assertRaises(ValidationError):
            self.submit(dob=dob, current_situation="high_school", desired_pay="20_24")

    def test_jordan_actual_retail_employment_remains_separate_from_trade_interest(self):
        source = self.submit(current_situation="employed_other", current_industry="retail",
            job_title="SYNTHETIC retail associate", employment_duration="1_2_years",
            job_responsibilities="SYNTHETIC customer service", industry_interests=["electrical"],
            career_timeline="1_3_months", opportunity_types=["apprenticeship"],
            travel_willingness="maybe", travel_range="regional", desired_pay="20_24")
        work = ParticipantWorkExperience.objects.get()
        self.assertEqual(work.role, "SYNTHETIC retail associate")
        self.assertEqual(work.employer, "")
        self.assertIsNone(work.started_on)
        self.assertEqual(work.reported_duration, "1_2_years")
        self.assertFalse(ParticipantHandsOnExperience.objects.exists())
        self.assertFalse(ParticipantQualification.objects.exists())
        with identity_context():
            form, people, _ = discover(self.admin, {"trade_interest": "electrical", "travel": "maybe"})
            self.assertTrue(form.is_valid())
            self.assertEqual(list(people.values_list("pk", flat=True)), [source.participant_id])
        person = source.participant
        career = person.career_profile
        career.willing_to_travel = False
        career.save()
        with identity_context():
            self.assertFalse(discover(self.admin, {"travel": "maybe"})[1].exists())
            self.assertTrue(discover(self.admin, {"travel": "no"})[1].filter(pk=person.pk).exists())
        career.willing_to_travel = None
        career.save(update_fields=["willing_to_travel", "updated_at"])
        with identity_context():
            self.assertFalse(discover(self.admin, {"travel": "maybe"})[1].exists())
            self.assertTrue(discover(self.admin, {"travel": "unknown"})[1].filter(pk=person.pk).exists())

    def test_chris_multi_entry_claims_use_frozen_maps_without_verification_or_dob_leak(self):
        self.mappings()
        self.skill.name = "SYNTHETIC renamed catalog label"
        self.skill.is_active = False
        self.skill.save()
        rows = [self.entry(source="employment", paid="paid", duration="over_10_years",
                           proficiency="advanced") for _ in range(21)]
        source = self.submit(dob="1983-01-01", current_situation="employed_trade",
            current_industry="construction", job_title="SYNTHETIC welding supervisor",
            hands_on="yes", experiences=rows, certifications="yes", certification_choices=["osha10"])
        self.assertEqual(ParticipantHandsOnExperience.objects.count(), 21)
        self.assertEqual(ParticipantQualification.objects.count(), 22)
        self.assertFalse(ParticipantVerificationRecord.objects.exists())
        self.assertEqual(process_frozen_intake_submission(source), [])
        skill = ParticipantQualification.objects.filter(catalog_entry=self.skill).first()
        self.assertEqual(skill.mapping_snapshot["catalog_snapshot"]["name"], "SYNTHETIC MIG")
        provenance = json.dumps(skill.source_answer_snapshot)
        self.assertNotIn("1983-01-01", provenance)
        self.assertNotIn("synthetic@example.test", provenance)
        self.assertTrue(skill.is_self_reported)
        self.assertEqual(skill.participant_id, source.participant_id)

    def test_minor_dob_is_restricted_on_profile_review_and_raw_admin(self):
        target = baseline.IntakeSubmission11BTests.legacy(self)
        source = self.submit(phone="5550107777")
        review = ParticipantMatchReview.objects.get(source_submission=source)
        reader = get_user_model().objects.create_user(username="synthetic-private-reader", is_staff=True)
        reader.user_permissions.add(*Permission.objects.filter(codename__in=[
            "view_participant", "view_participantmatchreview", "view_registrationsubmission",
            "change_registrationsubmission", "change_participant"]))
        self.client.force_login(reader, backend="django.contrib.auth.backends.ModelBackend")
        profile = self.client.get(reverse("participants:staff-profile", args=[target.pk]))
        self.assertEqual(profile.status_code, 200)
        self.assertNotContains(profile, "2004-01-01")
        detail = self.client.get(reverse("participants:intake-review-detail", args=[review.pk]), {"target": target.pk})
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "2004-01-01")
        self.assertEqual(self.client.get(reverse("admin:participants_registrationsubmission_change",
                                               args=[source.pk])).status_code, 403)

    def test_staff_profile_shows_new_typed_data_and_keeps_intake_history(self):
        source = self.submit(industry_interests=["welding"], hands_on="yes", experiences=[self.entry()],
                             travel_willingness="maybe", travel_range="regional")
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("participants:staff-profile", args=[source.participant_id])
        response = self.client.get(url, {"section": "career"})
        self.assertContains(response, "Hands-on trade experience")
        self.assertContains(response, "Welding/Fabrication")
        self.assertContains(response, "Maybe")
        self.assertContains(response, "Regional")
        history = self.client.get(url, {"section": "acquisition"})
        self.assertContains(history, str(source.intake_operation_id))
        self.assertContains(history, "Smart intake history")
