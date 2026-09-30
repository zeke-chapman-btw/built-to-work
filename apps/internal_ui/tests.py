from django.contrib.auth import get_user_model
from django.test import TestCase
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
