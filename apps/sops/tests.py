from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import ROLE_GROUPS
from apps.accounts.staff_preview import SESSION_KEY
from apps.events.models import Event, EventLodging
from apps.staffing.models import StaffAssignment, StaffProfile
from .formatting import render_body
from .models import SOP


def staff_user(username, role=None, **extra):
    user = get_user_model().objects.create_user(username, f"{username}@example.test", "test-only-password", is_staff=True, **extra)
    if role:
        group, _ = Group.objects.get_or_create(name=ROLE_GROUPS[role])
        user.groups.add(group)
    return user


class SOPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("sop-admin", "sop-admin@example.test", "test-only-password")
        cls.event_staff = staff_user("sop-event", "event_staff")
        cls.recruiter = staff_user("sop-recruiter", "recruiter")
        cls.everyone = SOP.objects.create(title="SYNTHETIC Everyone", body="- step", position=1)
        cls.events = SOP.objects.create(title="SYNTHETIC Event only", body="1. setup", audience="event_staff", position=2)
        cls.recruiting = SOP.objects.create(title="SYNTHETIC Recruiter only", body="text", audience="recruiter", position=3)
        cls.retired = SOP.objects.create(title="SYNTHETIC Retired", body="old", is_active=False, position=4)

    def login(self, user):
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")

    def test_staff_see_only_sops_for_their_role(self):
        self.login(self.event_staff)
        response = self.client.get(reverse("sops:index"))
        self.assertContains(response, "SYNTHETIC Everyone")
        self.assertContains(response, "SYNTHETIC Event only")
        self.assertNotContains(response, "SYNTHETIC Recruiter only")
        self.assertNotContains(response, "SYNTHETIC Retired")
        self.assertEqual(self.client.get(reverse("sops:detail", args=[self.recruiting.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("sops:detail", args=[self.events.pk])).status_code, 200)

    def test_only_admins_manage_the_library(self):
        self.login(self.recruiter)
        self.assertEqual(self.client.get(reverse("sops:manage")).status_code, 403)
        self.assertEqual(self.client.post(reverse("sops:action", args=[self.everyone.pk, "retire"])).status_code, 403)
        self.login(self.admin)
        response = self.client.get(reverse("sops:manage"))
        self.assertContains(response, "SYNTHETIC Retired")
        self.assertContains(response, "Put back in use")

    def test_admin_adds_retires_and_reorders(self):
        self.login(self.admin)
        response = self.client.post(reverse("sops:add"), {"title": "SYNTHETIC New", "category": "Trailer", "summary": "",
                                                          "audience": "all", "kind": "page", "body": "# Heading\n- one"})
        self.assertEqual(response.status_code, 302)
        new = SOP.objects.get(title="SYNTHETIC New")
        self.assertEqual(new.created_by, self.admin)
        self.client.post(reverse("sops:action", args=[self.events.pk, "up"]))
        order = list(SOP.objects.filter(is_active=True).order_by("position").values_list("title", flat=True))
        self.assertEqual(order[:2], ["SYNTHETIC Event only", "SYNTHETIC Everyone"])
        self.client.post(reverse("sops:action", args=[self.everyone.pk, "retire"]))
        self.everyone.refresh_from_db()
        self.assertFalse(self.everyone.is_active)

    def test_written_page_needs_steps_and_pdf_needs_storage(self):
        self.login(self.admin)
        response = self.client.post(reverse("sops:add"), {"title": "SYNTHETIC Empty", "audience": "all", "kind": "page", "body": ""})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SOP.objects.filter(title="SYNTHETIC Empty").exists())
        response = self.client.post(reverse("sops:add"), {"title": "SYNTHETIC Pdf", "audience": "all", "kind": "pdf", "body": ""})
        self.assertEqual(response.status_code, 400)

    def test_body_formatting_is_escaped(self):
        html = render_body("# Title\n- <b>bold</b>\n1. first\nplain <script>")
        self.assertIn("<h2>Title</h2>", html)
        self.assertIn("<ul><li>&lt;b&gt;bold&lt;/b&gt;</li></ul>", html)
        self.assertIn("<ol><li>first</li></ol>", html)
        self.assertNotIn("<script>", html)


class MyStuffAndStaffViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("my-admin", "my-admin@example.test", "test-only-password")
        cls.worker = staff_user("my-worker", "event_staff", first_name="Wanda", last_name="Worker")
        cls.profile = StaffProfile.objects.create(first_name="Wanda", last_name="Worker", email="w@example.test", user=cls.worker, is_active=True)
        start = timezone.now() + timedelta(days=5)
        cls.event = Event.objects.create(name="SYNTHETIC Staffed fair", status="upcoming", start_at=start, end_at=start + timedelta(hours=8),
                                         timezone_name="America/New_York", city="Augusta", state="GA")
        StaffAssignment.objects.create(event=cls.event, staff=cls.profile, status="confirmed", role="Kiosk")
        EventLodging.objects.create(event=cls.event, kind="hotel", name="SYNTHETIC Inn", city="Augusta", state="GA", confirmation_number="C-1")

    def test_my_schedule_shows_assignment_and_housing(self):
        self.client.force_login(self.worker, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("my:schedule"))
        self.assertContains(response, "SYNTHETIC Staffed fair")
        self.assertContains(response, "SYNTHETIC Inn")
        self.assertContains(response, "Conf. C-1")
        self.assertNotContains(response, ">My pay<")
        self.profile.my_pay_enabled = True
        self.profile.save()
        self.assertContains(self.client.get(reverse("my:schedule")), ">My pay<")

    def test_admin_views_as_staff_read_only_and_returns(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.post(reverse("internal:staff-preview-enter"), {"user": str(self.worker.pk)}).status_code, 302)
        response = self.client.get(reverse("my:schedule"))
        self.assertContains(response, "Viewing as Wanda Worker")
        self.assertContains(response, "SYNTHETIC Staffed fair")
        self.assertContains(response, "SYNTHETIC Inn")
        # Saving anything is blocked and the admin is told why.
        before = self.profile.updated_at
        response = self.client.post(reverse("internal:settings"), {"first_name": "Changed"}, HTTP_REFERER="/internal/settings/")
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(reverse("internal:settings")), "wasn't saved")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.updated_at, before)
        # Admin-only pages stay closed while viewing as staff.
        self.assertNotEqual(self.client.get(reverse("internal:staff-pay")).status_code, 200)
        self.client.post(reverse("internal:staff-preview-exit"))
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get(reverse("internal:staff-pay")).status_code, 200)

    def test_staff_cannot_start_a_staff_view(self):
        self.client.force_login(self.worker, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.post(reverse("internal:staff-preview-enter"), {"user": str(self.worker.pk)}).status_code, 403)

    def test_staff_pay_switch(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("internal:staff-pay"))
        self.assertContains(response, "Wanda Worker")
        self.client.post(reverse("internal:staff-pay"), {"profile": str(self.profile.pk), "enabled": "1", "revision": self.profile.updated_at.isoformat()})
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.my_pay_enabled)


class EventHousingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("house-admin", "house-admin@example.test", "test-only-password")
        start = timezone.now() + timedelta(days=9)
        cls.event = Event.objects.create(name="SYNTHETIC Housing event", status="upcoming", start_at=start, end_at=start + timedelta(hours=8),
                                         timezone_name="America/New_York")

    def test_add_edit_remove_housing(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        add = reverse("events:housing-add", args=[self.event.pk])
        self.assertContains(self.client.get(add), "Confirmation #")
        response = self.client.post(add, {"kind": "hotel", "name": "SYNTHETIC Lodge", "address_line_1": "1 Main", "city": "Macon", "state": "GA",
                                          "check_in": "2031-01-01", "check_out": "2031-01-03", "confirmation_number": "Z9"})
        self.assertEqual(response.status_code, 302)
        stay = self.event.lodgings.get()
        overview = self.client.get(reverse("events:event_detail", args=[self.event.pk]))
        self.assertContains(overview, "SYNTHETIC Lodge")
        self.assertContains(overview, "Z9")
        edit = reverse("events:housing-edit", args=[self.event.pk, stay.pk])
        bad = self.client.post(edit, {"kind": "hotel", "check_in": "2031-01-03", "check_out": "2031-01-01"})
        self.assertEqual(bad.status_code, 400)
        self.assertContains(bad, "Check-out can", status_code=400)
        self.client.post(edit, {"remove": "1"})
        self.assertFalse(self.event.lodgings.exists())


class EventRegistrationsForStaffTests(MyStuffAndStaffViewTests):
    def test_assigned_staff_can_look_people_up_unassigned_cannot(self):
        from apps.events.models import EventRegistration
        from apps.participants.models import Participant
        person = Participant.objects.create(first_name="SYNTHETIC", last_name="Lookup", contact_email="hidden@example.test")
        EventRegistration.objects.create(event=self.event, participant=person)
        url = reverse("staffing:event-participants", args=[self.event.pk])
        self.client.force_login(self.worker, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(url, {"q": "Lookup"})
        self.assertContains(response, "SYNTHETIC Lookup")
        self.assertNotContains(response, "hidden@example.test")
        other = staff_user("not-assigned", "event_staff")
        StaffProfile.objects.create(first_name="N", last_name="A", email="na@example.test", user=other, is_active=True)
        self.client.force_login(other, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(url).status_code, 403)
