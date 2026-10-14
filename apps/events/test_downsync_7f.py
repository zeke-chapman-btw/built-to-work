"""Build 7F central-to-trailer contract and idempotent import tests."""
import hashlib
from io import BytesIO
from pypdf import PdfWriter
import tempfile
import uuid
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.participants.models import (ConsentAcceptance, ConsentDocumentVersion, Participant,
                                     RegistrationForm, RegistrationFormVersion, RegistrationSubmission)
from .downsync import apply_download, eligible_registrations, event_configuration, registration_record
from .models import (CentralEventDownload, CentralEventDownloadPage, Event, EventGroup,
                     EventRegistration, EventRegistrationQuestion, OfflineEventPreparation,
                     OfflineTicketRange, QrTicket, TrailerInstance)
from .services import resolve_ticket, void_registration


class CentralDownloadTests(TransactionTestCase):
    def setUp(self):
        self.private_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.private_dir.cleanup)
        self.settings_ctx = self.settings(PRIVATE_CONSENT_ROOT=self.private_dir.name)
        self.settings_ctx.enable()
        self.addCleanup(self.settings_ctx.disable)
        now = timezone.now()
        pdf_buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(pdf_buffer)
        self.pdf = pdf_buffer.getvalue()
        self.doc = ConsentDocumentVersion.objects.create(key="test-consent", version="1", title="Synthetic consent",
            body="Synthetic consent text", content_hash=hashlib.sha256(self.pdf).hexdigest(),
            is_approved=True, effective_at=now - timedelta(days=1), published_at=now - timedelta(days=1))
        self.doc.original_pdf.save("synthetic.pdf", ContentFile(self.pdf), save=True)
        form = RegistrationForm.objects.create(slug="standard", name="Standard")
        self.form_version = RegistrationFormVersion.objects.create(form=form, version=1, status="published",
            snapshot={"questions": []}, published_at=now - timedelta(days=1))
        self.event = Event.objects.create(name="Prepared event", start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=2), status="upcoming", public_registration_status="open",
            public_consent_document=self.doc, group_mode="required")
        self.group = EventGroup.objects.create(event=self.event, name="Morning", capacity=10)
        EventRegistrationQuestion.objects.create(event=self.event, key="trade", label="Trade", is_required=True)
        self.trailer = TrailerInstance.objects.create(identity="trailer-7f", display_name="Trailer 7F",
                                                       credential_hash=make_password("test-secret"))
        self.prep = OfflineEventPreparation.objects.create(event=self.event, trailer=self.trailer,
            consent_document=self.doc, consent_hash=self.doc.content_hash, sync_identity=self.trailer.identity)
        OfflineTicketRange.objects.create(preparation=self.prep, kind="primary", start_number=6000000000,
                                          end_number=6000000099, next_number=6000000000)
        OfflineTicketRange.objects.create(preparation=self.prep, kind="backup", start_number=6000000100,
                                          end_number=6000000199, next_number=6000000100)
        self.person = Participant.objects.create(first_name="Taylor", last_name="Example", contact_email="t@example.test")
        self.submission = RegistrationSubmission.objects.create(form_version=self.form_version, participant=self.person,
            event=self.event, status="submitted", answers={"first_name": "Taylor"},
            answer_snapshot={"event_questions": [{"key": "trade"}]}, submitted_at=now,
            source="public_online", idempotency_key="source-7f")
        ConsentAcceptance.objects.create(participant=self.person, submission=self.submission, document=self.doc,
                                        metadata={"pdf_sha256": self.doc.content_hash})
        self.registration = EventRegistration.objects.create(event=self.event, participant=self.person, group=self.group,
            source="online", custom_answers={"trade": {"answer": "Welding"}}, registered_at=now)
        self.ticket = QrTicket.objects.create(registration=self.registration, ticket_number="0000001234",
                                               issued_at=now, expires_at=now + timedelta(days=2))

    def _request(self, *, token="test-secret", identity="trailer-7f", event=None, cursor=""):
        params = {"event_id": str(event or self.event.pk)}
        if cursor:
            params["cursor"] = cursor
        return Client().get(reverse("events:sync-download"), params,
                            HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_TRAILER_IDENTITY=identity)

    def test_scope_credentials_and_cursor(self):
        self.assertEqual(self._request(token="bad").status_code, 401)
        self.assertEqual(self._request(event=uuid.uuid4()).status_code, 403)
        response = self._request()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["records"][0]["ticket"]["ticket_number"], "0000001234")
        self.assertEqual(payload["records"][0]["participant"]["identity_uuid"], str(self.person.identity_uuid))
        self.assertEqual(payload["config"]["consent"]["content_hash"], self.doc.content_hash)
        catalog = Client().get(reverse("events:sync-download"), HTTP_AUTHORIZATION="Bearer test-secret",
                               HTTP_X_TRAILER_IDENTITY="trailer-7f")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(len(catalog.json()["events"]), 1)
        self.assertEqual(Client().get(reverse("events:sync-download")).status_code, 401)

    def test_import_preserves_identity_ticket_consent_and_is_idempotent(self):
        config = event_configuration(self.prep)
        record = registration_record(self.registration)
        expected_event = str(self.event.pk)
        expected_reg = str(self.registration.pk)
        expected_person = str(self.person.identity_uuid)
        expected_token = str(self.ticket.token)
        call_command("flush", verbosity=0, interactive=False)
        download = CentralEventDownload.objects.create(trailer_identity="trailer-7f", event_id=expected_event,
            preparation_id=config["preparation_id"], status="downloading", config=config,
            snapshot_at=timezone.now())
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[record])
        self.assertEqual(apply_download(download), 1)
        self.assertEqual(str(EventRegistration.objects.get().pk), expected_reg)
        self.assertEqual(str(Participant.objects.get().identity_uuid), expected_person)
        self.assertEqual(str(QrTicket.objects.get().token), expected_token)
        self.assertEqual(QrTicket.objects.get().ticket_number, "0000001234")
        self.assertEqual(ConsentAcceptance.objects.count(), 1)
        self.assertEqual(ConsentAcceptance.objects.get().accepted_at.isoformat(), record["consent"]["accepted_at"])
        self.assertEqual(ConsentAcceptance.objects.get().created_at.isoformat(), record["consent"]["created_at"])
        self.assertEqual(RegistrationSubmission.objects.get().answer_snapshot, record["submission"]["answer_snapshot"])
        self.assertEqual(EventRegistration.objects.get().custom_answers, record["registration"]["custom_answers"])
        self.assertEqual(resolve_ticket("tel:0000001234", expected_event=Event.objects.get()).ticket.pk, QrTicket.objects.get().pk)
        download.status = "downloading"
        download.save(update_fields=("status",))
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[record])
        self.assertEqual(apply_download(download), 1)
        self.assertEqual(Participant.objects.count(), 1)
        self.assertEqual(EventRegistration.objects.count(), 1)
        self.assertEqual(QrTicket.objects.count(), 1)
        self.assertEqual(ConsentAcceptance.objects.count(), 1)

    def test_central_reissue_invalidates_old_offline_ticket(self):
        config = event_configuration(self.prep)
        original = registration_record(self.registration)
        old_id = self.ticket.pk
        old_number = self.ticket.ticket_number
        self.ticket.is_current = False
        self.ticket.revoked_at = timezone.now()
        self.ticket.save(update_fields=("is_current", "revoked_at"))
        replacement = QrTicket.objects.create(
            registration=self.registration, ticket_number="0000001235",
            expires_at=timezone.now() + timedelta(days=2))
        self.ticket.superseded_by = replacement
        self.ticket.save(update_fields=("superseded_by",))
        updated = registration_record(self.registration)
        self.assertEqual(updated["prior_tickets"][0]["id"], str(old_id))
        event_id = str(self.event.pk)
        call_command("flush", verbosity=0, interactive=False)
        download = CentralEventDownload.objects.create(
            trailer_identity="trailer-7f", event_id=event_id,
            preparation_id=config["preparation_id"], status="downloading",
            config=config, snapshot_at=timezone.now())
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[original])
        self.assertEqual(apply_download(download), 1)
        self.assertEqual(QrTicket.objects.get(pk=old_id).is_current, True)
        download.status = "downloading"
        download.save(update_fields=("status",))
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[updated])
        self.assertEqual(apply_download(download), 1)
        self.assertEqual(QrTicket.objects.count(), 2)
        self.assertFalse(QrTicket.objects.get(pk=old_id).is_current)
        self.assertEqual(QrTicket.objects.get(pk=old_id).superseded_by_id, replacement.pk)
        self.assertEqual(QrTicket.objects.get(pk=replacement.pk).ticket_number, "0000001235")
        self.assertEqual(resolve_ticket("tel:" + old_number, expected_event=Event.objects.get()).status, "not_found")
        self.assertEqual(resolve_ticket("tel:0000001235", expected_event=Event.objects.get()).ticket.pk, replacement.pk)
        download.status = "downloading"
        download.save(update_fields=("status",))
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[updated])
        self.assertEqual(apply_download(download), 1)
        self.assertEqual(QrTicket.objects.count(), 2)

        revoked = dict(updated)
        revoked["ticket"] = dict(updated["ticket"], revoked_at=timezone.now().isoformat())
        download.status = "downloading"
        download.save(update_fields=("status",))
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[revoked])
        self.assertEqual(apply_download(download), 1)
        self.assertIsNotNone(QrTicket.objects.get(pk=replacement.pk).revoked_at)
        self.assertEqual(resolve_ticket("tel:0000001235", expected_event=Event.objects.get()).status, "revoked")

    def test_central_void_invalidates_downloaded_registration(self):
        config = event_configuration(self.prep)
        active = registration_record(self.registration)
        before_void = timezone.now()
        actor = get_user_model().objects.create_user(
            username="void-actor", password="synthetic", is_staff=True)
        self.assertTrue(void_registration(
            registration=self.registration, actor=actor, reason="Synthetic cancellation"))
        self.registration.refresh_from_db()
        voided = registration_record(self.registration)
        self.assertEqual(voided["registration"]["status"], EventRegistration.Status.VOID)
        self.assertFalse(voided["ticket"]["is_current"])
        self.assertEqual(
            [reg.pk for reg in eligible_registrations(
                self.event, before_void, timezone.now() + timedelta(seconds=1))],
            [self.registration.pk])
        event_id = str(self.event.pk)
        ticket_id = self.ticket.pk
        call_command("flush", verbosity=0, interactive=False)
        download = CentralEventDownload.objects.create(
            trailer_identity="trailer-7f", event_id=event_id,
            preparation_id=config["preparation_id"], status="downloading",
            config=config, snapshot_at=timezone.now())
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[active])
        self.assertEqual(apply_download(download), 1)
        download.status = "downloading"
        download.save(update_fields=("status",))
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[voided])
        self.assertEqual(apply_download(download), 1)
        self.assertEqual(EventRegistration.objects.get().status, EventRegistration.Status.VOID)
        self.assertFalse(QrTicket.objects.get(pk=ticket_id).is_current)
        self.assertIsNotNone(QrTicket.objects.get(pk=ticket_id).revoked_at)
        self.assertEqual(
            resolve_ticket("tel:0000001234", expected_event=Event.objects.get()).status,
            "not_found")
        download.status = "downloading"
        download.save(update_fields=("status",))
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[voided])
        self.assertEqual(apply_download(download), 1)
        self.assertEqual(QrTicket.objects.count(), 1)

    def test_invalid_consent_and_ticket_collision_roll_back(self):
        config = event_configuration(self.prep)
        record = registration_record(self.registration)
        call_command("flush", verbosity=0, interactive=False)
        config["consent"]["content_hash"] = "0" * 64
        download = CentralEventDownload.objects.create(trailer_identity="trailer-7f", event_id=config["event_id"],
            preparation_id=config["preparation_id"], status="downloading", config=config, snapshot_at=timezone.now())
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[record])
        with self.assertRaises(Exception):
            apply_download(download)
        self.assertEqual(Participant.objects.count(), 0)
        self.assertEqual(EventRegistration.objects.count(), 0)

    def test_historical_consent_and_form_versions_survive_policy_update(self):
        now = timezone.now()
        current = ConsentDocumentVersion.objects.create(key="test-consent", version="2", title="New synthetic consent",
            body="New synthetic text", content_hash=hashlib.sha256(self.pdf).hexdigest(),
            is_approved=True, effective_at=now, published_at=now)
        current.original_pdf.save("synthetic-v2.pdf", ContentFile(self.pdf), save=True)
        self.event.public_consent_document = current
        self.event.save(update_fields=("public_consent_document", "updated_at"))
        self.prep.consent_document = current
        self.prep.consent_hash = current.content_hash
        self.prep.save(update_fields=("consent_document", "consent_hash"))
        RegistrationFormVersion.objects.create(form=self.form_version.form, version=2, status="published",
                                                snapshot={"questions": [{"key": "new"}]}, published_at=now)
        config = event_configuration(self.prep)
        record = registration_record(self.registration)
        self.assertEqual(len(config["consent_versions"]), 2)
        self.assertEqual(len(config["form_versions"]), 1)
        call_command("flush", verbosity=0, interactive=False)
        download = CentralEventDownload.objects.create(trailer_identity="trailer-7f", event_id=config["event_id"],
            preparation_id=config["preparation_id"], status="downloading", config=config, snapshot_at=timezone.now())
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[record])
        apply_download(download)
        self.assertEqual(ConsentDocumentVersion.objects.count(), 2)
        self.assertEqual(RegistrationFormVersion.objects.count(), 2)
        self.assertEqual(ConsentAcceptance.objects.get().document.version, "1")
        self.assertEqual(Event.objects.get().public_consent_document.version, "2")

    def test_offline_readiness_blocks_incomplete_central_download(self):
        from .offline import check_offline_readiness
        download = CentralEventDownload.objects.create(trailer_identity="trailer-7f", event_id=self.event.pk,
            preparation_id=self.prep.pk, status="failed", central_registration_count=2, config=event_configuration(self.prep))
        self.assertFalse(check_offline_readiness(self.prep).ready)
        download.status = "ready"
        download.last_success_at = timezone.now()
        download.central_registration_count = 1
        download.save()
        self.assertTrue(check_offline_readiness(self.prep).ready)

    def test_ticket_collision_rejects_entire_import(self):
        config = event_configuration(self.prep)
        record = registration_record(self.registration)
        call_command("flush", verbosity=0, interactive=False)
        now = timezone.now()
        other = Event.objects.create(name="Other event", start_at=now, end_at=now + timedelta(days=1))
        person = Participant.objects.create(first_name="Other", last_name="Person")
        existing_reg = EventRegistration.objects.create(event=other, participant=person)
        QrTicket.objects.create(registration=existing_reg, ticket_number="0000001234", expires_at=now + timedelta(days=1))
        download = CentralEventDownload.objects.create(trailer_identity="trailer-7f", event_id=config["event_id"],
            preparation_id=config["preparation_id"], status="downloading", config=config, snapshot_at=now)
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[record])
        with self.assertRaises(Exception):
            apply_download(download)
        self.assertFalse(Event.objects.filter(pk=config["event_id"]).exists())
        self.assertEqual(Participant.objects.count(), 1)
        self.assertEqual(EventRegistration.objects.count(), 1)
        self.assertEqual(QrTicket.objects.count(), 1)

    def test_newer_local_participant_is_not_overwritten(self):
        config = event_configuration(self.prep)
        record = registration_record(self.registration)
        call_command("flush", verbosity=0, interactive=False)
        download = CentralEventDownload.objects.create(trailer_identity="trailer-7f", event_id=config["event_id"],
            preparation_id=config["preparation_id"], status="downloading", config=config, snapshot_at=timezone.now())
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[record])
        apply_download(download)
        participant = Participant.objects.get()
        participant.first_name = "Locally corrected"
        participant.save()
        download.status = "downloading"
        download.save(update_fields=("status",))
        CentralEventDownloadPage.objects.create(download=download, page_number=0, records=[record])
        with self.assertRaises(Exception):
            apply_download(download)
        self.assertEqual(Participant.objects.get().first_name, "Locally corrected")
        self.assertEqual(EventRegistration.objects.count(), 1)
