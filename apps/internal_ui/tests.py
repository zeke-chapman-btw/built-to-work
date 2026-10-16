from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from .navigation import WORKSPACES, visible_workspaces


class InternalShellTests(TestCase):
    def make_staff(self, username, *, superuser=False):
        return get_user_model().objects.create_user(
            username=username, password='valid-test-password', is_staff=True,
            is_active=True, is_superuser=superuser,
        )

    def test_anonymous_redirects_to_admin_login(self):
        response = self.client.get(reverse('internal:home'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(reverse('admin:login')))

    def test_authenticated_non_staff_is_forbidden(self):
        user = get_user_model().objects.create_user(
            username='participant-only', password='valid-test-password', is_staff=False, is_active=True
        )
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('internal:home')).status_code, 403)

    def test_workspace_registry_is_ordered_and_has_named_destinations(self):
        self.assertEqual([item['id'] for item in WORKSPACES], [
            'home', 'events', 'participants', 'operations', 'assessments', 'administration'
        ])
        user = self.make_staff('workspace-admin', superuser=True)
        visible = visible_workspaces(user)
        self.assertEqual([item['id'] for item in visible], [item['id'] for item in WORKSPACES])
        self.assertEqual(visible[0]['url'], reverse('internal:home'))
        self.assertEqual(visible[1]['url'], reverse('events:event_list'))
        for item in visible[2:]:
            self.assertTrue(item['url'].startswith('/internal/workspaces/'))

    def test_staff_sees_only_workspaces_allowed_by_permissions(self):
        user = self.make_staff('limited-staff')
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        home = self.client.get(reverse('internal:home'))
        self.assertContains(home, 'HOME')
        self.assertNotContains(home, 'PARTICIPANTS')
        self.assertNotContains(home, 'OPERATIONS')
        self.assertEqual(self.client.get(reverse('internal:workspace', kwargs={'workspace_id': 'participants'})).status_code, 403)

    def test_home_and_logo_link_to_dashboard_and_workspace_destinations(self):
        user = self.make_staff('portal-admin', superuser=True)
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('internal:home'))
        self.assertContains(response, 'aria-label="Built to Work home"')
        self.assertContains(response, reverse('internal:home'))
        self.assertContains(response, reverse('events:event_list'))
        self.assertContains(response, reverse('internal:workspace', kwargs={'workspace_id': 'participants'}))
        self.assertNotContains(response, 'aria-expanded="true"')
        self.assertNotContains(response, 'PLANNED')

    def test_named_workspace_pages_render_shared_shell_and_real_links(self):
        user = self.make_staff('workspace-owner', superuser=True)
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        for workspace_id in ('participants', 'operations', 'assessments', 'administration'):
            with self.subTest(workspace=workspace_id):
                response = self.client.get(reverse('internal:workspace', kwargs={'workspace_id': workspace_id}))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'POWERING THE AMERICAN WORKFORCE')
                self.assertContains(response, 'internal-sidebar')
                self.assertContains(response, 'workspace-card')

    def test_events_workspace_uses_shared_header_and_tab_patterns(self):
        user = self.make_staff('event-staff')
        permission = Permission.objects.get(codename='view_event', content_type__app_label='events', content_type__model='event')
        user.user_permissions.add(permission)
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('events:event_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Events sections')
        self.assertContains(response, 'All events')
        self.assertContains(response, 'aria-current="page"')
        self.assertNotContains(response, '<style>', html=False)
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("events:event_list"))
        for href in (
            "/internal/", "/events/", "/internal/workspaces/participants/",
            "/internal/workspaces/operations/", "/internal/workspaces/assessments/",
            "/internal/workspaces/administration/",
        ):
            with self.subTest(href=href):
                self.assertContains(response, f'href="{href}"')
        self.assertContains(response, 'class="nav-item active" href="/events/"')

    def test_unknown_workspace_is_not_exposed(self):
        user = self.make_staff('unknown-staff')
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('internal:workspace', kwargs={'workspace_id': 'future'})).status_code, 404)
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
        self.assertContains(response, "No workspaces available")

    def test_dashboard_uses_permission_scoped_event_data(self):
        self.client.force_login(self.staff_user("event-reader", ("events", "view_event")))
        response = self.client.get(reverse("internal:home"))
        self.assertEqual(response.status_code, 200)
        events_url = reverse("events:event_list")
        self.assertContains(response, events_url)
        self.assertContains(response, "Events")
        self.assertNotContains(response, "Active registrations")

        response = self.client.get(events_url)
        self.assertEqual(response.status_code, 200)
    def test_approvals_navigation_uses_valid_route_for_superuser(self):
        self.client.force_login(self.staff_user("portal-superuser", superuser=True))
        participants_workspace = reverse("internal:workspace", kwargs={"workspace_id": "participants"})
        response = self.client.get(participants_workspace)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("participants:staff-qualification-approvals"))
    def test_dashboard_has_one_primary_heading(self):
        self.client.force_login(self.staff_user("dashboard-heading-staff"))
        response = self.client.get(reverse("internal:home"))
        self.assertEqual(response.content.decode().count("<h1"), 1)

    def test_staff_header_uses_tagline_and_official_drawer_logo(self):
        self.client.force_login(self.staff_user("dashboard-branding-staff"))
        response = self.client.get(reverse("internal:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="portal-tagline"')
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
