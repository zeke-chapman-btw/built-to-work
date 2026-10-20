from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse
from .dashboard import QUICK_ACTIONS, available_quick_actions, quick_actions_for


class QuickActionsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('qa-owner', password='test-password', is_staff=True, is_superuser=True)
        self.client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        self.editor = reverse('internal:quick-actions-edit')
        self.home = reverse('internal:home')

    def ids(self, response=None):
        response = response or self.client.get(self.home)
        return [a['id'] for a in response.context['quick_actions']]

    def save(self, actions):
        return self.client.post(self.editor, {'operation': 'save', 'actions': actions})

    def test_catalog_defaults_are_existing_routes_and_no_placeholders(self):
        defaults = [a['id'] for a in QUICK_ACTIONS if a['default']]
        self.assertEqual(self.ids(), defaults)
        self.assertEqual(len(defaults), 9)
        self.assertEqual(len({a['id'] for a in QUICK_ACTIONS}), len(QUICK_ACTIONS))
        for action in available_quick_actions(self.user):
            self.assertEqual(self.client.get(action['url']).status_code, 200, action['id'])
        self.assertNotContains(self.client.get(self.home), 'Create customer')

    def test_editor_is_accessible_from_home_with_nine_ordered_slots(self):
        self.assertContains(self.client.get(self.home), 'href="'+self.editor+'">Edit</a>')
        response = self.client.get(self.editor)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="actions"', count=9)
        self.assertContains(response, 'Restore Defaults')
        self.assertContains(response, 'csrfmiddlewaretoken')

    def test_save_order_and_remove_persist_across_sessions(self):
        self.assertRedirects(self.save(['settings', '', 'events']), self.home)
        self.assertEqual(self.ids(), ['settings', 'events'])
        self.client.logout()
        self.client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.ids(), ['settings', 'events'])
        other_device = Client()
        other_device.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.ids(other_device.get(self.home)), ['settings', 'events'])

    def test_empty_selection_is_preserved_and_not_filled(self):
        self.assertRedirects(self.save([''] * 9), self.home)
        self.assertEqual(self.ids(), [])
        self.assertContains(self.client.get(self.home), 'No Quick Actions selected.')
        self.user.refresh_from_db()
        self.assertEqual(self.user.quick_action_ids, [])

    def test_restore_defaults_and_get_do_not_mutate_preferences(self):
        self.save(['events'])
        self.client.get(self.editor, {'operation': 'restore'})
        self.assertEqual(self.ids(), ['events'])
        self.assertRedirects(self.client.post(self.editor, {'operation': 'restore'}), self.home)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.quick_action_ids)
        self.assertEqual(self.ids(), [a['id'] for a in QUICK_ACTIONS if a['default']])

    def test_nine_limit_and_duplicates_are_validated(self):
        ids = [a['id'] for a in QUICK_ACTIONS]
        self.assertRedirects(self.save(ids[:9]), self.home)
        response = self.save(ids)
        self.assertContains(response, 'no more than nine')
        self.assertEqual(self.ids(), ids[:9])
        response = self.save(['events', 'events'])
        self.assertContains(response, 'only once')
        self.assertEqual(self.ids(), ids[:9])

    def test_unknown_ids_and_external_urls_are_rejected(self):
        for value in ('missing-action', 'https://example.test/', '/admin/'):
            response = self.save([value])
            self.assertContains(response, 'Select a valid choice')
        self.user.refresh_from_db()
        self.assertIsNone(self.user.quick_action_ids)

    def test_users_have_independent_preferences_and_cannot_target_another_user(self):
        other = get_user_model().objects.create_user('qa-other', password='test-password', is_staff=True)
        self.client.post(self.editor, {'actions': ['events'], 'user_id': str(other.pk)})
        other.refresh_from_db()
        self.assertIsNone(other.quick_action_ids)
        self.client.force_login(other, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.ids(), ['create-task', 'settings'])
        self.save([])
        self.client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.ids(), ['events'])

    def test_group_permissions_control_catalog_and_revocation_filters_saved_actions(self):
        staff = get_user_model().objects.create_user('qa-limited', password='test-password', is_staff=True)
        group = Group.objects.create(name='qa-events')
        permission = Permission.objects.get(content_type__app_label='events', codename='view_event')
        group.permissions.add(permission)
        staff.groups.add(group)
        self.client.force_login(staff, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(self.editor)
        self.assertEqual([a['id'] for a in response.context['available_actions']], ['create-task', 'events', 'settings'])
        self.assertRedirects(self.save(['events', 'settings']), self.home)
        group.permissions.remove(permission)
        self.assertEqual(self.ids(), ['settings'])
        self.assertContains(self.save(['events']), 'Select a valid choice')
        self.assertEqual(self.client.get(reverse('events:event_list')).status_code, 403)

    def test_create_permission_does_not_expose_event_list(self):
        staff = get_user_model().objects.create_user('qa-create', password='test-password', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='events', codename='add_event'))
        self.client.force_login(staff, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.ids(), ['create-task', 'create-event', 'settings'])
        self.assertContains(self.save(['events']), 'Select a valid choice')

    def test_operations_uses_destination_permission(self):
        staff = get_user_model().objects.create_user('qa-ops', password='test-password', is_staff=True)
        self.client.force_login(staff, backend='django.contrib.auth.backends.ModelBackend')
        self.assertContains(self.save(['operations']), 'Select a valid choice')
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='events', codename='view_syncoutboxitem'))
        self.assertRedirects(self.save(['operations']), self.home)
        self.assertEqual(self.client.get(reverse('events:operations-dashboard')).status_code, 200)

    def test_editor_requires_active_staff_and_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(csrf_client.post(self.editor, {'actions': ['events']}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.editor).status_code, 302)
        user = get_user_model().objects.create_user('qa-participant', password='test-password')
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(self.editor).status_code, 403)
        self.assertEqual(self.save(['settings']).status_code, 403)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.post(self.editor, {'actions': ['settings']}).status_code, 302)

    def test_corrupt_saved_values_never_render_unapproved_links(self):
        for values in ({'url': 'https://example.test'}, ['missing', {}, 'events', 'events']):
            self.user.quick_action_ids = values
            actions = quick_actions_for(self.user)
            self.assertEqual([a['id'] for a in actions], ['events'] if isinstance(values, list) else [])
