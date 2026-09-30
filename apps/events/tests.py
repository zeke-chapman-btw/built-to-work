from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.events.models import Attendance, Event, EventRegistration
from apps.events.services import check_in_registration, register_participant, reissue_ticket, resolve_ticket
from apps.participants.models import Participant


class EventWorkflowTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(
            name="Test hiring event", code="TEST-01",
            start_at=now - timedelta(minutes=15), end_at=now + timedelta(hours=4),
            timezone_name="America/New_York", status=Event.Status.UPCOMING,
        )
        self.other_event = Event.objects.create(
            name="Other event", code="TEST-02",
            start_at=now - timedelta(minutes=15), end_at=now + timedelta(hours=4),
            timezone_name="America/New_York", status=Event.Status.UPCOMING,
        )
        self.participant = Participant.objects.create(
            first_name="Taylor", last_name="Example", contact_email="taylor@example.test",
        )
        self.actor = get_user_model().objects.create_user(
            "event-staff", email="staff@example.test", password="test-password", is_staff=True,
        )

    def registration(self):
        registration, created = register_participant(
            event=self.event, participant=self.participant, actor=self.actor,
            source=EventRegistration.Source.STAFF,
        )
        return registration

    def test_event_end_must_not_precede_start(self):
        event = Event(
            name="Invalid", start_at=timezone.now(),
            end_at=timezone.now() - timedelta(hours=1), timezone_name="America/New_York",
        )
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_registration_issues_opaque_ticket_without_attendance(self):
        registration = self.registration()
        ticket = registration.tickets.get(is_current=True)
        self.assertEqual(EventRegistration.objects.filter(event=self.event).count(), 1)
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertNotIn(self.participant.contact_email, str(ticket.token))
        self.assertEqual(ticket.expires_at, self.event.end_at + timedelta(hours=24))
        resolved = resolve_ticket(str(ticket.token), expected_event=self.event)
        self.assertEqual(resolved.status, "valid")
        self.assertEqual(resolved.ticket.registration, registration)

    def test_duplicate_active_registration_is_idempotent(self):
        first = self.registration()
        second, created = register_participant(
            event=self.event, participant=self.participant, actor=self.actor,
            source=EventRegistration.Source.STAFF,
        )
        self.assertFalse(created)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(EventRegistration.objects.filter(event=self.event).count(), 1)

    def test_reissue_revokes_previous_ticket_and_preserves_history(self):
        registration = self.registration()
        original = registration.tickets.get(is_current=True)
        replacement, created = reissue_ticket(
            registration=registration, actor=self.actor, reason="Lost ticket",
            expected_ticket_id=original.pk,
        )
        self.assertTrue(created)
        original.refresh_from_db()
        self.assertFalse(original.is_current)
        self.assertIsNotNone(original.revoked_at)
        self.assertEqual(original.superseded_by_id, replacement.pk)
        self.assertTrue(replacement.is_current)
        self.assertEqual(resolve_ticket(str(original.token)).status, "revoked")

    def test_expired_ticket_is_not_valid(self):
        registration = self.registration()
        ticket = registration.tickets.get(is_current=True)
        ticket.expires_at = timezone.now() - timedelta(minutes=1)
        ticket.save()
        self.assertEqual(resolve_ticket(str(ticket.token)).status, "expired")

    def test_wrong_event_does_not_check_participant_in(self):
        registration = self.registration()
        ticket = registration.tickets.get(is_current=True)
        resolved = resolve_ticket(str(ticket.token), expected_event=self.other_event)
        self.assertEqual(resolved.status, "wrong_event")
        self.assertEqual(Attendance.objects.count(), 0)

    def test_check_in_is_idempotent_and_links_correct_records(self):
        registration = self.registration()
        first, created = check_in_registration(
            registration=registration, actor=self.actor, source=Attendance.Source.STAFF,
        )
        second, created_again = check_in_registration(
            registration=registration, actor=self.actor, source=Attendance.Source.STAFF,
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.event_id, self.event.pk)
        self.assertEqual(first.registration_id, registration.pk)
        self.assertEqual(first.participant_id, self.participant.pk)
        self.assertEqual(Attendance.objects.count(), 1)


class EventAccessTests(TestCase):
    def test_anonymous_user_cannot_access_internal_events(self):
        response = self.client.get("/events/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_participant_account_cannot_access_internal_events(self):
        user = get_user_model().objects.create_user(
            "event-participant", email="participant@example.test", password="test-password",
        )
        self.client.force_login(user)
        response = self.client.get("/events/")
        self.assertIn(response.status_code, (302, 403))
        self.assertNotEqual(response.status_code, 200)
