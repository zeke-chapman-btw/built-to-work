from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from apps.events.models import QrTicket
from .screen_library import SCREENS, IMAGES


class ScreenLibraryTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("screens-admin", "screens@example.test", "x")

    def test_every_screen_renders_without_creating_records(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("operations:screens")).status_code, 200)
        before = QrTicket.objects.count()
        for key, *_ in SCREENS:
            self.assertEqual(self.client.get(reverse("operations:screen-preview", args=[key])).status_code, 200, key)
        for key, *_ in IMAGES:
            response = self.client.get(reverse("operations:screen-image", args=[key]))
            self.assertEqual(response["Content-Type"], "image/png", key)
        self.assertEqual(QrTicket.objects.count(), before)

    def test_requires_staff_access(self):
        user = get_user_model().objects.create_user("plain", "plain@example.test", "x", is_staff=True)
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("operations:screens")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("operations:screens")).status_code, 302)

    def test_library_lists_phone_signup_links(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("operations:screens"))
        self.assertContains(response, "Try it on your phone")
        self.assertContains(response, reverse("participants:registration-start"))
