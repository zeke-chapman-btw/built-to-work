"""Game maintenance advertises native administration and provides an Operations return."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class GameAdministrationBridgeTests(TestCase):
    def test_workspace_and_native_game_pages_explain_the_transition(self):
        user = get_user_model().objects.create_superuser('synthetic-game-maintenance', 'games@example.test', 'synthetic-password')
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('internal:workspace', args=['operations']), {'resource': 'games'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Open game administration')
        self.assertContains(response, 'Opens restricted administration')
        for name in ('admin:games_gamedefinition_changelist', 'admin:games_gamedefinition_add'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Return to Operations')
                self.assertContains(response, '?resource=games')

    def test_staff_without_native_game_permission_remains_denied(self):
        user = get_user_model().objects.create_user('synthetic-no-game-access', is_staff=True)
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('admin:games_gamedefinition_changelist')).status_code, 403)
