"""Public sign-up uses the form builder; QR codes and trailer signs point at the right form."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, OfflineEventPreparation, TrailerInstance
from .models import ConsentDocumentVersion, IntakeSession
from .intake_lifecycle import create_form, publish, revision, assign, unassign, save_screens
from .intake_screens import public_screens


class SignupLinkTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="synthetic-admin", email="admin@example.test", password="synthetic-test-only")
        self.document = ConsentDocumentVersion.objects.create(
            key="btw-intake", version="synthetic-v1", title="SYNTHETIC consent",
            body="SYNTHETIC test-only terms", content_hash="a" * 64, is_approved=True)
        draft = create_form(actor=self.admin, name="SYNTHETIC Sign-up")
        self.version = publish(actor=self.admin, version_id=draft.pk, expected_revision=revision(draft))
        self.general = assign(actor=self.admin, version_id=self.version.pk, channel="online")
        now = timezone.now()
        self.event = Event.objects.create(name="SYNTHETIC Open Event", status="upcoming",
            start_at=now + timedelta(hours=2), end_at=now + timedelta(hours=8),
            registration_opens_at=now - timedelta(days=1), registration_closes_at=now + timedelta(hours=8),
            public_registration_status="open", public_consent_document=self.document)

    def test_general_link_opens_builder_form(self):
        response = self.client.get(reverse("participants:registration-start"))
        self.assertRedirects(response, reverse("participants:intake-start", args=[self.general.pk]), fetch_redirect_response=False)

    def test_event_without_its_own_form_uses_general_form_linked_to_event(self):
        response = self.client.get(reverse("participants:registration-event-start", args=[self.event.pk]))
        expected = reverse("participants:intake-start", args=[self.general.pk]) + "?event=" + str(self.event.pk)
        self.assertRedirects(response, expected, fetch_redirect_response=False)
        self.client.post(expected, {"event": str(self.event.pk)})
        session = IntakeSession.objects.get()
        self.assertEqual(session.event, self.event)
        self.assertEqual(session.channel, "event")

    def test_event_own_form_wins_and_unassign_falls_back(self):
        own = assign(actor=self.admin, version_id=self.version.pk, channel="event", event=self.event)
        response = self.client.get(reverse("participants:registration-event-start", args=[self.event.pk]))
        self.assertRedirects(response, reverse("participants:intake-start", args=[own.pk]), fetch_redirect_response=False)
        unassign(actor=self.admin, channel="event", event=self.event)
        response = self.client.get(reverse("participants:registration-event-start", args=[self.event.pk]))
        self.assertIn("?event=", response["Location"])

    def test_closed_event_shows_unavailable(self):
        self.event.public_registration_status = "paused"
        self.event.save()
        response = self.client.get(reverse("participants:registration-event-start", args=[self.event.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "open right now", status_code=403)

    def test_trailer_sign_follows_todays_event(self):
        trailer = TrailerInstance.objects.create(identity="synthetic-trailer", display_name="SYNTHETIC Trailer")
        url = reverse("participants:registration-trailer-start", args=[trailer.pk])
        self.assertRedirects(self.client.get(url), reverse("participants:registration-start"), fetch_redirect_response=False)
        OfflineEventPreparation.objects.create(event=self.event, trailer=trailer)
        self.assertRedirects(self.client.get(url), reverse("participants:registration-event-start", args=[self.event.pk]),
                             fetch_redirect_response=False)

    def test_qr_codes_and_forms_page_for_admins(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        image = self.client.get(reverse("participants:signup-qr-image", args=["event", self.event.pk]))
        self.assertEqual(image["Content-Type"], "image/png")
        sheet = self.client.get(reverse("participants:signup-qr-sheet", args=["general"]))
        self.assertContains(sheet, "Scan to sign up")
        page = self.client.get(reverse("participants:intake-config-index"))
        self.assertContains(page, "Where people sign up")
        self.assertContains(page, "SYNTHETIC Open Event")
        self.assertContains(page, ">Open<")

    def test_qr_codes_are_staff_only(self):
        response = self.client.get(reverse("participants:signup-qr-image", args=["general"]))
        self.assertNotEqual(response.status_code, 200)

    def test_wording_goes_into_a_draft_and_published_stays(self):
        latest = self.version
        draft = save_screens(actor=self.admin, form_id=latest.form_id,
                             screens={"welcome_title": "SYNTHETIC Hello", "welcome_challenge": ""},
                             expected_revision=revision(latest))
        self.assertEqual(draft.status, "draft")
        self.assertEqual(public_screens(draft)["welcome_title"], "SYNTHETIC Hello")
        self.assertEqual(public_screens(draft)["welcome_challenge"], "")
        self.version.refresh_from_db()
        self.assertNotEqual(public_screens(self.version)["welcome_title"], "SYNTHETIC Hello")
        # The live welcome page still shows the published wording until the draft is published.
        page = self.client.get(reverse("participants:intake-start", args=[self.general.pk]))
        self.assertNotContains(page, "SYNTHETIC Hello")

    def test_preview_shows_every_screen(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        base = reverse("participants:intake-config-preview-screen", args=[self.version.pk])
        for screen in ["welcome", "1", "2", "3", "4", "5", "finish"]:
            response = self.client.get(base + "?screen=" + screen)
            self.assertEqual(response.status_code, 200, screen)
        self.assertContains(self.client.get(reverse("participants:intake-config-preview", args=[self.version.pk])), "What people see")
