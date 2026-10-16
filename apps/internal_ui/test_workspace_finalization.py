from pathlib import Path
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from .navigation import visible_workspace_links, visible_workspaces


class WorkspaceFinalizationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="workspace-review", email="workspace-review@example.test", password="test-only-password"
        )
        self.client.force_login(self.user, backend="django.contrib.auth.backends.ModelBackend")

    def test_six_direct_workspaces_and_personal_settings_gear(self):
        response = self.client.get(reverse("internal:home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(item["id"], item["label"]) for item in response.context["navigation"]],
            [("home", "HOME"), ("events", "EVENTS"), ("participants", "PARTICIPANTS"),
             ("operations", "OPERATIONS"), ("customers", "CUSTOMERS"), ("administration", "ADMIN")],
        )
        self.assertEqual(response.context["settings_link"]["url"], reverse("internal:settings"))
        self.assertContains(response, 'aria-label="Personal settings"')
        self.assertNotContains(response, "<details")
        for workspace_id in ("events", "participants", "operations", "customers", "administration"):
            self.assertContains(response, reverse("internal:workspace", kwargs={"workspace_id": workspace_id}))

    def test_assessment_workflow_is_under_operations(self):
        links = visible_workspace_links(self.user, "operations")
        quiz = next(item for item in links if item["label"] == "Quizzes and tests")
        self.assertEqual(quiz["url"], reverse("assessments:dashboard"))
        self.assertNotIn("assessments", [item["id"] for item in visible_workspaces(self.user)])

    def test_customers_workspace_is_explicitly_planned_and_has_no_fake_tools(self):
        response = self.client.get(reverse("internal:workspace", kwargs={"workspace_id": "customers"}))
        self.assertEqual((response.status_code, response.get("Location"), response.wsgi_request.user.is_authenticated), (200, None, True))
        self.assertContains(response, "PLANNED")
        self.assertContains(response, "No customer records or management actions are available yet.")
        self.assertNotContains(response, "workspace-card")

    def test_settings_is_staff_only_and_uses_existing_self_password_change(self):
        response = self.client.get(reverse("internal:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user.username)
        self.assertContains(response, reverse("admin:password_change"))
        nonstaff = get_user_model().objects.create_user(username="nonstaff", password="test-only-password")
        self.client.force_login(nonstaff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("internal:settings")).status_code, 403)

    def test_workspace_links_stay_permission_scoped_for_staff(self):
        staff = get_user_model().objects.create_user(username="limited-staff", password="test-only-password", is_staff=True)
        self.client.force_login(staff, backend="django.contrib.auth.backends.ModelBackend")
        nav = visible_workspaces(staff)
        self.assertEqual([item["id"] for item in nav], ["home", "customers"])
        self.assertEqual(self.client.get(reverse("internal:workspace", kwargs={"workspace_id": "participants"})).status_code, 403)
        permission = Permission.objects.get(content_type__app_label="participants", codename="view_participant")
        staff.user_permissions.add(permission)
        staff.refresh_from_db()
        for cache_name in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            if hasattr(staff, cache_name):
                delattr(staff, cache_name)
        nav = visible_workspaces(staff)
        self.assertIn("participants", [item["id"] for item in nav])
        self.assertNotIn("events", [item["id"] for item in nav])
        self.assertEqual(self.client.get(reverse("internal:workspace", kwargs={"workspace_id": "participants"})).status_code, 200)

    def test_assessment_routes_highlight_operations_not_a_removed_workspace(self):
        nav = visible_workspaces(self.user, "/assessments/")
        active = [item["id"] for item in nav if item["active"]]
        self.assertEqual(active, ["operations"])

    def test_staff_stylesheet_loads_bundled_futura_for_navigation(self):
        response = self.client.get(reverse("internal:home"))
        self.assertContains(response, f'href="{static("internal/css/app.css")}"')
        stylesheet = Path(finders.find("internal/css/app.css")).read_text()
        self.assertRegex(stylesheet, r'@font-face\s*\{[^}]*font-family:\s*"BTW Futura Condensed";[^}]*src:\s*url\("/static/kiosk/fonts/futura-condensed-pt-medium.otf"\)\s*format\("opentype"\)')
        self.assertRegex(stylesheet, r'\.internal-app\s*\{[^}]*--font-display:\s*"BTW Futura Condensed",\s*"Arial Narrow",\s*Arial,\s*sans-serif;')
        self.assertRegex(stylesheet, r'\.nav-item\s*\{[^}]*font-family:\s*var\(--font-display\);')
        font_path = finders.find("kiosk/fonts/futura-condensed-pt-medium.otf")
        self.assertIsNotNone(font_path)
        self.assertEqual(Path(font_path).read_bytes()[:4], b"OTTO")

    def test_settings_link_is_available_on_existing_feature_pages(self):
        for route in ("events:event_list", "participants:staff-directory"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'href="{reverse("internal:settings")}"')
                self.assertContains(response, 'aria-label="Personal settings"')
