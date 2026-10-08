from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event, EventRegistration
from apps.participants.models import Participant, TestParticipantRun
from apps.participants.system_test import resolve_test_qr, simulator_identifier_for
from apps.stations.models import ExperienceSession


class TestParticipantAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="test-admin", email="test-admin@example.test", password="test-pass")
        self.staff = User.objects.create_user(username="test-staff", email="test-staff@example.test", password="test-pass", is_staff=True)
        now = timezone.now()
        self.event = Event.objects.create(name="Current test Event", status=Event.Status.UPCOMING,
            start_at=now - timedelta(minutes=1), end_at=now + timedelta(hours=2))
        self.url = reverse("participants:testing")

    def test_superuser_only_and_reusable_opaque_qr(self):
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"action": "provision"}).status_code, 403)
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.post(self.url, {"action": "provision"}).status_code, 302)
        person = Participant.objects.get(kind=Participant.Kind.SYSTEM_TEST)
        first_token = person.test_qr_token
        self.assertEqual(resolve_test_qr(str(first_token)).pk, person.pk)
        self.assertEqual(simulator_identifier_for(person), "0000000000")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data:image/svg+xml;base64,")
        self.assertNotContains(response, str(first_token))
        self.assertEqual(self.client.post(self.url, {"action": "provision"}).status_code, 302)
        person.refresh_from_db()
        self.assertEqual(person.test_qr_token, first_token)

    def test_start_resume_reset_preserves_nonofficial_history(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.client.post(self.url, {"action": "provision"})
        person = Participant.objects.get(kind=Participant.Kind.SYSTEM_TEST)
        self.assertEqual(self.client.post(self.url, {"action": "start", "event_id": str(self.event.pk)}).status_code, 302)
        run = TestParticipantRun.objects.get(participant=person)
        self.assertEqual(run.experience_session.mode, ExperienceSession.Mode.STAFF_TEST)
        self.assertIsNone(run.experience_session.registration_id)
        self.assertIsNone(run.experience_session.participant_id)
        self.assertFalse(EventRegistration.objects.filter(event=self.event).exists())
        self.client.post(self.url, {"action": "start", "event_id": str(self.event.pk)})
        self.assertEqual(TestParticipantRun.objects.count(), 1)
        self.assertEqual(self.client.post(self.url, {"action": "reset", "run_id": str(run.pk)}).status_code, 200)
        self.assertEqual(TestParticipantRun.objects.count(), 1)
        self.assertEqual(self.client.post(self.url, {"action": "reset", "run_id": str(run.pk), "reason": "Repeat trailer check"}).status_code, 302)
        run.refresh_from_db()
        self.assertIsNotNone(run.reset_at)
        self.assertEqual(run.reset_reason, "Repeat trailer check")
        self.assertEqual(TestParticipantRun.objects.count(), 2)
        self.assertEqual(Participant.objects.filter(kind=Participant.Kind.SYSTEM_TEST).count(), 1)
        self.assertFalse(EventRegistration.objects.filter(event=self.event).exists())
