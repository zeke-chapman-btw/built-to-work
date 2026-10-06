from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.events.models import Attendance, Event, EventRegistration
from apps.events.services import register_participant
from apps.participants.models import Participant
from .models import EventStation, ExperienceActivity, ExperienceSession, Station
from .services import activate_event_context, complete_activity, process_station_scan, skip_activity, start_test_session


class StationExperienceTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(
            name="Build 3 hiring event", code="B3-01",
            start_at=now - timedelta(minutes=10), end_at=now + timedelta(hours=3),
            timezone_name="America/New_York", status=Event.Status.UPCOMING,
        )
        self.other_event = Event.objects.create(
            name="Another event", code="B3-02",
            start_at=now - timedelta(minutes=10), end_at=now + timedelta(hours=3),
            timezone_name="America/New_York", status=Event.Status.UPCOMING,
        )
        self.person = Participant.objects.create(first_name="Riley", last_name="Sample", contact_email="riley@example.test")
        self.staff = get_user_model().objects.create_user("station-staff", password="test-password", is_staff=True, is_active=True)
        self.kiosk = self.add_station("kiosk-01", Station.Type.KIOSK)
        self.duck = self.add_station("duck-01", Station.Type.DUCK)
        self.simulator = self.add_station("sim-01", Station.Type.SIMULATOR)
        self.kiosk_assignment = self.assignment(self.kiosk)
        self.duck_assignment = self.assignment(self.duck)
        self.simulator_assignment = self.assignment(self.simulator)

    def add_station(self, code, station_type):
        return Station.objects.create(code=code, name=code.title(), station_type=station_type)

    def assignment(self, station, event=None):
        return EventStation.objects.create(event=event or self.event, station=station)

    def registration(self, event=None):
        registration, _ = register_participant(event=event or self.event, participant=self.person, actor=self.staff, source=EventRegistration.Source.STAFF)
        return registration

    def scan(self, station, token):
        return process_station_scan(station_code=station.code, token=str(token), actor=self.staff)

    def test_station_context_is_explicit_and_switching_is_audited(self):
        activate_event_context(assignment=self.kiosk_assignment, actor=self.staff)
        self.assertTrue(EventStation.objects.get(pk=self.kiosk_assignment.pk).is_active_context)
        second = self.assignment(self.kiosk, self.other_event)
        activate_event_context(assignment=second, actor=self.staff)
        self.assertFalse(EventStation.objects.get(pk=self.kiosk_assignment.pk).is_active_context)
        self.assertTrue(EventStation.objects.get(pk=second.pk).is_active_context)
        self.assertTrue(AuditLog.objects.filter(action="station.event_context.activated").exists())

    def test_no_active_station_context_does_not_process_scan(self):
        registration = self.registration()
        ticket = registration.tickets.get(is_current=True)
        result = self.scan(self.kiosk, ticket.token)
        self.assertEqual(result["status"], "station_unconfigured")
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(ExperienceSession.objects.count(), 0)

    def test_wrong_event_ticket_is_rejected_without_check_in_or_session(self):
        activate_event_context(assignment=self.kiosk_assignment, actor=self.staff)
        registration = self.registration(self.other_event)
        ticket = registration.tickets.get(is_current=True)
        self.event.allow_station_auto_check_in = True
        self.event.save(update_fields=("allow_station_auto_check_in",))
        result = self.scan(self.kiosk, ticket.token)
        self.assertEqual(result["status"], "wrong_event")
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(ExperienceSession.objects.count(), 0)

    def test_auto_check_in_is_off_by_default_and_staff_check_in_is_required(self):
        activate_event_context(assignment=self.kiosk_assignment, actor=self.staff)
        registration = self.registration()
        ticket = registration.tickets.get(is_current=True)
        result = self.scan(self.kiosk, ticket.token)
        self.assertEqual(result["status"], "staff_check_in_required")
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(ExperienceSession.objects.count(), 0)

    def test_auto_check_in_starts_official_session_once_and_records_source(self):
        activate_event_context(assignment=self.kiosk_assignment, actor=self.staff)
        self.event.allow_station_auto_check_in = True
        self.event.save(update_fields=("allow_station_auto_check_in",))
        registration = self.registration()
        ticket = registration.tickets.get(is_current=True)
        first = self.scan(self.kiosk, ticket.token)
        second = self.scan(self.kiosk, ticket.token)
        self.assertEqual(first["status"], "activity_in_progress")
        self.assertEqual(second["status"], "activity_in_progress")
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(Attendance.objects.get().source, Attendance.Source.STATION)
        self.assertEqual(ExperienceSession.objects.filter(registration=registration).count(), 1)
        self.assertEqual(ExperienceActivity.objects.filter(session__registration=registration).count(), 2)
        self.assertEqual(ExperienceActivity.objects.get(session__registration=registration, activity=ExperienceActivity.Activity.KIOSK).status, ExperienceActivity.Status.IN_PROGRESS)

    def test_experience_progression_and_completion_are_ordered_and_idempotent(self):
        self.event.allow_station_auto_check_in = True
        self.event.save(update_fields=("allow_station_auto_check_in",))
        registration = self.registration()
        ticket = registration.tickets.get(is_current=True)
        for assignment in (self.kiosk_assignment, self.duck_assignment, self.simulator_assignment):
            activate_event_context(assignment=assignment, actor=self.staff)
        blocked = self.scan(self.duck, ticket.token)
        self.assertEqual(blocked["status"], "game_not_configured")
        self.scan(self.kiosk, ticket.token)
        session = ExperienceSession.objects.get(registration=registration)
        first, changed = complete_activity(session=session, activity_name=ExperienceActivity.Activity.KIOSK, actor=self.staff)
        self.assertTrue(changed)
        _, changed_again = complete_activity(session=session, activity_name=ExperienceActivity.Activity.KIOSK, actor=self.staff)
        self.assertFalse(changed_again)
        self.assertEqual(self.scan(self.duck, ticket.token)["status"], "game_not_configured")
        self.scan(self.simulator, ticket.token)
        complete_activity(session=session, activity_name=ExperienceActivity.Activity.SIMULATOR, actor=self.staff)
        session.refresh_from_db()
        self.assertIsNotNone(session.completed_at)
        self.assertEqual(session.activities.filter(status=ExperienceActivity.Status.COMPLETED).count(), 2)

    def test_skip_requires_reason_and_records_staff_audit(self):
        session = start_test_session(event=self.event, mode=ExperienceSession.Mode.DEMO, actor=self.staff)
        with self.assertRaises(ValidationError):
            skip_activity(session=session, activity_name=ExperienceActivity.Activity.KIOSK, actor=self.staff, reason=" ")
        activity, changed = skip_activity(session=session, activity_name=ExperienceActivity.Activity.KIOSK, actor=self.staff, reason="Station unavailable")
        self.assertTrue(changed)
        self.assertEqual(activity.status, ExperienceActivity.Status.SKIPPED)
        self.assertEqual(activity.skipped_by, self.staff)
        self.assertEqual(activity.skip_reason, "Station unavailable")
        self.assertTrue(AuditLog.objects.filter(action="experience.activity.skipped", reason="Station unavailable").exists())

        from apps.stations.services import _prior_steps_complete

        self.assertIsNone(_prior_steps_complete(session, ExperienceActivity.Activity.SIMULATOR))

    def test_test_and_demo_sessions_are_not_linked_to_people(self):
        test_session = start_test_session(event=self.event, mode=ExperienceSession.Mode.STAFF_TEST, actor=self.staff)
        demo_session = start_test_session(event=self.event, mode=ExperienceSession.Mode.DEMO, actor=self.staff)
        self.assertIsNone(test_session.registration_id)
        self.assertIsNone(test_session.participant_id)
        self.assertIsNone(demo_session.registration_id)
        self.assertEqual(ExperienceSession.objects.filter(mode=ExperienceSession.Mode.OFFICIAL).count(), 0)


    def test_kiosk_http_requires_staff_check_in_without_creating_experience(self):
        registration = self.registration()
        ticket = registration.tickets.get(is_current=True)
        self.event.allow_station_auto_check_in = False
        self.event.save(update_fields=("allow_station_auto_check_in",))
        activate_event_context(assignment=self.kiosk_assignment, actor=self.staff)
        response = self.client.post(
            reverse("stations:kiosk", kwargs={"station_code": self.kiosk.code}),
            {"token": ticket.token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please ask a Built to Work team member for help before continuing.")
        self.assertNotContains(response, "staff_check_in_required")
        self.assertNotContains(response, ticket.token)
        for internal_value in ("staff_check_in_required", "Staff_Check_In_Required", "Traceback", "ValidationError", "IntegrityError"):
            self.assertNotContains(response, internal_value)
        self.assertEqual(Attendance.objects.filter(registration=registration).count(), 0)
        self.assertEqual(
            ExperienceSession.objects.filter(registration=registration).count(), 0
        )


    def test_kiosk_http_requires_staff_check_in_without_creating_experience(self):
        registration = self.registration()
        ticket = registration.tickets.get(is_current=True)
        self.event.allow_station_auto_check_in = False
        self.event.save(update_fields=("allow_station_auto_check_in",))
        activate_event_context(assignment=self.kiosk_assignment, actor=self.staff)
        response = self.client.post(
            reverse("stations:kiosk", kwargs={"station_code": self.kiosk.code}),
            {"token": ticket.token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please ask a Built to Work team member for help before continuing.")
        self.assertNotContains(response, "staff_check_in_required")
        self.assertEqual(Attendance.objects.filter(registration=registration).count(), 0)
        self.assertEqual(
            ExperienceSession.objects.filter(registration=registration).count(), 0
        )


class StationAccessTests(TestCase):
    def test_station_views_require_active_staff(self):
        response = self.client.get(reverse("stations:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", getattr(response, "url", ""))
        regular = get_user_model().objects.create_user("participant-user", password="test-password", is_active=True)
        self.client.force_login(regular, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("stations:list")); self.assertEqual(response.status_code, 403, getattr(response, "url", ""))
        staff = get_user_model().objects.create_user("station-admin", password="test-password", is_staff=True, is_active=True)
        self.client.force_login(staff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("stations:list")).status_code, 200)


class StationOperatorPresentationTests(TestCase):
    def render_status(self, status):
        return render_to_string(
            "stations/operator.html",
            {
                "station": {"code": "KIOSK-01", "name": "Kiosk"},
                "event": {"name": "Development event"},
                "result_status": status,
                "participant_label": None,
                "activities": [],
            },
        )

    def test_staff_check_in_status_has_human_readable_heading(self):
        rendered = self.render_status("staff_check_in_required")
        self.assertIn("Staff check-in required", rendered)
        self.assertNotIn("staff_check_in_required", rendered)
        self.assertNotIn("Staff_Check_In_Required", rendered)

    def test_unmapped_status_is_not_rendered_as_a_machine_code(self):
        rendered = self.render_status("future_internal_status")
        self.assertIn("Station update", rendered)
        self.assertNotIn("future_internal_status", rendered)
