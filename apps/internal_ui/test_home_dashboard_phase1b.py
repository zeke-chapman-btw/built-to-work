import re
from html.parser import HTMLParser
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = {'tag': '', 'attrs': {}, 'children': [], 'text': []}
        self.stack = [self.root]
        self.nodes = []
    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'children': [], 'text': []}
        self.stack[-1]['children'].append(node)
        self.nodes.append(node)
        if tag not in ('img', 'input', 'link', 'meta', 'br', 'hr'):
            self.stack.append(node)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i]['tag'] == tag:
                self.stack = self.stack[:i]
                break
    def handle_data(self, data):
        for node in self.stack:
            node['text'].append(data)
    def with_class(self, value):
        return [n for n in self.nodes if value in n['attrs'].get('class', '').split()]


class HomeDashboardPolishTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser('polish-review', 'polish@example.test', 'test-only-password')
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        self.response = self.client.get(reverse('internal:home'))
        self.html = self.response.content.decode()
        self.markup = Markup()
        self.markup.feed(self.html)

    def test_workspace_text_navigation_labels_and_personal_settings_gear(self):
        nav = self.markup.with_class('workspace-nav')[0]
        links = [n for n in nav['children'] if n['tag'] == 'a']
        self.assertEqual([''.join(n['text']).strip() for n in links], ['HOME', 'EVENTS', 'PARTICIPANTS', 'TASKS', 'OPERATIONS', 'CUSTOMERS', 'ADMIN'])
        self.assertEqual([n['attrs']['href'] for n in links], [n['url'] for n in self.response.context['navigation']])
        self.assertNotContains(self.response, 'nav-icon')
        gear = self.markup.with_class('settings-gear')
        self.assertEqual(len(gear), 1)
        self.assertEqual(gear[0]['attrs']['href'], reverse('internal:settings'))
        self.assertEqual(gear[0]['attrs']['aria-label'], 'Personal settings')

    def test_four_panel_headings_and_real_secondary_actions(self):
        headers = self.markup.with_class('dashboard-section-header')
        self.assertEqual(len(headers), 4)
        headings = [''.join(h['children'][0]['text']).strip() for h in headers]
        for actual, expected in zip(headings, ('My Tasks', 'Recent Updates', 'Quick Actions', 'At a Glance')):
            self.assertIn(expected, actual)
        self.assertContains(self.response, reverse('operations:tasks') + '?view=mine')
        self.assertContains(self.response, reverse('operations:updates'))
        self.assertContains(self.response, reverse('internal:quick-actions-edit'))
        self.assertContains(self.response, reverse('operations:updates-read-all'))
        self.assertNotContains(self.response, 'Unavailable')
        self.assertNotContains(self.response, 'Edit Options')

    def test_updates_empty_state_links_to_real_feed(self):
        notices = self.markup.with_class('dashboard-notification')
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['tag'], 'li')
        self.assertIn('No task updates yet.', ''.join(notices[0]['text']))
        self.assertIn('Open Updates', ''.join(notices[0]['text']))
        self.assertContains(self.response, reverse('operations:updates'))
        self.assertNotContains(self.response, 'No updates connected in this visual preview.')
        self.assertNotContains(self.response, 'dashboard-update-list')
