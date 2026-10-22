"""PostgreSQL-only concurrency checks; no ticket allocator changes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone
from apps.events.models import ParticipantMatchReview, ParticipantIdentityLink
from .models import Participant, RegistrationSubmission, ConsentDocumentVersion
from .intake_contracts import validate_answers
from .intake_defaults import default_contract
from .intake_identity import resolve_intake_identity
from .intake_lifecycle import create_form, save_draft, publish, assign, revision
from .intake_submission import start_session, complete_session
from .intake_review import decide, review_revision
from . import test_intake_contract_11b as contract_tests


@skipUnless(connection.vendor == "postgresql", "Row-lock concurrency requires PostgreSQL")
class IntakeConcurrency11BTests(TransactionTestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "synthetic-concurrency", "concurrency@example.test", "synthetic-fixture-only")
        self.answers = validate_answers(default_contract(), contract_tests.IntakeContract11BTests().answers(),
                                       occurred_on=timezone.localdate())["answers"]

    def concurrent(self, calls):
        barrier = Barrier(len(calls))
        def run(call):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return call()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return [future.result(timeout=30) for future in [pool.submit(run, call) for call in calls]]

    def test_identical_contacts_create_one_identity(self):
        def resolve():
            result = resolve_intake_identity(answers=self.answers, occurred_on=timezone.localdate())
            return result.participant.pk, result.created, result.review_required
        results = self.concurrent([resolve, resolve])
        self.assertEqual(len({row[0] for row in results}), 1)
        self.assertEqual(sum(row[1] for row in results), 1)
        self.assertFalse(any(row[2] for row in results))
        self.assertEqual(Participant.objects.count(), 1)

    def test_overlapping_contact_pairs_serialize_without_silently_associating(self):
        def resolve(phone):
            answers = dict(self.answers, phone=phone)
            result = resolve_intake_identity(answers=answers, occurred_on=timezone.localdate())
            return result.participant.pk, result.review_required
        results = self.concurrent([lambda: resolve("5550101234"), lambda: resolve("5550109999")])
        self.assertEqual(Participant.objects.count(), 2)
        self.assertEqual(sum(row[1] for row in results), 1)
        self.assertNotEqual(results[0][0], results[1][0])

    def test_concurrent_session_completion_has_one_submission_and_consent(self):
        document = ConsentDocumentVersion.objects.create(key="btw-intake", version="synthetic",
            title="SYNTHETIC consent", body="SYNTHETIC test-only", content_hash="a"*64, is_approved=True)
        draft = create_form(actor=self.admin, name="SYNTHETIC concurrency form")
        version = publish(actor=self.admin, version_id=draft.pk, expected_revision=revision(draft))
        assignment = assign(actor=self.admin, version_id=version.pk, channel="online")
        session = start_session(assignment=assignment, browser_key="a"*64)
        session.answers, session.current_step = self.answers, 4
        session.consent_accepted_at = timezone.now()
        session.save()
        def complete():
            return complete_session(session_id=session.pk).pk
        ids = self.concurrent([complete, complete])
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(RegistrationSubmission.objects.count(), 1)
        self.assertEqual(RegistrationSubmission.objects.get().consent_acceptances.count(), 1)

    def test_concurrent_draft_edits_reject_one_stale_revision(self):
        draft = create_form(actor=self.admin, name="SYNTHETIC draft locking")
        expected = revision(draft)
        def edit(label):
            contract = default_contract()
            contract["questions"][0]["label"] = label
            try:
                save_draft(actor=self.admin, form_id=draft.form_id, contract=contract, expected_revision=expected)
                return "saved"
            except ValidationError:
                return "stale"
        self.assertCountEqual(self.concurrent([lambda: edit("SYNTHETIC one"), lambda: edit("SYNTHETIC two")]),
                              ["saved", "stale"])
