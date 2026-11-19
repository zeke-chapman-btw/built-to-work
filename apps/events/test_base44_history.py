"""Events → Import base44 history: staff sign-ups and expenses, matched to events and staff already here."""
import json
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.event_reports.models import ManualExpense
from apps.events.models import Event
from apps.staffing.models import StaffAssignment, StaffProfile

DATA = {
    "assignments": [
        {"event": {"name": "Southeast Georgia Skills Challenge", "start_date": "2026-11-11"},
         "staff": {"name": "Pat Lee", "email": "PAT@example.test"}, "role": "driver", "status": "confirmed", "work_days": ["2026-11-11"]},
        {"event": {"name": "Southeast Georgia Skills Challenge", "start_date": "2026-11-11"},
         "staff": {"name": "Sam Roe", "phone": "706-555-0100"}, "role": "staff", "status": "declined", "work_days": ["2026-11-11"]},
        {"event": {"name": "Somewhere Else", "start_date": "2026-11-01"}, "staff": {"name": "Pat Lee"}, "role": "staff",
         "status": "confirmed", "work_days": ["2026-11-01"]},
    ],
    "expenses": [
        {"event": {"name": "Southeast Georgia Skills Challenge", "start_date": "2026-11-11"}, "description": "Truck & Generator",
         "category": "fuel", "amount": 724.08, "expense_date": "2026-11-11", "status": "active", "entered_by": "zeke@example.test"},
        {"event": {"name": "Southeast Georgia Skills Challenge", "start_date": "2026-11-11"}, "description": "Old",
         "category": "food", "amount": 5, "expense_date": "2026-11-11", "status": "voided"},
    ],
    "staff": [{"name": "Pat Lee", "email": "pat@example.test", "mailing_city": "Athens", "shirt_size": "xl"}],
}


class HistoryImportTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("hist-admin", "h@example.test", "x-test-only")
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        tz = ZoneInfo("America/New_York")
        self.event = Event.objects.create(name="Southeast Georgia Skills Challenge", status="completed",
                                          start_at=timezone.datetime(2026, 11, 11, 8, tzinfo=tz),
                                          end_at=timezone.datetime(2026, 11, 11, 13, tzinfo=tz))
        self.pat = StaffProfile.objects.create(first_name="Pat", last_name="Lee", email="pat@example.test")
        StaffProfile.objects.create(first_name="Sam", last_name="Roe", phone="(706) 555-0100")

    def test_check_then_save_once(self):
        url = reverse("events:import-base44-history")
        self.client.post(url, {"action": "check", "file": SimpleUploadedFile("h.json", json.dumps(DATA).encode())})
        page = self.client.get(url).content.decode()
        self.assertIn("Not confirmed in base44", page)
        self.assertIn("Event isn&#x27;t here yet", page)
        self.assertIn("Voided in base44", page)
        self.client.post(url, {"action": "save"})
        assignment = StaffAssignment.objects.get(event=self.event, staff=self.pat)
        self.assertEqual(assignment.coverage_role, "driver")
        self.assertEqual(assignment.days.count(), 1)
        expense = ManualExpense.objects.get(event=self.event)
        self.assertEqual((str(expense.amount), expense.category_name), ("724.08", "Fuel"))
        self.pat.refresh_from_db()
        self.assertEqual((self.pat.city, self.pat.shirt_size), ("Athens", "XL"))
        self.client.post(url, {"action": "check", "file": SimpleUploadedFile("h.json", json.dumps(DATA).encode())})
        self.client.post(url, {"action": "save"})
        self.assertEqual(StaffAssignment.objects.filter(event=self.event).count(), 1)
        self.assertEqual(ManualExpense.objects.count(), 1)

    def test_only_administrators(self):
        staff = get_user_model().objects.create_user("hist-staff", password="x-test-only", is_staff=True)
        self.client.force_login(staff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("events:import-base44-history")).status_code, 403)
