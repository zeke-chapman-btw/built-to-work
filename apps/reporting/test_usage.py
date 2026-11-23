"""App usage is counted separately for staff, customers and participants; admins see it."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import UsageAreaDay, UsageSignIn
from .usage import area_for, count_view


@override_settings(USAGE_TRACKING=True)
class UsageTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_superuser("us-admin", "us@example.test", "x-test-only", first_name="SYNTHETIC", last_name="Admin")
        self.staffer = users.objects.create_user("us-staff", "uss@example.test", "x-test-only", is_staff=True)

    def test_pages_and_sign_ins_are_counted_by_audience(self):
        self.client.force_login(self.staffer)  # fires the sign-in signal
        self.assertEqual(UsageSignIn.objects.filter(audience="staff").count(), 1)
        count_view(self.staffer, "staff", "Events")
        count_view(self.staffer, "staff", "Events")
        self.assertEqual(UsageAreaDay.objects.get(user=self.staffer, area="Events").views, 2)
        self.assertEqual(area_for("/participants/portal/"), "Participant portal")
        self.assertEqual(area_for("/participants/directory/"), "Participants")
        self.assertEqual(area_for("/customer/home/"), "Customer portal")

    def test_admin_report_splits_groups(self):
        count_view(self.staffer, "staff", "Events")
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        page = self.client.get(reverse("reporting:usage")).content.decode()
        self.assertIn("App usage", page)
        self.assertIn("Customers", page)
        self.assertIn("Participants", page)
        self.assertIn("us-staff", page)
        empty = self.client.get(reverse("reporting:usage") + "?who=participant").content.decode()
        self.assertIn("No activity yet", empty)

    def test_only_admins(self):
        self.client.force_login(self.staffer)
        self.assertIn(self.client.get(reverse("reporting:usage")).status_code, (302, 403))
