from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts.roles import ROLE_GROUPS, role_configuration
from .models import Guide, GuideProgress

BACKEND = "django.contrib.auth.backends.ModelBackend"


class TrainingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        role_configuration(apply=True)
        User = get_user_model()
        cls.admin = User.objects.create_superuser("training-admin", "ta@example.test", "x-test-only")
        cls.staff = User.objects.create_user("training-staff", password="x-test-only", is_staff=True)
        cls.staff.groups.add(Group.objects.get(name=ROLE_GROUPS["event_staff"]))
        cls.recruiter = User.objects.create_user("training-recruiter", password="x-test-only", is_staff=True)
        cls.recruiter.groups.add(Group.objects.get(name=ROLE_GROUPS["recruiter"]))

    def test_starting_guides_are_there(self):
        self.assertIn("event_staff", Guide.objects.get(slug="ask-to-work-an-event").audiences)
        self.assertGreaterEqual(Guide.objects.get(slug="check-in-at-an-event").steps.count(), 4)

    def test_event_staff_see_their_list_and_can_finish_a_guide(self):
        self.client.force_login(self.staff, backend=BACKEND)
        page = self.client.get(reverse("training:mine"))
        self.assertContains(page, "Ask to work an event")
        self.assertContains(page, "0 of 5 done")
        guide = self.client.get(reverse("training:guide", args=["ask-to-work-an-event"]))
        self.assertContains(guide, "Walk me through it")
        self.assertContains(guide, "training/es-request-days.jpg")
        steps = self.client.get(reverse("training:steps", args=["ask-to-work-an-event"])).json()
        self.assertEqual(len(steps["steps"]), 5)
        self.client.post(reverse("training:done", args=["ask-to-work-an-event"]))
        self.assertTrue(GuideProgress.objects.filter(user=self.staff, guide__slug="ask-to-work-an-event").exists())
        self.assertContains(self.client.get(reverse("training:mine")), "1 of 5 done")

    def test_recruiter_does_not_see_event_staff_only_guides(self):
        self.client.force_login(self.recruiter, backend=BACKEND)
        page = self.client.get(reverse("training:mine"))
        self.assertContains(page, "Find your way around")
        self.assertNotContains(page, "Ask to work an event")
        self.assertEqual(self.client.get(reverse("training:guide", args=["ask-to-work-an-event"])).status_code, 403)

    def test_admin_page_edits_guides_and_shows_progress(self):
        self.client.force_login(self.admin, backend=BACKEND)
        page = self.client.get(reverse("training:manage"))
        self.assertContains(page, "Staff progress")
        self.assertContains(page, "training-staff")
        response = self.client.post(reverse("training:new"), {
            "title": "SYNTHETIC Log a touchpoint", "summary": "Record a call", "audiences": ["recruiter"], "minutes": 2,
            "position": 60, "required": "on", "published": "on", "video_url": "https://youtu.be/abc123",
            "step_title": ["Open the profile", ""], "step_body": ["Find the person", ""], "step_page": ["", ""],
            "step_target": ["", ""], "step_image": ["", ""]})
        self.assertEqual(response.status_code, 302)
        guide = Guide.objects.get(title="SYNTHETIC Log a touchpoint")
        self.assertEqual(guide.steps.count(), 1)
        self.assertEqual(guide.video_embed(), "https://www.youtube.com/embed/abc123")
        self.client.force_login(self.recruiter, backend=BACKEND)
        self.assertContains(self.client.get(reverse("training:mine")), "SYNTHETIC Log a touchpoint")

    def test_only_admins_manage_training(self):
        self.client.force_login(self.recruiter, backend=BACKEND)
        self.assertEqual(self.client.get(reverse("training:manage")).status_code, 403)

    def test_my_stuff_has_a_training_tab(self):
        self.client.force_login(self.staff, backend=BACKEND)
        self.assertContains(self.client.get(reverse("my:schedule")), reverse("training:mine"))
