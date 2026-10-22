"""Independent histories, immutability and Admin-only lifecycle regression tests."""
from copy import deepcopy
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from .models import RegistrationFormVersion, RegistrationSubmission, IntakeFormAssignment
from .intake_defaults import default_contract
from .intake_lifecycle import (create_form, duplicate_form, save_draft, publish,
                               revision, assign, retire, verify_published)


class IntakeLifecycle11BTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="synthetic-intake-admin", email="admin@example.test", password="synthetic-test-only")
        self.staff = get_user_model().objects.create_user(
            username="synthetic-intake-staff", is_staff=True)

    def published(self, name="SYNTHETIC Intake"):
        draft = create_form(actor=self.admin, name=name)
        return publish(actor=self.admin, version_id=draft.pk, expected_revision=revision(draft))

    def test_histories_are_independent_and_duplicate_is_unpublished_one(self):
        one = self.published("SYNTHETIC Form One")
        second = self.published("SYNTHETIC Form Two")
        copy = duplicate_form(actor=self.admin, source_version_id=one.pk, name="SYNTHETIC Copy")
        self.assertEqual([one.version, second.version, copy.version], [1, 1, 1])
        self.assertEqual(copy.status, "draft")
        self.assertEqual(copy.snapshot, one.snapshot)
        self.assertNotEqual(copy.form_id, one.form_id)
        self.assertNotEqual(copy.stable_id, one.stable_id)
        self.assertEqual(RegistrationSubmission.objects.count(), 0)

    def test_published_edits_make_new_version_and_do_not_reassign(self):
        original = self.published()
        assignment = assign(actor=self.admin, version_id=original.pk, channel="online")
        snapshot = deepcopy(original.snapshot)
        snapshot["questions"][0]["label"] = "New first-name wording"
        draft = save_draft(actor=self.admin, form_id=original.form_id,
                           contract=snapshot, expected_revision=revision(original))
        self.assertEqual(draft.version, 2)
        new = publish(actor=self.admin, version_id=draft.pk, expected_revision=revision(draft))
        assignment.refresh_from_db()
        original.refresh_from_db()
        self.assertEqual(assignment.active_version_id, original.pk)
        self.assertNotEqual(original.snapshot, new.snapshot)
        self.assertEqual(original.snapshot["questions"][0]["label"], "First name")
        assign(actor=self.admin, version_id=new.pk, channel="online")
        self.assertEqual(IntakeFormAssignment.objects.get(pk=assignment.pk).active_version_id, new.pk)

    def test_admin_only_configuration_even_for_staff(self):
        with self.assertRaises(PermissionDenied):
            create_form(actor=self.staff, name="Denied")
        version = self.published()
        for action in [lambda: publish(actor=self.staff, version_id=version.pk, expected_revision=revision(version)),
                       lambda: assign(actor=self.staff, version_id=version.pk, channel="online"),
                       lambda: retire(actor=self.staff, form_id=version.form_id)]:
            with self.assertRaises(PermissionDenied):
                action()

    def test_published_contract_immutable_and_hash_verified(self):
        version = self.published()
        version.snapshot["questions"][0]["label"] = "Tampered"
        with self.assertRaises(ValidationError):
            version.save()
        version.refresh_from_db()
        with self.assertRaises(ValidationError):
            version.delete()
        # A raw database corruption must fail integrity checks at use, not become valid intake.
        RegistrationFormVersion.objects.filter(pk=version.pk).update(content_hash="0" * 64)
        version.refresh_from_db()
        with self.assertRaises(ValidationError):
            verify_published(version)

    def test_stale_revision_draft_assign_and_bad_contract_rejected(self):
        draft = create_form(actor=self.admin, name="SYNTHETIC Draft")
        with self.assertRaises(ValidationError):
            assign(actor=self.admin, version_id=draft.pk, channel="online")
        with self.assertRaises(ValidationError):
            publish(actor=self.admin, version_id=draft.pk, expected_revision="stale")
        snapshot = default_contract()
        next(q for q in snapshot["questions"] if q["key"] == "email")["required"] = False
        with self.assertRaises(ValidationError):
            save_draft(actor=self.admin, form_id=draft.form_id, contract=snapshot,
                       expected_revision=revision(draft))

    def test_retired_version_remains_integral_for_pinned_and_queued_work(self):
        version = self.published()
        retire(actor=self.admin, form_id=version.form_id)
        version.refresh_from_db()
        self.assertEqual(verify_published(version, allow_retired=True).pk, version.pk)
        with self.assertRaises(ValidationError):
            verify_published(version)
        with self.assertRaises(ValidationError):
            assign(actor=self.admin, version_id=version.pk, channel="online")
