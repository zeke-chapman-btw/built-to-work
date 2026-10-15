import base64

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from config.middleware import PreviewAccessMiddleware


class PreviewAccessMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.get_response = lambda request: HttpResponse("app response")

    @override_settings(
        BTW_PREVIEW_GATE_ENABLED=True,
        BTW_PREVIEW_GATE_USERNAME="reviewer",
        BTW_PREVIEW_GATE_PASSWORD="test-only-password",
    )
    def test_all_routes_except_health_require_preview_credentials(self):
        middleware = PreviewAccessMiddleware(self.get_response)
        response = middleware(self.factory.get("/events/"))
        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response["WWW-Authenticate"])
        self.assertEqual(response["Cache-Control"], "no-store")

    @override_settings(
        BTW_PREVIEW_GATE_ENABLED=True,
        BTW_PREVIEW_GATE_USERNAME="reviewer",
        BTW_PREVIEW_GATE_PASSWORD="test-only-password",
    )
    def test_valid_credentials_allow_public_routes(self):
        middleware = PreviewAccessMiddleware(self.get_response)
        token = base64.b64encode(b"reviewer:test-only-password").decode("ascii")
        response = middleware(
            self.factory.get("/register/example/", HTTP_AUTHORIZATION=f"Basic {token}")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"app response")

    @override_settings(
        BTW_PREVIEW_GATE_ENABLED=True,
        BTW_PREVIEW_GATE_USERNAME="reviewer",
        BTW_PREVIEW_GATE_PASSWORD="test-only-password",
    )
    def test_static_assets_are_protected_by_preview_gate(self):
        response = self.client.get("/static/internal/images/built-to-work-linear.svg")
        self.assertEqual(response.status_code, 401)

    def test_health_endpoint_is_available_without_preview_credentials(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @override_settings(
        BTW_PREVIEW_GATE_ENABLED=True,
        BTW_PREVIEW_GATE_USERNAME="",
        BTW_PREVIEW_GATE_PASSWORD="",
    )
    def test_enabled_gate_fails_closed_without_credentials(self):
        with self.assertRaises(ImproperlyConfigured):
            PreviewAccessMiddleware(self.get_response)

    @override_settings(BTW_PREVIEW_GATE_ENABLED=False)
    def test_disabled_gate_preserves_normal_application_access(self):
        middleware = PreviewAccessMiddleware(self.get_response)
        response = middleware(self.factory.get("/register/example/"))
        self.assertEqual(response.status_code, 200)
