from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.participants.models import (
    ConsentAcceptance, ConsentDocumentVersion, Participant, RegistrationForm,
    RegistrationFormVersion, RegistrationSubmission,
)
from .communications import deliver_alert, deliver_ticket, emit_incident, queue_ticket_delivery
from .models import (
    Event, EventRegistration, OperationalIncident, OperationalNotificationDelivery,
    OperationalNotificationRule, QrTicket, TicketDelivery,
)
from .operations_views import central_connectivity_status
from .ticket_renderer import ticket_png_bytes


class CommunicationTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(name="Review event", start_at=now, end_at=now + timedelta(days=1), status=Event.Status.UPCOMING)
        self.participant = Participant.objects.create(first_name="Review", last_name="Person", contact_email="review@example.test", contact_phone="5551234567")
        self.registration = EventRegistration.objects.create(event=self.event, participant=self.participant)
        self.ticket = QrTicket.objects.create(registration=self.registration, ticket_number="1234567890", expires_at=now + timedelta(days=1))

    def opt_in(self, *, email=True, sms=False):
        self.registration.ticket_email_opt_in = email
        self.registration.ticket_sms_opt_in = sms
        self.registration.ticket_communication_opt_in_at = timezone.now()
        self.registration.save(update_fields=["ticket_email_opt_in", "ticket_sms_opt_in", "ticket_communication_opt_in_at"])

    def queue_with_consent(self):
        form, _ = RegistrationForm.objects.get_or_create(slug="review-form", defaults={"name": "Review form"})
        version, _ = RegistrationFormVersion.objects.get_or_create(form=form, version=1)
        document, _ = ConsentDocumentVersion.objects.get_or_create(
            key="review-consent", version="1",
            defaults={"title": "Review consent", "body": "Synthetic test policy", "is_approved": True},
        )
        submission, _ = RegistrationSubmission.objects.get_or_create(
            form_version=version, participant=self.participant, event=self.event,
            defaults={"status": RegistrationSubmission.Status.SUBMITTED},
        )
        ConsentAcceptance.objects.get_or_create(participant=self.participant, submission=submission, document=document)
        return queue_ticket_delivery(self.registration)

    def test_registration_forms_require_destination_for_opted_in_channel(self):
        from types import SimpleNamespace
        from .offline_views import OfflineRegistrationForm
        from apps.participants.forms import SnapshotRegistrationForm
        offline = OfflineRegistrationForm({"first_name": "Review", "last_name": "Person", "consent_ack": "on", "ticket_email_opt_in": "on"}, preparation=SimpleNamespace(event=self.event))
        self.assertFalse(offline.is_valid())
        self.assertIn("contact_email", offline.errors)
        public = SnapshotRegistrationForm({"ticket_email_opt_in": "on"}, questions=[], event=self.event)
        self.assertFalse(public.is_valid())
        self.assertIn("ticket_email_opt_in", public.errors)

    def test_no_opt_in_or_consent_means_no_queue(self):
        self.assertEqual(queue_ticket_delivery(self.registration), [])
        self.opt_in()
        self.assertEqual(queue_ticket_delivery(self.registration), [])
        self.assertFalse(TicketDelivery.objects.exists())

    @override_settings(BTW_TICKET_EMAIL_FROM="tickets@btw.example", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_consent_from_another_event_cannot_authorize_ticket_delivery(self):
        self.opt_in()
        form = RegistrationForm.objects.create(slug="consent-review", name="Consent review")
        version = RegistrationFormVersion.objects.create(form=form, version=1)
        document = ConsentDocumentVersion.objects.create(
            key="review-policy", version="1", title="Review policy", body="Synthetic test policy", is_approved=True,
        )
        submission = RegistrationSubmission.objects.create(
            form_version=version, participant=self.participant, event=self.event,
            status=RegistrationSubmission.Status.SUBMITTED,
        )
        ConsentAcceptance.objects.create(participant=self.participant, submission=submission, document=document)
        self.assertEqual(len(queue_ticket_delivery(self.registration)), 1)

        other_event = Event.objects.create(
            name="Other event", start_at=timezone.now(), end_at=timezone.now() + timedelta(days=1),
            status=Event.Status.UPCOMING,
        )
        other_registration = EventRegistration.objects.create(
            event=other_event, participant=self.participant,
            ticket_email_opt_in=True, ticket_communication_opt_in_at=timezone.now(),
        )
        QrTicket.objects.create(
            registration=other_registration, ticket_number="1234567891",
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.assertEqual(queue_ticket_delivery(other_registration), [])

    @override_settings(BTW_TICKET_EMAIL_FROM="tickets@example.test")
    def test_ticket_queues_once_and_sends_after_reconnection(self):
        self.opt_in()
        self.queue_with_consent()
        self.queue_with_consent()
        self.assertEqual(TicketDelivery.objects.count(), 1)
        delivery = TicketDelivery.objects.get()
        self.assertNotIn("review@example.test", str(delivery.__dict__))
        with patch("apps.events.communications.ConsentAcceptance.objects.filter") as accepted:
            accepted.return_value.exists.return_value = True
            with patch("apps.events.communications._provider_send", side_effect=ConnectionError("offline")):
                deliver_ticket(delivery.pk)
            delivery.refresh_from_db()
            self.assertEqual(delivery.status, TicketDelivery.Status.FAILED)
            self.assertEqual(delivery.attempts, 1)
            delivery.next_attempt_at = timezone.now() - timedelta(seconds=1)
            delivery.save(update_fields=["next_attempt_at"])
            call_command("deliver_communications", verbosity=0)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, TicketDelivery.Status.SENT, delivery.last_error)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("1234567890", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].attachments[0][2], "image/png")
        self.assertTrue(mail.outbox[0].attachments[0][1].startswith(b"\x89PNG"))
        self.assertEqual(mail.outbox[0].attachments[0][1], ticket_png_bytes(self.ticket))
        self.assertEqual(QrTicket.objects.get(pk=self.ticket.pk).ticket_number, "1234567890")
        call_command("deliver_communications", verbosity=0)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(BTW_SMS_PROVIDER_URL="http://sms.example.test/send", BTW_SMS_PROVIDER_TOKEN="test-only")
    def test_sms_provider_rejects_insecure_transport(self):
        from .communications import _provider_send
        with patch("apps.events.communications.request.urlopen") as send:
            with self.assertRaises(ValueError):
                _provider_send("sms", "+15551234567", subject="Ticket", body="1234567890", idempotency_key="test-1")
            send.assert_not_called()

    def test_sms_timeout_is_uncertain_and_not_automatically_retried(self):
        self.opt_in(email=False, sms=True)
        delivery = self.queue_with_consent()[0]
        with patch("apps.events.communications.ConsentAcceptance.objects.filter") as accepted:
            accepted.return_value.exists.return_value = True
            with patch("apps.events.communications._provider_send", side_effect=TimeoutError("timeout")) as send:
                deliver_ticket(delivery.pk)
                call_command("deliver_communications", verbosity=0)
                self.assertEqual(send.call_count, 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, TicketDelivery.Status.UNCERTAIN)

    def test_withdrawn_opt_in_blocks_delivery(self):
        self.opt_in()
        delivery = self.queue_with_consent()[0]
        self.registration.ticket_email_opt_in = False
        self.registration.save(update_fields=["ticket_email_opt_in"])
        deliver_ticket(delivery.pk)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, TicketDelivery.Status.FAILED)
        self.assertIn("withdrawn", delivery.last_error)

    def test_global_rule_event_override_and_incident_deduplication(self):
        OperationalNotificationRule.objects.create(kind=OperationalNotificationRule.Kind.SYNC_FAILURE, channels=["email"], recipients=["global@example.test"])
        OperationalNotificationRule.objects.create(event=self.event, kind=OperationalNotificationRule.Kind.SYNC_FAILURE, channels=["email"], recipients=["event@example.test"])
        one = emit_incident(kind=OperationalNotificationRule.Kind.SYNC_FAILURE, fingerprint="trailer-a", summary="Sync offline", event=self.event)
        two = emit_incident(kind=OperationalNotificationRule.Kind.SYNC_FAILURE, fingerprint="trailer-a", summary="Sync offline", event=self.event)
        self.assertEqual(one.pk, two.pk)
        self.assertEqual(OperationalIncident.objects.count(), 1)
        self.assertEqual(OperationalIncident.objects.get().occurrences, 2)
        delivery = OperationalNotificationDelivery.objects.get()
        self.assertEqual(delivery.destination, "event@example.test")
        with patch("apps.events.communications._provider_send", return_value="provider-1") as send:
            deliver_alert(delivery.pk)
            deliver_alert(delivery.pk)
            self.assertEqual(send.call_count, 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, OperationalNotificationDelivery.Status.SENT)

    def test_alert_rule_rejects_missing_recipients(self):
        from django.core.exceptions import ValidationError
        rule = OperationalNotificationRule(kind=OperationalNotificationRule.Kind.OTHER, enabled=True, channels=["email"], recipients=[])
        with self.assertRaises(ValidationError):
            rule.full_clean()

    def test_alerts_queue_offline_and_retry(self):
        OperationalNotificationRule.objects.create(kind=OperationalNotificationRule.Kind.BACKUP_FAILURE, channels=["sms"], recipients=["+15551234567"])
        emit_incident(kind=OperationalNotificationRule.Kind.BACKUP_FAILURE, fingerprint="trailer-a", summary="Backup failed")
        delivery = OperationalNotificationDelivery.objects.get()
        with patch("apps.events.communications._provider_send", side_effect=ConnectionError("offline")):
            deliver_alert(delivery.pk)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, OperationalNotificationDelivery.Status.FAILED)
        delivery.next_attempt_at = timezone.now() - timedelta(seconds=1)
        delivery.save(update_fields=["next_attempt_at"])
        with patch("apps.events.communications._provider_send", return_value="accepted"):
            call_command("deliver_communications", verbosity=0)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, OperationalNotificationDelivery.Status.SENT)

    def test_dashboard_permissions_and_live_reachability(self):
        url = reverse("events:operations-dashboard")
        self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user(username="staff-no-permission", password="test-only", is_staff=True)
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(url).status_code, 403)
        admin = get_user_model().objects.create_superuser(username="operations-admin", password="test-only")
        self.client.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
        with patch("apps.events.operations_views.central_connectivity_status", return_value="Unavailable now"):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unavailable now")
        self.assertContains(response, "Last accepted sync")

    def test_retry_requires_permission_reason_and_audit(self):
        self.opt_in()
        delivery = self.queue_with_consent()[0]
        delivery.status = TicketDelivery.Status.FAILED
        delivery.save(update_fields=["status"])
        url = reverse("events:retry-ticket-delivery", args=[delivery.pk])
        self.assertEqual(self.client.post(url, {"reason": "reviewed"}).status_code, 302)
        user = get_user_model().objects.create_user(username="retry-no-permission", password="test-only", is_staff=True)
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.post(url, {"reason": "reviewed"}).status_code, 403)
        admin = get_user_model().objects.create_superuser(username="retry-admin", password="test-only")
        self.client.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.post(url, {}).status_code, 400)
        self.assertEqual(self.client.post(url, {"reason": "Confirmed provider did not send"}).status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, TicketDelivery.Status.QUEUED)
        self.assertTrue(AuditLog.objects.filter(action="ticket.delivery.retry", object_id=str(delivery.pk)).exists())
        self.assertEqual(self.client.post(url, {"reason": "again"}).status_code, 400)

    def test_permanent_failure_is_not_retried_without_staff_action(self):
        self.opt_in()
        delivery = self.queue_with_consent()[0]
        delivery.status = TicketDelivery.Status.FAILED
        delivery.next_attempt_at = None
        delivery.save(update_fields=["status", "next_attempt_at"])
        with patch("apps.events.communications._provider_send") as provider:
            self.assertIsNone(deliver_ticket(delivery.pk))
        provider.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, TicketDelivery.Status.FAILED)

    def test_backup_failure_creates_incident(self):
        from .backup_service import create_database_backup
        from .models import BackupRecord, TrailerInstance

        trailer = TrailerInstance.objects.create(identity="alert-review-trailer", display_name="Review trailer")
        with patch("apps.events.backup_service._backup_root", side_effect=RuntimeError("Synthetic storage failure")):
            with self.assertRaises(Exception):
                create_database_backup(trailer)
        self.assertTrue(BackupRecord.objects.filter(trailer=trailer, status=BackupRecord.Status.FAILED).exists())
        self.assertTrue(OperationalIncident.objects.filter(kind=OperationalNotificationRule.Kind.BACKUP_FAILURE).exists())

    def test_exhausted_ticket_ranges_create_event_incident(self):
        from types import SimpleNamespace
        from uuid import uuid4
        from django.http import HttpResponse
        from .offline import TicketRangeExhausted

        admin = get_user_model().objects.create_superuser(username="ticket-range-admin", password="test-only")
        self.client.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
        preparation_id = uuid4()
        preparation = SimpleNamespace(pk=preparation_id, event=self.event)
        with patch("apps.events.offline_views.get_object_or_404", return_value=preparation), \
             patch("apps.events.offline_views.OfflineRegistrationForm") as form_class, \
             patch("apps.events.offline_views.create_offline_registration", side_effect=TicketRangeExhausted("All offline ticket ranges are exhausted.")), \
             patch("apps.events.offline_views.render", return_value=HttpResponse("Range exhausted")):
            form_class.return_value.is_valid.return_value = True
            form_class.return_value.question_definitions = []
            form_class.return_value.cleaned_data = {}
            response = self.client.post(reverse("events:offline-register", args=[preparation_id]), {})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(OperationalIncident.objects.filter(
            kind=OperationalNotificationRule.Kind.TICKET_EXHAUSTED, event=self.event,
        ).exists())

    @override_settings(BTW_CENTRAL_SYNC_URL="https://central.example.test/events/sync/ingest/")
    def test_live_connectivity_has_safe_timeout(self):
        with patch("apps.events.operations_views.urlrequest.urlopen") as urlopen:
            urlopen.side_effect = OSError("offline")
            self.assertEqual(central_connectivity_status(), "Unavailable now")
            self.assertEqual(urlopen.call_args.kwargs["timeout"], 2)
