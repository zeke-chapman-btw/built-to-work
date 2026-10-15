from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

class InternalShellTests(TestCase):
    def test_anonymous_redirects_to_admin_login(self):
        response = self.client.get(reverse('internal:home'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(reverse('admin:login')))
    def test_staff_can_open_landing(self):
        user = get_user_model().objects.create_user(username='internal-staff', password='valid-test-password', is_staff=True, is_active=True)
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse('internal:home'))
        self.assertEqual(response.status_code, 200, response.get('Location'))
        self.assertContains(response, 'Built to Work')
    def test_authenticated_non_staff_is_forbidden(self):
        user = get_user_model().objects.create_user(username='participant-only', password='valid-test-password', is_staff=False, is_active=True)
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse('internal:home')).status_code, 403)


from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"])
class PortalFoundationTests(TestCase):
    def staff_user(self, username, permission=None, superuser=False):
        user = get_user_model().objects.create_user(username=username, password="test-password", is_staff=True, is_superuser=superuser)
        if permission:
            app, codename = permission
            user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))
        return user

    def test_dashboard_hides_ungranted_workflows(self):
        self.client.force_login(self.staff_user("limited-portal-staff"))
        response = self.client.get(reverse("internal:home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/events/")
        self.assertNotContains(response, "Active registrations")
        self.assertContains(response, "No workflows are assigned yet")

    def test_dashboard_uses_permission_scoped_event_data(self):
        self.client.force_login(self.staff_user("event-reader", ("events", "view_event")))
        response = self.client.get(reverse("internal:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Available in this workspace")
        self.assertContains(response, reverse("events:event_list"))
        self.assertNotContains(response, "Active registrations")

    def test_approvals_navigation_uses_valid_route_for_superuser(self):
        self.client.force_login(self.staff_user("portal-superuser", superuser=True))
        response = self.client.get(reverse("internal:home"))
        self.assertContains(response, reverse("participants:staff-qualification-approvals"))

    def test_dashboard_has_one_primary_heading(self):
        self.client.force_login(self.staff_user("dashboard-heading-staff"))
        response = self.client.get(reverse("internal:home"))
        self.assertEqual(response.content.decode().count("<h1"), 1)

    def test_staff_header_uses_official_horizontal_logo(self):
        self.client.force_login(self.staff_user("dashboard-branding-staff"))
        response = self.client.get(reverse("internal:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="topbar-tagline"')
        self.assertContains(response, 'internal/images/built-to-work-linear.svg')
        self.assertContains(response, "POWERING THE AMERICAN WORKFORCE")
        self.assertNotContains(response, "Staff workspace")

    def test_event_page_does_not_keep_dashboard_active_or_duplicate_title(self):
        self.client.force_login(self.staff_user("event-page-reader", ("events", "view_event")))
        response = self.client.get(reverse("events:event_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count("<h1"), 1)
        self.assertContains(response, 'aria-current="page"')
        self.assertNotContains(response, 'href="/internal/" class="nav-item active"')


@override_settings(AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"])
class EventPermissionBoundaryTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="staff-no-event-perms", password="test-password", is_staff=True)
        self.client.force_login(user)

    def test_event_view_and_create_permissions_are_separate(self):
        self.assertEqual(self.client.get(reverse("events:event_list")).status_code, 403)
        create_url = reverse("events:event_create")
        self.assertEqual(self.client.get(create_url).status_code, 403)
        self.assertEqual(self.client.post(create_url, {}).status_code, 403)

    def test_event_viewer_cannot_create_event(self):
        user = get_user_model().objects.get(username="staff-no-event-perms")
        user.user_permissions.add(Permission.objects.get(content_type__app_label="events", codename="view_event"))
        self.assertEqual(self.client.get(reverse("events:event_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("events:event_create")).status_code, 403)

    def test_sensitive_event_posts_require_change_permissions(self):
        from uuid import uuid4
        event_id, registration_id = uuid4(), uuid4()
        protected_urls = [
            reverse("events:station_assign", kwargs={"event_id": event_id}),
            reverse("events:registration_group_change", kwargs={"event_id": event_id, "registration_id": registration_id}),
            reverse("events:registration_answers_correct", kwargs={"event_id": event_id, "registration_id": registration_id}),
        ]
        for url in protected_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {}).status_code, 403)
