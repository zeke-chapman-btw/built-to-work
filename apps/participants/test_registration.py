from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.events.models import Event, EventRegistration, QrTicket

from apps.participants.forms import RegistrationIntakeForm
from apps.participants.models import (
    ConsentAcceptance,
    ConsentDocumentVersion,
    Participant,
    RegistrationFormVersion,
    RegistrationQuestion,
    RegistrationSubmission,
)

from .registration_views import standard_form


class RegistrationIntakeTests(TestCase):
    def setUp(self):
        ConsentDocumentVersion.objects.create(
            key="btw-intake",
            version="1",
            title="Synthetic test consent",
            body="Synthetic test-only consent.",
            is_approved=True,
        )
        form = standard_form()
        questions = list(
            form.questions.filter(is_active=True).values(
                "canonical_key", "label", "help_text", "field_type", "options",
                "visibility_rule", "is_required", "is_protected", "position",
            )
        )
        RegistrationFormVersion.objects.create(
            form=form,
            version=1,
            status=RegistrationFormVersion.Status.PUBLISHED,
            snapshot={"questions": [{"key": q.pop("canonical_key"), **q} for q in questions]},
        )

    def valid_data(self, **overrides):
        data = {
            "first_name": "Taylor",
            "last_name": "Stone",
            "preferred_name": "Tay",
            "contact_email": "taylor@example.com",
            "contact_phone": "555-0101",
            "date_of_birth": "2000-01-02",
            "address_line_1": "1 Main St",
            "address_line_2": "",
            "city": "Atlanta",
            "state": "GA",
            "postal_code": "30301",
            "life_stage": "working",
            "employment_status": "employed",
            "current_industry": "construction",
            "industry_other": "",
            "career_interests": "Heavy equipment",
            "construction_experience": "yes",
            "years_experience": "3",
            "certifications": "OSHA",
            "willing_to_travel": "yes",
            "travel_distance": "50",
            "consent": "on",
        }
        data.update(overrides)
        return data

    def test_underage_is_rejected(self):
        form = RegistrationIntakeForm(self.valid_data(date_of_birth=(date.today() - timedelta(days=13 * 365)).isoformat()))
        self.assertFalse(form.is_valid())
        self.assertIn("14", str(form.errors))

    def test_future_birth_date_is_rejected(self):
        form = RegistrationIntakeForm(self.valid_data(date_of_birth=(date.today() + timedelta(days=1)).isoformat()))
        self.assertFalse(form.is_valid())

    def test_conditional_fields_are_optional_when_not_applicable(self):
        form = RegistrationIntakeForm(self.valid_data(construction_experience="no", years_experience="", willing_to_travel="no", travel_distance=""))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn(form.cleaned_data["years_experience"], (None, ""))
        self.assertIn(form.cleaned_data["travel_distance"], (None, ""))

    def test_registration_persists_participant_profile_and_snapshot(self):
        response = self.client.post(reverse("participants:registration-form"), self.valid_data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Participant.objects.count(), 1)
        self.assertEqual(RegistrationSubmission.objects.count(), 1)

    def test_same_contact_creates_a_new_participant_record(self):
        self.client.post(reverse("participants:registration-form"), self.valid_data())
        self.client.post(reverse("participants:registration-form"), self.valid_data(contact_phone="555-0102", contact_email="other@example.com"))
        self.assertEqual(Participant.objects.count(), 2)

    def test_repeated_event_form_submission_reuses_all_records(self):
        now = timezone.now()
        event = Event.objects.create(name="Retry Event", status=Event.Status.UPCOMING,
                                     start_at=now + timedelta(hours=1), end_at=now + timedelta(hours=4))
        url = reverse("participants:registration-form")
        response = self.client.get(url, {"event": str(event.pk)})
        self.assertEqual(response.status_code, 200)
        key = response.context["registration_request_key"]
        payload = self.valid_data(event=str(event.pk), registration_request_key=key)
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.assertEqual(Participant.objects.count(), 1)
        self.assertEqual(RegistrationSubmission.objects.count(), 1)
        self.assertEqual(ConsentAcceptance.objects.count(), 1)
        self.assertEqual(EventRegistration.objects.count(), 1)
        self.assertEqual(QrTicket.objects.count(), 1)

    def test_ticket_issuance_failure_rolls_back_registration_and_consent(self):
        now = timezone.now()
        event = Event.objects.create(name="Allocation Failure Event", status=Event.Status.UPCOMING,
                                     start_at=now + timedelta(hours=1), end_at=now + timedelta(hours=4))
        url = reverse("participants:registration-form")
        key = self.client.get(url, {"event": str(event.pk)}).context["registration_request_key"]
        payload = self.valid_data(event=str(event.pk), registration_request_key=key)
        with patch("apps.participants.registration_views.register_participant",
                   side_effect=ValidationError("No ticket numbers remain")):
            with self.assertRaises(ValidationError):
                self.client.post(url, payload)
        self.assertEqual(Participant.objects.count(), 0)
        self.assertEqual(RegistrationSubmission.objects.count(), 0)
        self.assertEqual(ConsentAcceptance.objects.count(), 0)
        self.assertEqual(EventRegistration.objects.count(), 0)
        self.assertEqual(QrTicket.objects.count(), 0)

    def test_start_screen_and_event_extension_point(self):
        self.assertEqual(self.client.get(reverse("participants:registration-start")).status_code, 200)


class RegistrationAdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="admin", password="pass", email="admin@example.com")
        self.staff = get_user_model().objects.create_user(username="staff", password="pass")
        self.form = standard_form()
        self.question = self.form.questions.get(canonical_key="first_name")

    def test_admin_routes_require_superuser_for_get_and_post(self):
        self.assertEqual(self.client.get(reverse("participants:registration-forms")).status_code, 302)
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("participants:registration-forms"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post(reverse("participants:registration-form-publish")).status_code, 403)

    def test_protected_question_allows_copy_edit_but_rejects_structure_change(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("participants:registration-question-edit", args=[self.question.pk])
        allowed = {
            "label": "Given name",
            "help_text": "",
            "field_type": "short_text",
            "options": "[]",
            "visibility_rule": "{}",
            "is_required": "on",
            "is_active": "on",
            "position": str(self.question.position),
        }
        response = self.client.post(url, allowed)
        self.assertEqual(response.status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(self.question.label, "Given name")
        self.assertEqual(self.client.post(url, {**allowed, "field_type": "long_text"}).status_code, 400)

    def test_publish_creates_immutable_snapshot(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.post(reverse("participants:registration-form-publish")).status_code, 302)
        version = RegistrationFormVersion.objects.get()
        self.assertEqual(version.status, RegistrationFormVersion.Status.PUBLISHED)
        self.question.label = "Changed later"
        self.question.save()
        version.refresh_from_db()
        self.assertEqual(version.snapshot["questions"][0]["label"], "First name")
