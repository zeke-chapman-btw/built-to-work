from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.events.models import Event, EventGroup, EventRegistration, EventRegistrationQuestion, QrTicket
from apps.events.services import register_participant, transfer_registration_group, correct_registration_answers
from apps.events.ticket_renderer import bulk_ticket_pdf_bytes
from apps.participants.forms import SnapshotRegistrationForm
from apps.participants.models import Participant


class EventGroupRegistrationTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(name="Group Review", start_at=now, end_at=now + timedelta(days=2), status=Event.Status.UPCOMING,
                                          group_mode="required", group_label="Classroom")
        self.other = Event.objects.create(name="Other Event", start_at=now, end_at=now + timedelta(days=2), status=Event.Status.UPCOMING)
        self.group = EventGroup.objects.create(event=self.event, name="Room A", capacity=1)
        self.other_group = EventGroup.objects.create(event=self.other, name="Other Room")
        self.staff = get_user_model().objects.create_superuser(username="groupadmin", email="groupadmin@example.test", password="test-password")

    def person(self, suffix):
        return Participant.objects.create(first_name="Person", last_name=suffix)

    def test_group_event_isolation_and_required_choice(self):
        person = self.person("One")
        with self.assertRaises(ValidationError):
            register_participant(event=self.event, participant=person, actor=None, source="public")
        with self.assertRaises(ValidationError):
            register_participant(event=self.event, participant=person, actor=None, source="public", group=self.other_group)
        registration, created = register_participant(event=self.event, participant=person, actor=None, source="public", group=self.group)
        self.assertTrue(created)
        self.assertEqual(registration.group_id, self.group.pk)
        self.assertEqual(self.group.registration_count, 1)
        self.assertEqual(QrTicket.objects.filter(registration=registration).count(), 1)

    def test_group_capacity_and_duplicate_submission(self):
        first = self.person("First")
        original, _ = register_participant(event=self.event, participant=first, actor=None, source="public", group=self.group)
        repeated, created = register_participant(event=self.event, participant=first, actor=None, source="public", group=self.group)
        self.assertFalse(created)
        self.assertEqual(original.pk, repeated.pk)
        self.assertEqual(self.group.registration_count, 1)
        with self.assertRaises(ValidationError):
            register_participant(event=self.event, participant=self.person("Second"), actor=None, source="public", group=self.group)
        self.assertEqual(self.event.registrations.count(), 1)

    def test_staff_override_and_group_transfer_keep_ticket(self):
        first, _ = register_participant(event=self.event, participant=self.person("First"), actor=None, source="public", group=self.group)
        second, _ = register_participant(event=self.event, participant=self.person("Second"), actor=self.staff, group=None)
        ticket = second.tickets.get(is_current=True)
        with self.assertRaises(ValidationError):
            transfer_registration_group(registration=second, destination=self.group, actor=self.staff)
        transfer_registration_group(registration=second, destination=self.group, actor=self.staff, override_reason="Classroom exception")
        second.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(second.group_id, self.group.pk)
        self.assertEqual(second.tickets.count(), 1)
        self.assertEqual(ticket.ticket_number, second.tickets.get().ticket_number)
        self.assertTrue(AuditLog.objects.filter(action="event.registration.group_changed", reason="Classroom exception").exists())
        self.assertEqual(self.group.registration_count, 2)

    def test_window_deadline_and_event_capacity(self):
        self.event.group_mode = "optional"
        self.event.registration_capacity = 1
        self.event.registration_opens_at = timezone.now() + timedelta(hours=1)
        self.event.save()
        with self.assertRaises(ValidationError):
            register_participant(event=self.event, participant=self.person("Early"), actor=None, source="public")
        self.event.registration_opens_at = timezone.now() - timedelta(minutes=1)
        self.event.save()
        register_participant(event=self.event, participant=self.person("First"), actor=None, source="public")
        with self.assertRaises(ValidationError):
            register_participant(event=self.event, participant=self.person("Second"), actor=None, source="public")
        register_participant(event=self.event, participant=self.person("Override"), actor=self.staff, override_reason="Approved overflow")
        self.assertTrue(AuditLog.objects.filter(action="event.registration.override", reason="Approved overflow").exists())
        self.event.registration_closes_at = timezone.now() - timedelta(seconds=1)
        self.event.save()
        with self.assertRaises(ValidationError):
            register_participant(event=self.event, participant=self.person("Late"), actor=None, source="public")

    def test_dynamic_event_questions_required_and_staff_correction(self):
        question = EventRegistrationQuestion.objects.create(event=self.event, key="grade", label="Grade", is_required=True)
        form = SnapshotRegistrationForm(questions=[], event=self.event)
        self.assertIn("event_group", form.fields)
        self.assertIn("event_answer_grade", form.fields)
        self.assertTrue(form.fields["event_answer_grade"].required)
        registration, _ = register_participant(event=self.event, participant=self.person("One"), actor=None, source="public",
                                               group=self.group, custom_answers={"grade": {"label": "Grade", "answer": "10"}})
        correct_registration_answers(registration=registration, answers={"grade": "11"}, actor=self.staff, reason="Staff correction")
        registration.refresh_from_db()
        self.assertEqual(registration.custom_answers["grade"], "11")
        self.assertTrue(AuditLog.objects.filter(action="event.registration.answers_corrected", reason="Staff correction").exists())

    def test_bulk_pdf_reuses_existing_number_and_is_staff_only(self):
        registration, _ = register_participant(event=self.event, participant=self.person("One"), actor=None, source="public", group=self.group)
        ticket = registration.tickets.get()
        pdf = bulk_ticket_pdf_bytes([ticket])
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(QrTicket.objects.count(), 1)
        url = reverse("events:bulk_tickets", kwargs={"event_id": self.event.pk})
        self.assertEqual(self.client.post(url, {"scope": "all"}).status_code, 302)
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.post(url, {"scope": "all"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(QrTicket.objects.get().ticket_number, ticket.ticket_number)
        self.assertTrue(AuditLog.objects.filter(action="event.tickets.bulk_reprinted").exists())
