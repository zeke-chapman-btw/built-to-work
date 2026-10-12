"""Staff authorization boundary for participant match review."""
from uuid import UUID
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from .reconciliation import resolve_match, reverse_match


CASE = UUID("a1000000-0000-4000-8000-000000000099")


class MatchReviewAuthorizationTests(TestCase):
    def setUp(self):
        self.list_url = reverse("events:match-review-list")
        self.detail_url = reverse("events:match-review-detail", args=[CASE])
        self.resolve_url = reverse("events:match-review-resolve", args=[CASE])
        self.reverse_url = reverse("events:match-review-reverse", args=[CASE])
        self.member = get_user_model().objects.create_user(username="ordinary-match", password="test")
        self.staff = get_user_model().objects.create_user(
            username="staff-match", password="test", is_staff=True,
        )

    def test_anonymous_users_are_redirected_from_review(self):
        self.assertEqual(self.client.get(self.list_url, secure=True).status_code, 302)
        self.assertEqual(self.client.post(self.resolve_url, {"decision": "same", "reason": "x"}, secure=True).status_code, 302)

    def test_nonstaff_direct_urls_and_post_are_denied(self):
        self.client.force_login(self.member, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(self.list_url, secure=True).status_code, 403)
        self.assertEqual(self.client.get(self.detail_url, secure=True).status_code, 403)
        self.assertEqual(self.client.post(self.resolve_url, {"decision": "same", "reason": "x"}, secure=True).status_code, 403)
        self.assertEqual(self.client.post(self.reverse_url, {"reason": "x"}, secure=True).status_code, 403)

    def test_staff_without_review_permission_are_denied(self):
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(self.list_url, secure=True).status_code, 403)
        self.assertEqual(self.client.post(self.resolve_url, {"decision": "same", "reason": "x"}, secure=True).status_code, 403)

    def test_readonly_staff_cannot_resolve_or_reverse(self):
        self.staff.user_permissions.add(Permission.objects.get(codename="view_participantmatchreview"))
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(self.list_url, secure=True).status_code, 200)
        self.assertEqual(self.client.get(self.detail_url, secure=True).status_code, 404)
        self.assertEqual(self.client.post(self.resolve_url, {"decision": "same", "reason": "x"}, secure=True).status_code, 403)
        self.assertEqual(self.client.post(self.reverse_url, {"reason": "x"}, secure=True).status_code, 403)

    def test_missing_reason_is_rejected_before_any_import(self):
        admin = get_user_model().objects.create_superuser(username="match-admin", password="test")
        with self.assertRaises(ValidationError):
            resolve_match(review_id=CASE, action="same", reason=" ", actor=admin)
        with self.assertRaises(ValidationError):
            reverse_match(review_id=CASE, reason=" ", actor=admin)
