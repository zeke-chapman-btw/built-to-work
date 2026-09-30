import uuid

from django.contrib.auth.hashers import check_password
from django.test import TestCase

from .models import User


class UserTests(TestCase):
    def test_user_has_uuid_primary_key_and_hashed_password(self):
        user = User.objects.create_user(username="member", password="test-password")

        self.assertIsInstance(user.pk, uuid.UUID)
        self.assertTrue(check_password("test-password", user.password))
        self.assertNotEqual(user.password, "test-password")

    def test_superuser_defaults_are_set(self):
        user = User.objects.create_superuser(
            username="admin", email="admin@example.test", password="test-password"
        )

        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)