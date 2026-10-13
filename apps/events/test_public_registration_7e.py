"""Build 7E public event registration and link safety."""
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.participants.models import (Participant, ConsentAcceptance, ConsentDocumentVersion,
                                      RegistrationFormVersion, RegistrationSubmission)
from apps.participants.registration_views import standard_form
from .models import (Attendance, Event, EventGroup, EventPublicSlug, EventRegistration,
                     EventRegistrationQuestion, QrTicket, TicketDelivery)
from .services import resolve_ticket
from .ticketing import qr_payload


class PublicRegistrationTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.document = ConsentDocumentVersion.objects.create(
            key="btw-intake", version="synthetic-7e", title="Synthetic review consent",
            body="Synthetic test policy text only.", is_approved=True, content_hash="a" * 64,
            effective_at=now - timedelta(days=1),
        )
        form = standard_form()
        self.version = RegistrationFormVersion.objects.create(
            form=form, version=1, status=RegistrationFormVersion.Status.PUBLISHED,
            snapshot={"questions": [
                {"key": "first_name", "label": "First name", "field_type": "short_text", "required": True},
                {"key": "last_name", "label": "Last name", "field_type": "short_text", "required": True},
                {"key": "contact_phone", "label": "Phone", "field_type": "short_text", "required": True},
                {"key": "contact_email", "label": "Email", "field_type": "short_text", "required": False},
                {"key": "date_of_birth", "label": "Date of birth", "field_type": "date", "required": True},
                {"key": "life_stage", "label": "Life stage", "field_type": "single_choice", "required": True,
                 "options": [{"value": "working", "label": "Working"}]},
            ]}, published_at=now,
        )
        self.event = Event.objects.create(name="Macon Career Day", status=Event.Status.UPCOMING,
            start_at=now + timedelta(days=1), end_at=now + timedelta(days=2),
            public_registration_status="open", public_consent_document=self.document,
            group_mode="required", group_label="Session")
        self.group = EventGroup.objects.create(event=self.event, name="Morning", capacity=2)
        EventPublicSlug.objects.create(event=self.event, slug="macon-career-day", is_current=True)
        self.staff = get_user_model().objects.create_superuser(username="public-admin", email="admin@example.test", password="test")
        self.url = reverse("public-event-registration", kwargs={"slug": "macon-career-day"})

    def payload(self, **updates):
        data = {"registration_request_key": str(uuid4()), "event": str(self.event.pk), "consent_document_id": str(self.document.pk),
                "first_name": "Taylor", "last_name": "Example", "contact_phone": "5555550111",
                "contact_email": "taylor@example.test", "date_of_birth": "2000-01-01",
                "life_stage": "working", "event_group": str(self.group.pk), "consent": "on"}
        data.update(updates)
        return data

    def test_authorized_slug_management_redirect_and_audit(self):
        settings_url = reverse("events:public-settings", args=[self.event.pk])
        self.assertEqual(self.client.get(settings_url).status_code, 302)
        self.assertEqual(self.client.post(settings_url, {"slug": "other", "status": "open"}).status_code, 302)
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.post(settings_url, {"slug": "new-macon-link", "status": "open", "consent_document": self.document.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.get(self.url)["Location"], reverse("public-event-registration", kwargs={"slug": "new-macon-link"}))
        self.assertEqual(self.client.get(reverse("events:public-link-qr", args=[self.event.pk]))["Content-Type"], "image/png")
        self.assertTrue(AuditLog.objects.filter(action="event.public_registration.updated").exists())
        other = Event.objects.create(name="Other", status=Event.Status.UPCOMING, start_at=timezone.now(), end_at=timezone.now() + timedelta(days=1))
        conflict = self.client.post(reverse("events:public-settings", args=[other.pk]), {"slug": "macon-career-day", "status": "disabled"})
        self.assertContains(conflict, "already in use")
        self.assertEqual(EventPublicSlug.objects.filter(slug="macon-career-day").count(), 1)

    def test_slug_collision_after_validation_keeps_existing_link(self):
        other = Event.objects.create(name="Other Event", status=Event.Status.UPCOMING,
                                     start_at=timezone.now(), end_at=timezone.now() + timedelta(days=1))
        EventPublicSlug.objects.create(event=other, slug="already-taken", is_current=True)
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        with patch("apps.events.public_views.PublicEventSettingsForm.clean_slug", return_value="already-taken"):
            response = self.client.post(reverse("events:public-settings", args=[self.event.pk]),
                                        {"slug": "already-taken", "status": "open", "consent_document": self.document.pk})
        self.assertContains(response, "already in use")
        self.assertEqual(EventPublicSlug.objects.get(event=self.event, is_current=True).slug, "macon-career-day")
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_closed_draft_and_disabled_links_hide_form(self):
        self.event.public_registration_status = "paused"; self.event.save(update_fields=["public_registration_status"])
        self.assertNotContains(self.client.get(self.url, follow=True), "registration_request_key", status_code=403)
        self.event.public_registration_status = "closed"; self.event.save(update_fields=["public_registration_status"])
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)
        self.event.public_registration_status = "disabled"; self.event.save(update_fields=["public_registration_status"])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.event.public_registration_status = "open"; self.event.status = Event.Status.DRAFT
        self.event.save(update_fields=["public_registration_status", "status"])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(Participant.objects.count(), 0)

    def test_registration_consent_snapshot_ticket_communications_and_repeat(self):
        EventRegistrationQuestion.objects.create(event=self.event, key="trade", label="Preferred trade", position=1, is_required=True)
        response = self.client.get(self.url)
        self.assertContains(response, "Preferred trade")
        self.assertContains(response, self.document.body)
        data = self.payload(event_answer_trade="Welding", ticket_email_opt_in="on", ticket_sms_opt_in="on")
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You are registered")
        registration = EventRegistration.objects.get()
        ticket = QrTicket.objects.get(registration=registration)
        submission = RegistrationSubmission.objects.get()
        acceptance = ConsentAcceptance.objects.get()
        self.assertEqual(registration.source, EventRegistration.Source.ONLINE)
        self.assertEqual(registration.group, self.group)
        self.assertEqual(registration.custom_answers["trade"]["answer"], "Welding")
        self.assertEqual(submission.answer_snapshot["event_questions"][0]["key"], "trade")
        self.assertEqual(submission.source, "public_online")
        self.assertEqual(acceptance.document, self.document)
        self.assertEqual(acceptance.metadata["pdf_sha256"], self.document.content_hash)
        self.assertEqual(TicketDelivery.objects.count(), 2)
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(qr_payload(ticket.ticket_number), "tel:" + ticket.ticket_number)
        self.assertEqual(resolve_ticket("tel:" + ticket.ticket_number, expected_event=self.event).ticket, ticket)
        self.assertTrue(1 <= int(ticket.ticket_number) <= 3999999999)
        self.assertEqual(self.client.post(self.url, data).status_code, 200)
        self.assertEqual(Participant.objects.count(), 1)
        self.assertEqual(EventRegistration.objects.count(), 1)
        self.assertEqual(QrTicket.objects.count(), 1)
        self.assertEqual(TicketDelivery.objects.count(), 2)

    def test_invalid_consent_questions_groups_capacity_and_deadlines(self):
        EventRegistrationQuestion.objects.create(event=self.event, key="trade", label="Preferred trade", is_required=True)
        self.assertEqual(self.client.post(self.url, self.payload(consent="", event_answer_trade="Welding")).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.payload(event_answer_trade="")).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.payload(event_group="", event_answer_trade="Welding")).status_code, 200)
        self.assertEqual(Participant.objects.count(), 0)
        self.group.capacity = 0; self.group.save(update_fields=["capacity"])
        response = self.client.post(self.url, self.payload(event_answer_trade="Welding"))
        self.assertContains(response, "capacity")
        self.assertEqual(Participant.objects.count(), 0)
        self.group.capacity = 2; self.group.registration_deadline_at = timezone.now() - timedelta(seconds=1)
        self.group.save(update_fields=["capacity", "registration_deadline_at"])
        response = self.client.post(self.url, self.payload(event_answer_trade="Welding"))
        self.assertContains(response, "deadline")
        self.assertEqual(Participant.objects.count(), 0)

    def test_future_effective_consent_cannot_be_used(self):
        self.document.effective_at = timezone.now() + timedelta(days=1)
        self.document.save(update_fields=["effective_at"])
        self.assertEqual(self.client.get(self.url).status_code, 503)
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 503)
        self.assertEqual(Participant.objects.count(), 0)
        self.assertEqual(QrTicket.objects.count(), 0)

    def test_no_approved_consent_and_no_opt_in(self):
        self.event.public_consent_document = None; self.event.save(update_fields=["public_consent_document"])
        self.assertEqual(self.client.get(self.url).status_code, 503)
        self.event.public_consent_document = self.document; self.event.save(update_fields=["public_consent_document"])
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TicketDelivery.objects.count(), 0)

    def test_staff_permission_slug_generation_and_preview(self):
        settings_url = reverse("events:public-settings", args=[self.event.pk])
        limited = get_user_model().objects.create_user(username="limited", password="test", is_staff=True)
        self.client.force_login(limited, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(settings_url).status_code, 403)
        self.assertEqual(self.client.post(settings_url, {"slug": "stolen", "status": "open"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("events:public-link-qr", args=[self.event.pk])).status_code, 403)
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        EventPublicSlug.objects.filter(event=self.event).delete()
        response = self.client.post(settings_url, {"slug": "", "status": "open", "consent_document": self.document.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EventPublicSlug.objects.get(event=self.event).slug, "macon-career-day")
        self.event.public_registration_status = "disabled"; self.event.save(update_fields=["public_registration_status"])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertContains(self.client.get(self.url + "?preview=1"), "Tell us about you")
        self.client.logout()
        self.assertEqual(self.client.get(self.url + "?preview=1").status_code, 404)

    def test_consent_change_requires_fresh_review(self):
        page = self.client.get(self.url)
        self.assertContains(page, "consent_document_id")
        replacement = ConsentDocumentVersion.objects.create(key="btw-intake", version="synthetic-new",
            title="New synthetic review consent", body="New synthetic policy text.", is_approved=True)
        self.event.public_consent_document = replacement; self.event.save(update_fields=["public_consent_document"])
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Participant.objects.count(), 0)
        self.assertEqual(ConsentAcceptance.objects.count(), 0)

    def test_event_deadline_and_public_capacity_do_not_create_data(self):
        self.event.registration_deadline_at = timezone.now() - timedelta(seconds=1)
        self.event.save(update_fields=["registration_deadline_at"])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)
        self.event.registration_deadline_at = None
        self.event.registration_capacity = 0
        self.event.save(update_fields=["registration_deadline_at", "registration_capacity"])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(EventRegistration.objects.count(), 0)
        self.assertEqual(QrTicket.objects.count(), 0)

    def test_fixed_public_event_cannot_be_switched_by_post(self):
        other = Event.objects.create(name="Other", status=Event.Status.UPCOMING,
            start_at=timezone.now(), end_at=timezone.now() + timedelta(days=1))
        response = self.client.post(self.url, self.payload(event=str(other.pk)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EventRegistration.objects.get().event, self.event)
        self.assertEqual(RegistrationSubmission.objects.get().event, self.event)
