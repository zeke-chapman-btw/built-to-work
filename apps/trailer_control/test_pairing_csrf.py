"""Protected HTTPS pairing regressions using Django's CSRF-enforcing client."""
import json
import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event, OfflineEventPreparation, TrailerInstance
from .models import ControlConfiguration, DisplayBinding
from . import services


@override_settings(
    TRAILER_CONTROL_ENABLED=True, TRAILER_CONTROL_IDENTITY="SYNTHETIC-CSRF",
    CSRF_COOKIE_SECURE=True, SESSION_COOKIE_SECURE=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    CSRF_TRUSTED_ORIGINS=[],
)
class PairingCsrfTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="synthetic-csrf-admin", password=None)
        self.worker = get_user_model().objects.create_user(username="synthetic-csrf-worker", is_staff=True)
        self.trailer = TrailerInstance.objects.create(identity="SYNTHETIC-CSRF")
        self.other = TrailerInstance.objects.create(identity="SYNTHETIC-CSRF-OTHER")
        now = timezone.now()
        self.event = Event.objects.create(name="SYNTHETIC CSRF event", start_at=now, end_at=now+timedelta(hours=4))
        self.prep = OfflineEventPreparation.objects.create(trailer=self.trailer, event=self.event)
        ControlConfiguration.objects.create(trailer=self.trailer, preparation=self.prep)
        self.device, _ = services.enroll_device(self.admin, self.trailer, name="Synthetic TV", kind="leaderboard")
        self.url = reverse("trailer_control:devices")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")

    def token(self, client=None):
        client = client or self.client
        response = client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 200)
        return client.cookies["csrftoken"].value

    def generate(self, token=None, **headers):
        return self.client.post(self.url, {
            "action": "pair", "device": str(self.device.pk),
            "csrfmiddlewaretoken": token or self.token(),
        }, secure=True, **headers)

    def test_staff_pages_preserve_same_origin_csrf_metadata(self):
        for name in ("index", "devices", "sync", "publication", "status"):
            with self.subTest(name=name):
                response = self.client.get(reverse("trailer_control:"+name), secure=True)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_browser_null_origin_reproduces_exact_rejection(self):
        token = self.token()
        before = AuditLog.objects.count()
        with self.assertLogs("django.security.csrf", level="WARNING") as logs:
            response = self.generate(token, HTTP_ORIGIN="null")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Origin checking failed - null does not match any trusted origins.", " ".join(logs.output))
        self.assertEqual(AuditLog.objects.count(), before)
        self.assertFalse(DisplayBinding.objects.exists())

    def test_valid_same_origin_post_generates_audited_scoped_pairing(self):
        response = self.generate(HTTP_ORIGIN="https://testserver")
        self.assertEqual(response.status_code, 200)
        pairing = response.context["pairing"]
        binding = DisplayBinding.objects.get(device=self.device)
        self.assertEqual(binding.preparation, self.prep)
        self.assertEqual(binding.channel, "leaderboard")
        self.assertEqual(binding.pairing_hash, services.digest(pairing["code"]))
        self.assertNotIn(pairing["code"], pairing["url"])
        self.assertContains(response, 'data-sensitive="true"')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertGreater(binding.pairing_expires_at, timezone.now())
        self.assertLessEqual(binding.pairing_expires_at, timezone.now()+timedelta(minutes=5))
        audit = AuditLog.objects.filter(action="trailer.control.display.pairing_started").get()
        self.assertEqual(audit.actor, self.admin)
        self.assertNotIn(pairing["code"], json.dumps(audit.new_data))

    def test_missing_invalid_and_mismatched_tokens_are_rejected(self):
        token = self.token()
        for value in (None, "invalid", "a"*64):
            with self.subTest(value_is_missing=value is None):
                payload = {"action": "pair", "device": str(self.device.pk)}
                if value is not None:
                    payload["csrfmiddlewaretoken"] = value
                response = self.client.post(self.url, payload, secure=True, HTTP_ORIGIN="https://testserver")
                self.assertEqual(response.status_code, 403)
        self.assertFalse(DisplayBinding.objects.exists())

    def test_missing_cookie_is_rejected_even_with_valid_form_token(self):
        token = self.token()
        del self.client.cookies["csrftoken"]
        self.assertEqual(self.generate(token, HTTP_ORIGIN="https://testserver").status_code, 403)
        self.assertFalse(DisplayBinding.objects.exists())

    def test_foreign_origin_is_not_relaxed_by_matching_referer(self):
        self.assertEqual(self.generate(HTTP_ORIGIN="https://foreign.invalid", HTTP_REFERER="https://testserver/").status_code, 403)
        self.assertFalse(DisplayBinding.objects.exists())

    def test_https_referer_fallback_requires_trusted_https_source(self):
        token = self.token()
        for headers in ({}, {"HTTP_REFERER": "https://foreign.invalid/"}, {"HTTP_REFERER": "http://testserver/"}):
            self.assertEqual(self.generate(token, **headers).status_code, 403)
        self.assertEqual(self.generate(token, HTTP_REFERER="https://testserver/internal/trailer-control/devices/").status_code, 200)

    def test_forwarded_https_uses_existing_proxy_handling(self):
        response = self.client.post(self.url, {"action": "pair", "device": str(self.device.pk), "csrfmiddlewaretoken": self.token()}, HTTP_X_FORWARDED_PROTO="https", HTTP_ORIGIN="https://testserver", HTTP_HOST="testserver")
        self.assertTrue(response.wsgi_request.is_secure())
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_and_unauthorized_users_cannot_generate(self):
        public_binding, _ = services.create_display(self.admin, self.device, channel="leaderboard")
        for user in (None, self.worker):
            with self.subTest(authenticated=user is not None):
                client = Client(enforce_csrf_checks=True)
                if user:
                    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
                client.get(reverse("trailer_control:display", args=[public_binding.pk]), secure=True)
                token = client.cookies["csrftoken"].value
                old_revision = DisplayBinding.objects.get(pk=public_binding.pk).session_revision
                response = client.post(self.url, {"action": "pair", "device": str(self.device.pk), "csrfmiddlewaretoken": token}, secure=True, HTTP_ORIGIN="https://testserver")
                self.assertEqual(response.status_code, 403 if user else 302)
                self.assertEqual(DisplayBinding.objects.get(pk=public_binding.pk).session_revision, old_revision)

    def test_control_manager_still_requires_administrator_for_pairing(self):
        self.worker.user_permissions.add(*Permission.objects.filter(content_type__app_label="trailer_control", codename__in=["view_control_center", "manage_control_center"]))
        self.client.force_login(self.worker, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.generate(HTTP_ORIGIN="https://testserver").status_code, 403)
        self.assertFalse(DisplayBinding.objects.exists())

    def test_foreign_trailer_device_is_not_pairable(self):
        foreign, _ = services.enroll_device(self.admin, self.other, name="Foreign synthetic TV", kind="leaderboard")
        response = self.client.post(self.url, {"action": "pair", "device": str(foreign.pk), "csrfmiddlewaretoken": self.token()}, secure=True, HTTP_ORIGIN="https://testserver")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(DisplayBinding.objects.exists())

    def test_generated_code_pairs_once_with_csrf_and_preserves_polling_heartbeat(self):
        pairing = self.generate(HTTP_ORIGIN="https://testserver").context["pairing"]
        binding = DisplayBinding.objects.get(device=self.device)
        client = Client(enforce_csrf_checks=True)
        client.get(pairing["url"], secure=True)
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post(pairing["url"], {"code": pairing["code"]}, secure=True, HTTP_ORIGIN="https://testserver").status_code, 403)
        response = client.post(pairing["url"], {"code": pairing["code"], "csrfmiddlewaretoken": token}, secure=True, HTTP_ORIGIN="https://testserver")
        self.assertEqual(response.status_code, 302)
        page = client.get(pairing["url"], secure=True)
        self.assertEqual(page["Referrer-Policy"], "no-referrer")
        self.assertNotContains(page, "Main navigation")
        reused = Client(enforce_csrf_checks=True)
        reused.get(pairing["url"], secure=True)
        self.assertEqual(reused.post(pairing["url"], {"code": pairing["code"], "csrfmiddlewaretoken": reused.cookies["csrftoken"].value}, secure=True, HTTP_ORIGIN="https://testserver").status_code, 400)
        data = client.get(reverse("trailer_control:display-data", args=[binding.pk]), secure=True)
        self.assertEqual(data.status_code, 200)
        self.assertEqual(data.json()["groups"], [])
        heartbeat = reverse("trailer_control:display-heartbeat", args=[binding.pk])
        payload = {"boot_id": str(uuid.uuid4()), "sequence": 1, "event_id": str(self.event.pk), "state": "operating"}
        self.assertEqual(client.post(heartbeat, json.dumps(payload), content_type="application/json", secure=True, HTTP_ORIGIN="https://testserver").status_code, 403)
        response = client.post(heartbeat, json.dumps(payload), content_type="application/json", secure=True, HTTP_ORIGIN="https://testserver", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"accepted": True})

    def test_expired_pairing_code_is_rejected_through_protected_route(self):
        pairing = self.generate(HTTP_ORIGIN="https://testserver").context["pairing"]
        binding = DisplayBinding.objects.get(device=self.device)
        binding.pairing_expires_at = timezone.now()-timedelta(seconds=1)
        binding.save(update_fields=["pairing_expires_at"])
        client = Client(enforce_csrf_checks=True)
        client.get(pairing["url"], secure=True)
        response = client.post(pairing["url"], {"code": pairing["code"], "csrfmiddlewaretoken": client.cookies["csrftoken"].value}, secure=True, HTTP_ORIGIN="https://testserver")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("trailer_displays", client.session)
