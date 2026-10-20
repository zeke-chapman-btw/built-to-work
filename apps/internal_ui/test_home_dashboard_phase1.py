from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons = []
        self.gears = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'button':
            self.buttons.append(attrs)
        if tag == 'a' and 'settings-gear' in attrs.get('class', '').split():
            self.gears.append(attrs)


class HomeDashboardPhaseOneTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('layout-review', 'layout@example.test', 'test-only-password')
        self.client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')

    def test_dashboard_sections_are_honest_preview_and_reuse_existing_routes(self):
        response = self.client.get(reverse('internal:home'))
        self.assertEqual(response.status_code, 200)
        for section in ('Updates', 'Quick Actions', 'Analytics'):
            self.assertContains(response, section)
        self.assertContains(response, 'No task updates yet.')
        self.assertContains(response, reverse('operations:updates'))
        self.assertContains(response, 'analytics data is not connected')
        self.assertEqual(len(response.context['quick_actions']), 8)
        self.assertContains(response, 'class="card dashboard-metric"', count=6)
        self.assertContains(response, 'Not connected', count=6)
        self.assertContains(response, reverse('events:event_create'))
        self.assertContains(response, reverse('participants:staff-directory'))
        controls = Controls(); controls.feed(response.content.decode())
        self.assertEqual(len(controls.buttons), 7)  # shell/modal, empty live Updates read action, two Analytics preview controls
        self.assertEqual(sum("data-task-close" in b for b in controls.buttons), 1)
        preview_buttons = [b for b in controls.buttons if 'data-menu-toggle' not in b and 'data-scrim' not in b and 'data-task-close' not in b and 'sign-out' not in b.get('class', '').split()]
        self.assertEqual(len(preview_buttons), 3)
        self.assertEqual(sum(b.get('type') == 'submit' for b in preview_buttons), 1)
        self.assertTrue(all('disabled' in b for b in preview_buttons))
        self.assertEqual(response.content.decode().count('<h1'), 1)

    def test_settings_gear_replaces_row_on_home_and_existing_feature_pages(self):
        for route in ('internal:home', 'events:event_list', 'participants:staff-directory', 'internal:settings'):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                controls = Controls(); controls.feed(response.content.decode())
                self.assertEqual(len(controls.gears), 1)
                self.assertEqual(controls.gears[0]['href'], reverse('internal:settings'))
                self.assertEqual(controls.gears[0]['aria-label'], 'Personal settings')
                self.assertNotContains(response, '>SETTINGS</span>')
                self.assertContains(response, self.user.username)
                if route != 'internal:home':
                    self.assertNotContains(response, 'internal/css/dashboard.css')
                    self.assertNotContains(response, 'home-dashboard-page')

    def test_quick_actions_respect_view_and_create_permissions_separately(self):
        staff = get_user_model().objects.create_user('limited-layout', password='test-password', is_staff=True)
        self.client.force_login(staff, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('internal:home'))
        self.assertEqual([a['label'] for a in response.context['quick_actions']], ['Personal settings'])
        self.assertNotContains(response, 'href="'+reverse('events:event_create')+'"')
        self.assertNotContains(response, 'href="'+reverse('participants:staff-directory')+'"')
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='events', codename='view_event'))
        response = self.client.get(reverse('internal:home'))
        self.assertContains(response, 'href="'+reverse('events:event_list')+'"')
        self.assertNotContains(response, 'href="'+reverse('events:event_create')+'"')
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='events', codename='add_event'))
        response = self.client.get(reverse('internal:home'))
        self.assertContains(response, 'href="'+reverse('events:event_create')+'"')

    def test_existing_feature_pages_keep_their_forms_and_actions(self):
        events = self.client.get(reverse('events:event_list'))
        self.assertEqual(events.status_code, 200)
        self.assertTemplateUsed(events, 'events/event_list.html')
        self.assertContains(events, 'Events sections')
        self.assertContains(events, reverse('events:event_create'))
        directory = self.client.get(reverse('participants:staff-directory'))
        self.assertEqual(directory.status_code, 200)
        self.assertContains(directory, 'role="search"')
        self.assertContains(directory, 'name="q"')
        self.assertContains(directory, 'Name, email, or phone')
        for response in (events, directory):
            self.assertNotContains(response, 'internal/css/dashboard.css')
            self.assertNotContains(response, 'home-dashboard-page')
