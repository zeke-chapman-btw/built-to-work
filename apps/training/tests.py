from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
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
        self.assertContains(page, "0 of 6 done")
        guide = self.client.get(reverse("training:guide", args=["ask-to-work-an-event"]))
        self.assertContains(guide, "Walk me through it")
        self.assertContains(guide, "training/es-request-days.jpg")
        steps = self.client.get(reverse("training:steps", args=["ask-to-work-an-event"])).json()
        self.assertEqual(len(steps["steps"]), 5)
        self.client.post(reverse("training:done", args=["ask-to-work-an-event"]))
        self.assertTrue(GuideProgress.objects.filter(user=self.staff, guide__slug="ask-to-work-an-event").exists())
        self.assertContains(self.client.get(reverse("training:mine")), "1 of 6 done")

    def test_recruiter_does_not_see_event_staff_only_guides(self):
        self.client.force_login(self.recruiter, backend=BACKEND)
        page = self.client.get(reverse("training:mine"))
        self.assertContains(page, "Find your way around")
        self.assertNotContains(page, "Ask to work an event")
        self.assertEqual(self.client.get(reverse("training:guide", args=["ask-to-work-an-event"])).status_code, 200)

    def test_help_lists_the_guides(self):
        self.client.force_login(self.staff, backend=BACKEND)
        page = self.client.get(reverse("security:help"))
        self.assertContains(page, "Step-by-step guides")
        self.assertContains(page, reverse("training:guide", args=["check-in-at-an-event"]))

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


class GuidesMatchTheAppTests(TestCase):
    """Fails if a guide points at a page, button or screenshot that no longer exists.
    When this fails after a change to the app, update the guide in apps/training/content.py (and a migration
    for guides already in the app), so training always matches what people see."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import html
        from pathlib import Path
        from django.conf import settings
        root = Path(settings.BASE_DIR)
        texts = []
        for pattern in ("templates/**/*.html", "apps/**/templates/**/*.html", "apps/**/*.py", "static/**/*.js"):
            for path in root.glob(pattern):
                if "/tests" in str(path) or path.name.startswith("test") or "training/content.py" in str(path):
                    continue
                try:
                    texts.append(path.read_text(errors="ignore"))
                except OSError:
                    pass
        cls.source = html.unescape("\n".join(texts))
        cls.static = root / "static" / "training"

    def _found(self, target):
        import re
        if target.startswith("text:"):
            return target[5:].strip() in self.source
        names = re.findall(r"\[name=([\w-]+)\]", target)
        classes = re.findall(r"\.([\w-]+)", target)
        attrs = re.findall(r"\[([\w-]+)\]", target)
        if names:
            return all(re.search(rf"""name=["']?{n}\b|["']{n}["']""", self.source) for n in names)
        if classes:
            return all(c in self.source for c in classes)
        if attrs:
            return all(a in self.source for a in attrs)
        return True

    def test_every_guide_step_points_at_something_real(self):
        from django.urls import Resolver404, resolve
        from .content import GUIDES
        problems = []
        for guide in GUIDES:
            for number, step in enumerate(guide["steps"], 1):
                where = f'{guide["title"]} → step {number} ({step["title"]})'
                if step.get("page"):
                    try:
                        resolve(step["page"].split("?")[0])
                    except Resolver404:
                        problems.append(f"{where}: page {step['page']} doesn't exist")
                if step.get("image") and not (self.static / step["image"]).exists():
                    problems.append(f"{where}: screenshot {step['image']} is missing")
                options = [t.strip() for t in step.get("target", "").split("||") if t.strip()]
                if options and not any(self._found(t) for t in options):
                    problems.append(f"{where}: can't find {step['target']!r} anywhere in the app")
        self.assertEqual(problems, [], "Guides out of date:\n" + "\n".join(problems))


@override_settings(SIGN_IN_MESSAGES_ENABLED=True)
class SignInMessageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        role_configuration(apply=True)
        User = get_user_model()
        cls.admin = User.objects.create_superuser("msg-admin", "ma@example.test", "x-test-only-Pw1!")
        cls.staff = User.objects.create_user("prompt-staff", password="x-test-only-Pw1!", is_staff=True)
        cls.staff.groups.add(Group.objects.get(name=ROLE_GROUPS["event_staff"]))
        cls.recruiter = User.objects.create_user("msg-recruiter", password="x-test-only-Pw1!", is_staff=True)
        cls.recruiter.groups.add(Group.objects.get(name=ROLE_GROUPS["recruiter"]))

    def sign_in(self, name):
        self.client.logout()
        self.assertTrue(self.client.login(username=name, password="x-test-only-Pw1!"))

    def test_training_message_after_sign_in_until_answered(self):
        self.sign_in("prompt-staff")
        self.assertContains(self.client.get(reverse("my:schedule")), "Learn the app, step by step")
        from .models import Announcement
        item = Announcement.objects.get(button_url="/my/training/")
        self.client.post(reverse("training:announcement-answer", args=[item.pk]), {"answer": "later", "next": "/my/schedule/"})
        self.assertNotContains(self.client.get(reverse("my:schedule")), "Learn the app, step by step")
        self.sign_in("prompt-staff")
        self.assertContains(self.client.get(reverse("my:schedule")), "Learn the app, step by step")
        response = self.client.post(reverse("training:announcement-answer", args=[item.pk]), {"answer": "go"})
        self.assertRedirects(response, reverse("training:mine"))
        self.sign_in("prompt-staff")
        self.assertNotContains(self.client.get(reverse("my:schedule")), "Learn the app, step by step")

    def test_admin_sends_a_message_to_one_account_type(self):
        from .models import Announcement
        Announcement.objects.all().delete()
        self.sign_in("msg-admin")
        self.assertContains(self.client.get(reverse("training:announcements")), "+ New message")
        response = self.client.post(reverse("training:announcement-new"), {
            "title": "SYNTHETIC New check-in screen", "body": "Check in from the TODAY card.", "audiences": ["event_staff"],
            "button_label": "", "button_url": "", "starts_on": "", "ends_on": "", "active": "on"})
        self.assertEqual(response.status_code, 302)
        self.sign_in("msg-recruiter")
        self.assertNotContains(self.client.get(reverse("my:schedule")), "SYNTHETIC New check-in screen")
        self.sign_in("prompt-staff")
        page = self.client.get(reverse("my:schedule"))
        self.assertContains(page, "SYNTHETIC New check-in screen")
        self.assertContains(page, "Got it")
        item = Announcement.objects.get(title="SYNTHETIC New check-in screen")
        self.client.post(reverse("training:announcement-answer", args=[item.pk]), {"answer": "ok", "next": "/my/schedule/"})
        self.assertEqual(item.seen.count(), 1)
        self.assertNotContains(self.client.get(reverse("my:schedule")), "SYNTHETIC New check-in screen")

    def test_unsafe_button_links_are_refused_and_only_admins_manage(self):
        self.sign_in("msg-admin")
        response = self.client.post(reverse("training:announcement-new"), {
            "title": "x", "body": "y", "audiences": ["event_staff"], "button_label": "Go", "button_url": "javascript:alert(1)",
            "active": "on"})
        self.assertEqual(response.status_code, 200)
        self.sign_in("msg-recruiter")
        self.assertEqual(self.client.get(reverse("training:announcements")).status_code, 403)
