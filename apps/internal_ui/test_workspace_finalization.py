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

    def test_direct_workspaces_and_personal_settings_gear(self):
        response = self.client.get(reverse("internal:home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(item["id"], item["label"]) for item in response.context["navigation"]],
            [("home", "HOME"), ("events", "EVENTS"), ("participants", "PARTICIPANTS"),
             ("tasks", "TASKS"), ("operations", "OPERATIONS"), ("platform-tools", "PLATFORM TOOLS"), ("customers", "CUSTOMERS"), ("campaigns", "CAMPAIGNS"), ("approvals", "APPROVALS"), ("administration", "ADMIN")],
        )
        self.assertEqual(response.context["settings_link"]["url"], reverse("internal:settings"))
        self.assertContains(response, 'aria-label="Personal settings"')
        self.assertContains(response, "data-notification-bell", count=1)  # Native bell disclosure; workspace navigation remains direct.
        self.assertContains(response, reverse("events:event_list"))
        for workspace_id in ("participants", "operations", "platform-tools", "customers", "campaigns", "approvals", "administration"):
            self.assertEqual(self.client.get(reverse("internal:workspace", kwargs={"workspace_id": workspace_id})).status_code, 200)

    def test_assessment_workflow_is_under_operations(self):
        links = visible_workspace_links(self.user, "operations")
        quiz = next(item for item in links if item["label"] == "Quizzes & Tests")
        self.assertEqual(quiz["url"], reverse("assessments:dashboard"))
        self.assertNotIn("assessments", [item["id"] for item in visible_workspaces(self.user)])

    def test_customers_workspace_has_real_directory_without_fake_matches(self):
        response = self.client.get(reverse("internal:workspace", kwargs={"workspace_id": "customers"}))
        self.assertEqual((response.status_code, response.get("Location"), response.wsgi_request.user.is_authenticated), (200, None, True))
        self.assertContains(response, 'class="customer-page"')
        self.assertContains(response, 'No customers found')
        self.assertContains(response, reverse('customers:new'))
        self.assertNotContains(response, 'planned-workspace')
        self.assertNotContains(response, 'Candidate rankings')
        self.assertNotContains(response, "workspace-card")

    def test_settings_is_staff_only_and_uses_existing_self_password_change(self):
        response = self.client.get(reverse("internal:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user.username)
        self.assertContains(response, reverse("accounts:password-change"))
        nonstaff = get_user_model().objects.create_user(username="nonstaff", password="test-only-password")
        self.client.force_login(nonstaff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("internal:settings")).status_code, 403)

    def test_workspace_links_stay_permission_scoped_for_staff(self):
        staff = get_user_model().objects.create_user(username="limited-staff", password="test-only-password", is_staff=True)
        self.client.force_login(staff, backend="django.contrib.auth.backends.ModelBackend")
        nav = visible_workspaces(staff)
        self.assertEqual([item["id"] for item in nav], ["home", "tasks", "approvals"])
        for key in ('customers', 'campaigns'):
            self.assertEqual(self.client.get(reverse('internal:workspace', kwargs={'workspace_id': key})).status_code, 403)
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

    def test_staff_foundation_uses_jost_and_keeps_kiosk_font_available(self):
        response = self.client.get(reverse("internal:home"))
        self.assertContains(response, f'href="{static("internal/css/app.css")}"')
        stylesheet = Path(finders.find("internal/css/app.css")).read_text()
        self.assertRegex(stylesheet, r'@font-face\s*\{[^}]*font-family:\s*"BTW Futura Condensed";[^}]*src:\s*url\("/static/kiosk/fonts/futura-condensed-pt-medium.otf"\)\s*format\("opentype"\)')
        self.assertRegex(stylesheet, r'\.internal-app\s*\{[^}]*--font-display:\s*"BTW Futura Condensed",\s*"Arial Narrow",\s*Arial,\s*sans-serif;')
        self.assertRegex(stylesheet, r'\.nav-item\s*\{[^}]*font-family:\s*var\(--font-display\);')
        font_path = finders.find("kiosk/fonts/futura-condensed-pt-medium.otf")
        self.assertIsNotNone(font_path)
        self.assertEqual(Path(font_path).read_bytes()[:4], b"OTTO")
        foundation = Path(finders.find("internal/css/workspace_ui.css")).read_text()
        self.assertIn('font-family: "Jost";', foundation)
        self.assertIn('src: url("../fonts/Jost-Variable.ttf") format("truetype");', foundation)
        self.assertIn('font-weight: 100 900;', foundation)
        self.assertIn('--font: "Jost", system-ui, -apple-system, "Segoe UI", sans-serif;', foundation)
        self.assertIn('body.internal-app {', foundation)
        self.assertEqual(Path(finders.find("internal/fonts/Jost-Variable.ttf")).read_bytes()[:4], b"\x00\x01\x00\x00")
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", Path(finders.find("internal/fonts/Jost-LICENSE.txt")).read_text())
        html = response.content.decode()
        self.assertGreater(html.rfind("workspace_ui.css"), html.index("internal/css/app.css"))

    def test_settings_link_is_available_on_existing_feature_pages(self):
        for route in ("events:event_list", "participants:staff-directory"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'href="{reverse("internal:settings")}"')
                self.assertContains(response, 'aria-label="Personal settings"')
