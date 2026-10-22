"""Synthetic cases for the explicitly approved dual-contact policy."""
from datetime import date
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from .models import Participant
from .intake_identity import resolve_intake_identity


class IntakeIdentity11BTests(TestCase):
    def answers(self, **changes):
        data = {"first_name": "SYNTHETIC", "last_name": "Person", "dob": "2000-01-01",
                "email": "synthetic@example.test", "phone": "5550101234"}
        data.update(changes)
        return data

    def person(self, **changes):
        values = {"first_name": "SYNTHETIC", "last_name": "Person",
                  "date_of_birth": date(2000, 1, 1),
                  "contact_email": "synthetic@example.test", "contact_phone": "5550101234"}
        values.update(changes)
        return Participant.objects.create(**values)

    def resolve(self, **changes):
        return resolve_intake_identity(answers=self.answers(**changes), occurred_on=date(2026, 10, 22))

    def test_unique_dual_match_associates_without_profile_write(self):
        person = self.person(city="STAFF PROTECTED CITY")
        result = self.resolve(email="SYNTHETIC@example.test", phone="(555) 010-1234")
        self.assertEqual(result.participant.pk, person.pk)
        self.assertFalse(result.created)
        self.assertFalse(result.review_required)
        person.refresh_from_db()
        self.assertEqual(person.city, "STAFF PROTECTED CITY")

    def test_single_contact_split_and_multiple_records_enter_review(self):
        person = self.person()
        result = self.resolve(phone="5550109876")
        self.assertTrue(result.review_required)
        self.assertIn("one_contact_only", result.reasons)
        self.assertNotEqual(result.participant.pk, person.pk)
        first_split = self.person(contact_email="split-a@example.test", contact_phone="5550102222")
        second = self.person(contact_email="split-b@example.test", contact_phone="5550103333")
        result = self.resolve(email=first_split.contact_email, phone=second.contact_phone)
        self.assertTrue(result.review_required)
        self.assertIn("contacts_different_people", result.reasons)
        self.person()
        result = self.resolve()
        self.assertTrue(result.review_required)
        self.assertIn("multiple_records", result.reasons)

    def test_shared_family_material_name_and_dob_conflicts(self):
        person = self.person()
        result = self.resolve(first_name="SYNTHETIC Sibling", dob="2009-01-01")
        self.assertTrue(result.review_required)
        self.assertIn("name_conflict", result.reasons)
        self.assertIn("dob_conflict", result.reasons)
        self.assertNotEqual(result.participant.identity_uuid, person.identity_uuid)
        person.refresh_from_db()
        self.assertEqual(person.date_of_birth, date(2000, 1, 1))

    def test_neither_contact_match_creates_distinct_person(self):
        result = self.resolve()
        self.assertTrue(result.created)
        self.assertFalse(result.review_required)
        self.assertEqual(result.participant.contact_email, "synthetic@example.test")

    def test_archived_contact_never_reactivated(self):
        person = self.person(archived_at=timezone.now())
        result = self.resolve()
        self.assertTrue(result.review_required)
        self.assertIn("archived_identity", result.reasons)
        person.refresh_from_db()
        self.assertIsNotNone(person.archived_at)

    def test_missing_contacts_and_underage_direct_service_call_rejected(self):
        for changes in [{"email": ""}, {"phone": ""}, {"email": "invalid"},
                        {"dob": "2015-01-01"}, {"dob": "invalid"}]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.resolve(**changes)
        self.assertEqual(Participant.objects.count(), 0)
