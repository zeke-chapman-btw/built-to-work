"""Synthetic trailer installation, immutable queuing and portable import tests."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import tempfile
import uuid
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.events.models import (Event, TrailerInstance, OfflineEventPreparation, OfflineTicketRange,
    SyncOutboxItem, ParticipantMatchReview, QrTicket, EventRegistration, CentralSyncReceipt)
from apps.events.sync import build_sync_payload
from .models import (TrailerIntakeActivation, RegistrationSubmission, Participant, ConsentAcceptance)
from .intake_lifecycle import assign, save_draft, publish, retire, revision
from .intake_trailer import portable_version, install_version, TRANSPORT_SCHEMA
from .intake_submission import start_session, complete_session
from .intake_sync import import_intake
from .intake_contracts import contract_hash, validate_answers
from .intake_review import decide, review_revision
from .identity import canonical_id
from . import test_intake_submission_11b as baseline


class IntakeTrailer11BTests(TestCase):
    answers = baseline.IntakeSubmission11BTests.answers
    def setUp(self):
        baseline.IntakeSubmission11BTests.setUp(self)
        self.private = tempfile.TemporaryDirectory()
        self.addCleanup(self.private.cleanup)
        self.settings_override = self.settings(PRIVATE_CONSENT_ROOT=self.private.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        pdf = b"%PDF-1.4\nSYNTHETIC test-only consent"
        self.document.content_hash = hashlib.sha256(pdf).hexdigest()
        self.document.original_pdf.save("synthetic-intake.pdf", ContentFile(pdf))
        self.document.save()
        now = timezone.now()
        self.event = Event.objects.create(name="SYNTHETIC trailer event", start_at=now+timedelta(days=1),
            end_at=now+timedelta(days=2), status="upcoming", public_registration_status="open",
            public_consent_document=self.document)
        self.trailer = TrailerInstance.objects.create(identity="synthetic-11b-trailer",
            display_name="SYNTHETIC trailer", credential_hash=make_password("synthetic-fixture-only"))
        self.preparation = OfflineEventPreparation.objects.create(event=self.event, trailer=self.trailer,
            status="active", consent_document=self.document, consent_hash=self.document.content_hash,
            sync_identity=self.trailer.identity)
        for kind, start in [("primary", 9000000000), ("backup", 9000001000)]:
            OfflineTicketRange.objects.create(preparation=self.preparation, kind=kind, start_number=start,
                end_number=start+99, next_number=start)
        self.trailer_assignment = assign(actor=self.admin, version_id=self.version.pk, channel="trailer",
                                         event=self.event, trailer=self.trailer)
        install_version(actor=self.admin, preparation=self.preparation,
                        package=portable_version(self.version), confirmed=True)

    def session(self, **changes):
        session = start_session(assignment=self.trailer_assignment, browser_key="a"*64,
            actor=self.admin, event=self.event, preparation=self.preparation)
        session.answers = self.answers(**changes)
        session.current_step = 4
        session.consent_accepted_at = timezone.now()
        session.save()
        return session

    def payload(self, **changes):
        now = timezone.now()
        answers = validate_answers(self.version.snapshot, self.answers(**changes),
                                   occurred_on=timezone.localdate())["answers"]
        payload = {"schema": TRANSPORT_SCHEMA, "operation_id": str(uuid.uuid4()),
            "event_id": str(self.event.pk), "source_identity": str(uuid.uuid4()),
            "form": {"slug": self.version.form.slug, "version": self.version.version,
                     "stable_id": str(self.version.stable_id), "hash": self.version.content_hash},
            "answers": answers, "metadata": {"channel": "trailer", "created_participant": True},
            "submitted_at": now.isoformat(),
            "consent": {"key": self.document.key, "version": self.document.version,
                "hash": self.document.content_hash, "text_hash": hashlib.sha256(self.document.body.encode()).hexdigest(),
                "accepted_at": now.isoformat()}, "source_actor_uuid": str(self.admin.pk),
            "registration": None, "review": None}
        payload["envelope_hash"] = contract_hash(payload)
        return payload

    def test_explicit_admin_confirmation_and_failed_validation_keep_previous(self):
        before = TrailerIntakeActivation.objects.get().active_version_id
        with self.assertRaises(ValidationError):
            install_version(actor=self.admin, preparation=self.preparation,
                            package=portable_version(self.version), confirmed=False)
        tampered = portable_version(self.version)
        tampered["contract"]["questions"][0]["label"] = "Unapproved change"
        with self.assertRaises(ValidationError):
            install_version(actor=self.admin, preparation=self.preparation, package=tampered, confirmed=True)
        self.assertEqual(TrailerIntakeActivation.objects.get().active_version_id, before)
        self.assertEqual(RegistrationSubmission.objects.count(), 0)

    def test_publication_and_cloud_assignment_do_not_activate_trailer(self):
        session = self.session()
        draft = save_draft(actor=self.admin, form_id=self.version.form_id, contract=self.version.snapshot,
                           expected_revision=revision(self.version))
        updated = publish(actor=self.admin, version_id=draft.pk, expected_revision=revision(draft))
        assign(actor=self.admin, version_id=updated.pk, channel="trailer", event=self.event, trailer=self.trailer)
        self.assertEqual(TrailerIntakeActivation.objects.get().active_version_id, self.version.pk)
        install_version(actor=self.admin, preparation=self.preparation, package=portable_version(updated), confirmed=True)
        self.assertEqual(TrailerIntakeActivation.objects.get().active_version_id, updated.pk)
        result = complete_session(session_id=session.pk, actor=self.admin)
        self.assertEqual(result.form_version_id, self.version.pk)
        later = self.session()
        self.assertEqual(later.form_version_id, updated.pk)

    def test_trailer_completion_uses_reserved_ticket_and_portable_refs(self):
        session = self.session()
        submission = complete_session(session_id=session.pk, actor=self.admin)
        item = SyncOutboxItem.objects.get()
        self.assertEqual(item.operation_type, "intake.completed")
        self.assertEqual(item.payload["form"]["stable_id"], str(self.version.stable_id))
        self.assertNotIn("form_version_id", item.payload["form"])
        self.assertNotIn("document_id", item.payload["consent"])
        self.assertEqual(item.payload["registration"]["ticket"]["number"], "9000000000")
        self.assertEqual(QrTicket.objects.get().ticket_number, "9000000000")
        complete_session(session_id=session.pk, actor=self.admin)
        self.assertEqual(SyncOutboxItem.objects.count(), 1)

    def test_pending_duplicate_queues_valid_intake_without_consuming_a_ticket(self):
        Participant.objects.create(first_name="SYNTHETIC", last_name="Example",
            contact_email="synthetic@example.test", contact_phone="5550109876")
        self.event.registration_capacity = 0
        self.event.save()
        submission = complete_session(session_id=self.session().pk, actor=self.admin)
        self.assertTrue(ParticipantMatchReview.objects.filter(source_submission=submission).exists())
        self.assertFalse(EventRegistration.objects.exists())
        self.assertIsNone(SyncOutboxItem.objects.get().payload["registration"])
        self.assertEqual(self.preparation.ticket_ranges.get(kind="primary").next_number, 9000000000)

    def test_retired_form_queue_is_accepted_and_retry_is_idempotent(self):
        payload = self.payload()
        retire(actor=self.admin, form_id=self.version.form_id)
        one, review = import_intake(event=self.event, preparation=self.preparation, payload=payload)
        two, _ = import_intake(event=self.event, preparation=self.preparation, payload=payload)
        self.assertEqual(one.pk, two.pk)
        self.assertFalse(review)
        self.assertEqual(str(one.participant.identity_uuid), payload["source_identity"])
        self.assertEqual(ConsentAcceptance.objects.filter(submission=one).count(), 1)
        self.assertEqual(one.participant.acquisition.channel, "event")

    def test_new_offline_uuid_associates_without_merging_established_person(self):
        existing = baseline.IntakeSubmission11BTests.legacy(self)
        original = existing.identity_uuid
        payload = self.payload()
        source, review = import_intake(event=self.event, preparation=self.preparation, payload=payload)
        self.assertFalse(review)
        self.assertNotEqual(source.participant_id, existing.pk)
        self.assertEqual(canonical_id(source.participant_id), existing.pk)
        existing.refresh_from_db()
        self.assertEqual(existing.identity_uuid, original)
        self.assertEqual(existing.city, "PROTECTED LEGACY CITY")
        self.assertEqual(str(source.participant.identity_uuid), payload["source_identity"])

    def test_partial_offline_identity_is_retained_for_central_review(self):
        existing = baseline.IntakeSubmission11BTests.legacy(self)
        payload = self.payload(phone="5550109999")
        source, review = import_intake(event=self.event, preparation=self.preparation, payload=payload)
        self.assertTrue(review)
        pending = ParticipantMatchReview.objects.get(source_submission=source)
        self.assertIn("one_contact_only", pending.conflict_reasons)
        self.assertNotEqual(source.participant_id, existing.pk)
        self.assertFalse(EventRegistration.objects.exists())
        self.assertEqual(source.answers["phone"], "5550109999")

    def test_portable_form_hash_consent_and_operation_conflicts_are_rejected(self):
        payload = self.payload()
        source, _ = import_intake(event=self.event, preparation=self.preparation, payload=payload)
        changed = deepcopy(payload)
        changed["answers"]["city"] = "Conflicting retry"
        changed.pop("envelope_hash")
        changed["envelope_hash"] = contract_hash(changed)
        with self.assertRaises(ValidationError):
            import_intake(event=self.event, preparation=self.preparation, payload=changed)
        self.assertEqual(RegistrationSubmission.objects.count(), 1)
        bad = self.payload()
        bad["form"]["stable_id"] = str(uuid.uuid4())
        bad.pop("envelope_hash")
        bad["envelope_hash"] = contract_hash(bad)
        with self.assertRaises(ValidationError):
            import_intake(event=self.event, preparation=self.preparation, payload=bad)

    def test_authenticated_device_api_retains_valid_intake_and_rejects_changed_retry(self):
        payload = self.payload()
        body = {"operation_key": "intake:" + payload["operation_id"], "operation_type": "intake.completed",
            "trailer_identity": self.trailer.identity, "event_id": str(self.event.pk),
            "preparation_id": str(self.preparation.pk), "payload": payload}
        url = reverse("events:sync-ingest")
        response = self.client.post(url, json.dumps(body), content_type="application/json",
                                  HTTP_AUTHORIZATION="Bearer synthetic-fixture-only")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertNotContains(response, "synthetic@example.test")
        body["payload"]["answers"]["city"] = "Changed retry"
        changed = self.client.post(url, json.dumps(body), content_type="application/json",
                                  HTTP_AUTHORIZATION="Bearer synthetic-fixture-only")
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(CentralSyncReceipt.objects.count(), 1)

    def test_cloud_inventory_does_not_implicitly_activate_or_serve_other_trailer(self):
        url = reverse("events:sync-download")
        response = self.client.get(url, {"event_id": self.event.pk, "intake": "1"},
            HTTP_AUTHORIZATION="Bearer synthetic-fixture-only", HTTP_X_TRAILER_IDENTITY=self.trailer.identity)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["versions"][0]["stable_id"], str(self.version.stable_id))
        self.assertEqual(TrailerIntakeActivation.objects.get().active_version_id, self.version.pk)
        denied = self.client.get(url, {"event_id": self.event.pk, "intake": "1"},
            HTTP_AUTHORIZATION="Bearer synthetic-fixture-only", HTTP_X_TRAILER_IDENTITY="another-trailer")
        self.assertEqual(denied.status_code, 401)

    def online_event_submission(self):
        assignment = assign(actor=self.admin, version_id=self.version.pk, channel="event", event=self.event)
        session = start_session(assignment=assignment, browser_key="b"*64, event=self.event)
        session.answers, session.current_step = self.answers(), 4
        session.consent_accepted_at = timezone.now()
        session.save()
        return complete_session(session_id=session.pk)

    def test_automatic_pre_registration_download_does_not_download_form_definitions(self):
        from apps.events.downsync import event_configuration, registration_record
        source = self.online_event_submission()
        registration = EventRegistration.objects.get()
        row = registration_record(registration)
        config = event_configuration(self.preparation)
        self.assertIsNone(config["form"])
        self.assertEqual(config["form_versions"], [])
        self.assertNotIn("contract", row["submission"]["answer_snapshot"])
        self.assertEqual(row["submission"]["form_stable_id"], str(self.version.stable_id))
        self.assertEqual(row["submission"]["intake_operation_id"], str(source.intake_operation_id))

    def test_pre_registration_requires_explicit_installed_version_and_retains_ids(self):
        from apps.events.downsync import registration_record, apply_registration
        from apps.events.models import CentralEventDownload
        source = self.online_event_submission()
        registration = EventRegistration.objects.get()
        ticket = QrTicket.objects.get()
        row = registration_record(registration)
        download = CentralEventDownload.objects.create(event_id=self.event.pk, preparation_id=self.preparation.pk,
                                                       trailer_identity=self.trailer.identity)
        self.assertEqual(apply_registration(download, row).pk, registration.pk)
        source.refresh_from_db()
        self.assertEqual(RegistrationSubmission.objects.count(), 1)
        self.assertEqual(QrTicket.objects.get().token, ticket.token)
        bad = deepcopy(row)
        bad["submission"]["form_stable_id"] = str(uuid.uuid4())
        with self.assertRaises(ValidationError):
            apply_registration(download, bad)
        self.assertEqual(RegistrationSubmission.objects.count(), 1)
        self.assertEqual(EventRegistration.objects.count(), 1)

    def test_historical_form_is_available_for_explicit_history_only_installation(self):
        from .intake_lifecycle import create_form
        self.online_event_submission()
        draft = create_form(actor=self.admin, name="SYNTHETIC replacement independent form")
        newer = publish(actor=self.admin, version_id=draft.pk, expected_revision=revision(draft))
        assign(actor=self.admin, version_id=newer.pk, channel="trailer", event=self.event, trailer=self.trailer)
        response = self.client.get(reverse("events:sync-download"),
            {"event_id": self.event.pk, "intake": "1"},
            HTTP_AUTHORIZATION="Bearer synthetic-fixture-only", HTTP_X_TRAILER_IDENTITY=self.trailer.identity)
        self.assertEqual(response.status_code, 200)
        inventory = {v["stable_id"]: v for v in response.json()["versions"]}
        self.assertFalse(inventory[str(self.version.stable_id)]["activation_allowed"])
        self.assertTrue(inventory[str(newer.stable_id)]["activation_allowed"])
        self.assertEqual(TrailerIntakeActivation.objects.get().active_version_id, self.version.pk)

    def test_staff_assisted_contract_requires_contacts_without_granting_an_account(self):
        from .models import ParticipantAccount
        staff_assignment = assign(actor=self.admin, version_id=self.version.pk, channel="staff")
        session = start_session(assignment=staff_assignment, browser_key="c"*64, actor=self.admin)
        session.answers = self.answers()
        session.current_step, session.consent_accepted_at = 4, timezone.now()
        session.save()
        source = complete_session(session_id=session.pk, actor=self.admin)
        self.assertEqual(source.participant.acquisition.channel, "unknown")
        self.assertEqual(source.form_version_id, self.version.pk)
        self.assertFalse(ParticipantAccount.objects.exists())
        with self.assertRaises(PermissionDenied):
            start_session(assignment=staff_assignment, browser_key="d"*64)

    def test_staged_event_configuration_can_be_prepared_without_downloading_forms(self):
        from apps.events.downsync import event_configuration
        from apps.events.models import CentralEventDownload
        config = event_configuration(self.preparation)
        prepared_id, event_id = uuid.uuid4(), uuid.uuid4()
        config["preparation_id"], config["event_id"] = str(prepared_id), str(event_id)
        config["event"]["id"] = str(event_id)
        config["ranges"] = []
        CentralEventDownload.objects.create(event_id=event_id, preparation_id=prepared_id,
            trailer_identity=self.trailer.identity, config=config)
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("participants:intake-trailer-manage", args=[prepared_id])
        self.assertContains(self.client.get(url), "Prepare trailer")
        self.assertFalse(OfflineEventPreparation.objects.filter(pk=prepared_id).exists())
        response = self.client.post(url, {"action": "prepare", "confirmed": "yes"})
        self.assertEqual(response.status_code, 302, response.content)
        self.assertTrue(OfflineEventPreparation.objects.filter(pk=prepared_id).exists())
        self.assertFalse(TrailerIntakeActivation.objects.filter(event_id=event_id).exists())
        self.assertEqual(self.version.form.versions.count(), 1)

    def registration_payload(self, owner, number="9000000000"):
        now = timezone.now()
        return {"id": str(uuid.uuid4()), "owner_identity": str(owner),
            "registered_at": now.isoformat(), "custom_answers": {},
            "ticket": {"id": str(uuid.uuid4()), "token": str(uuid.uuid4()), "number": number,
                "issued_at": now.isoformat(), "expires_at": (now+timedelta(days=3)).isoformat()}}

    def test_late_ticket_evidence_imports_without_changing_retained_identity(self):
        payload = self.payload()
        original, _ = import_intake(event=self.event, preparation=self.preparation, payload=payload)
        payload["registration"] = self.registration_payload(payload["source_identity"])
        payload.pop("envelope_hash")
        payload["envelope_hash"] = contract_hash(payload)
        retry, _ = import_intake(event=self.event, preparation=self.preparation, payload=payload)
        self.assertEqual(retry.pk, original.pk)
        self.assertEqual(EventRegistration.objects.get().participant_id, original.participant_id)
        review = ParticipantMatchReview.objects.get(source_submission=original)
        self.assertEqual(review.status, "resolved")
        self.assertEqual(review.evidence["registration_status"], "registered")
        import_intake(event=self.event, preparation=self.preparation, payload=payload)
        self.assertEqual(QrTicket.objects.count(), 1)
        self.assertEqual(ConsentAcceptance.objects.filter(submission=original).count(), 1)

    def test_late_conflicting_ticket_is_held_for_authorized_retry(self):
        from .intake_review import retry_registration
        payload = self.payload()
        original, _ = import_intake(event=self.event, preparation=self.preparation, payload=payload)
        payload["registration"] = self.registration_payload(payload["source_identity"], "1000000000")
        payload.pop("envelope_hash")
        payload["envelope_hash"] = contract_hash(payload)
        import_intake(event=self.event, preparation=self.preparation, payload=payload)
        review = ParticipantMatchReview.objects.get(source_submission=original)
        self.assertEqual(review.status, "resolved")
        self.assertEqual(review.evidence["registration_status"], "held")
        retry_registration(actor=self.admin, review_id=review.pk, expected_revision=review_revision(review),
                           reason="SYNTHETIC review retry", confirmed=True)
        review.refresh_from_db()
        self.assertEqual(review.evidence["registration_status"], "held")
        self.assertFalse(QrTicket.objects.exists())

    def test_event_assignment_requires_scope(self):
        with self.assertRaises(ValidationError):
            assign(actor=self.admin, version_id=self.version.pk, channel="event")

    def test_older_offline_answers_do_not_replace_newer_current_fields(self):
        from .models import RegistrationFormVersion, ConsentDocumentVersion
        earlier = timezone.now()-timedelta(days=3)
        RegistrationFormVersion.objects.filter(pk=self.version.pk).update(published_at=earlier, effective_at=earlier)
        ConsentDocumentVersion.objects.filter(pk=self.document.pk).update(published_at=earlier, effective_at=earlier)
        self.version.refresh_from_db()
        self.document.refresh_from_db()
        current_payload = self.payload(city="SYNTHETIC newest current city")
        current, _ = import_intake(event=self.event, preparation=self.preparation, payload=current_payload)
        older_payload = self.payload(city="SYNTHETIC older retained answer")
        older = timezone.now()-timedelta(days=1)
        older_payload["submitted_at"] = older.isoformat()
        older_payload["consent"]["accepted_at"] = older.isoformat()
        older_payload.pop("envelope_hash")
        older_payload["envelope_hash"] = contract_hash(older_payload)
        source, pending = import_intake(event=self.event, preparation=self.preparation, payload=older_payload)
        self.assertFalse(pending)
        current.participant.refresh_from_db()
        self.assertEqual(current.participant.city, "SYNTHETIC newest current city")
        self.assertEqual(source.answers["city"], "SYNTHETIC older retained answer")
        self.assertEqual(canonical_id(source.participant_id), current.participant_id)
        self.assertEqual(RegistrationSubmission.objects.count(), 2)
