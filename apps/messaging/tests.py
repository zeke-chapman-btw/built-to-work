import base64
import json
import uuid
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.crm.models import Interaction
from apps.recruiting.test_employer import EmployerFixtures

from . import google, text_status
from .models import GmailConnection, SentEmail, TextDeliveryResult

GOOGLE = dict(GOOGLE_OAUTH_CLIENT_ID="client-test", GOOGLE_OAUTH_CLIENT_SECRET="secret-test", GOOGLE_OAUTH_ALLOWED_DOMAIN="example.test")


def id_token(email, verified=True):
    claims = base64.urlsafe_b64encode(json.dumps({"email": email, "email_verified": verified}).encode()).decode().rstrip("=")
    return f"h.{claims}.s"


class _Response:
    def __init__(self, data, status=200):
        self._data, self.status, self.headers = json.dumps(data).encode(), status, {}
    def read(self):
        return self._data
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


@override_settings(**GOOGLE)
class GmailConnectTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.person.contact_email = "person@example.org"
        self.person.save(update_fields=["contact_email"])

    def connect(self, email="staff@example.test", scope="openid email https://www.googleapis.com/auth/gmail.send"):
        response = self.client.post(reverse("messaging:gmail-connect"))
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(query["scope"][0], google.SCOPES)
        self.assertEqual(query["hd"][0], "example.test")
        token = {"access_token": "a", "refresh_token": "refresh-1", "scope": scope, "id_token": id_token(email)}
        with patch("apps.messaging.google.request.urlopen", return_value=_Response(token)):
            return self.client.get(reverse("messaging:gmail-callback"), {"state": query["state"][0], "code": "c"})

    def test_connect_stores_encrypted_token(self):
        self.connect()
        row = GmailConnection.objects.get(user=self.admin)
        self.assertEqual(row.google_email, "staff@example.test")
        self.assertNotIn("refresh-1", row.refresh_token_encrypted)
        self.assertEqual(google.decrypt(row.refresh_token_encrypted), "refresh-1")

    def test_wrong_state_or_domain_or_scope_does_not_connect(self):
        self.client.post(reverse("messaging:gmail-connect"))
        self.client.get(reverse("messaging:gmail-callback"), {"state": "forged", "code": "c"})
        self.connect(email="someone@gmail.com")
        self.connect(scope="openid email")
        self.assertFalse(GmailConnection.objects.exists())

    def test_test_email_goes_to_your_own_address(self):
        self.connect()
        responses = [_Response({"access_token": "a"}), _Response({"id": "gm-t"})]
        with patch("apps.messaging.google.request.urlopen", side_effect=responses) as urlopen:
            response = self.client.post(reverse("messaging:gmail-test"), follow=True)
        raw = base64.urlsafe_b64decode(json.loads(urlopen.call_args_list[1].args[0].data)["raw"]).decode()
        self.assertIn("To: staff@example.test", raw)
        self.assertContains(response, "Test email sent to staff@example.test")

    def test_send_records_history_once(self):
        self.connect()
        url = reverse("messaging:email-participant", args=[self.person.pk])
        self.assertContains(self.client.get(url), "staff@example.test")
        op = str(uuid.uuid4())
        responses = [_Response({"access_token": "a"}), _Response({"id": "gm-1"})]
        with patch("apps.messaging.google.request.urlopen", side_effect=responses) as urlopen:
            self.client.post(url, {"operation_id": op, "subject": "Next steps", "body": "Hi there"})
        sent = json.loads(urlopen.call_args_list[1].args[0].data)
        raw = base64.urlsafe_b64decode(sent["raw"]).decode()
        self.assertIn("From: ", raw)
        self.assertIn("staff@example.test", raw)
        self.assertIn("To: person@example.org", raw)
        row = SentEmail.objects.get()
        self.assertEqual((row.status, row.provider_reference), ("sent", "gm-1"))
        interaction = Interaction.objects.get(pk=row.interaction_id)
        self.assertEqual((interaction.source, interaction.provider, interaction.delivery_status), ("provider", "gmail", "sent"))
        with patch("apps.messaging.google.request.urlopen") as again:
            self.client.post(url, {"operation_id": op, "subject": "Next steps", "body": "Hi there"})
        again.assert_not_called()
        self.assertEqual(SentEmail.objects.count(), 1)

    def test_revoked_permission_asks_to_reconnect(self):
        self.connect()
        url = reverse("messaging:email-participant", args=[self.person.pk])
        from io import BytesIO
        from urllib import error
        bad = error.HTTPError("u", 400, "x", {}, BytesIO(b'{"error": "invalid_grant"}'))
        with patch("apps.messaging.google.request.urlopen", side_effect=bad):
            self.client.post(url, {"operation_id": str(uuid.uuid4()), "subject": "S", "body": "B"})
        self.assertTrue(GmailConnection.objects.get().needs_reconnect)
        self.assertEqual(SentEmail.objects.get().status, "failed")
        self.assertFalse(Interaction.objects.filter(source="provider").exists())

    def test_disconnect_revokes(self):
        self.connect()
        with patch("apps.messaging.google.request.urlopen", return_value=_Response({})) as urlopen:
            self.client.post(reverse("messaging:gmail-disconnect"))
        self.assertIn("revoke", urlopen.call_args.args[0].full_url)
        self.assertFalse(GmailConnection.objects.exists())

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_CLIENT_SECRET="")
    def test_not_configured_is_explained(self):
        response = self.client.get(reverse("messaging:email-participant", args=[self.person.pk]))
        self.assertContains(response, "set up on this site yet")


@override_settings(EZTEXTING_USERNAME="app", EZTEXTING_PASSWORD="test-only", BTW_SMS_PROVIDER="")
class TextDeliveryTests(TestCase):
    def answer(self, delivered=0, bounced=0, not_sent=0):
        def fake(req, timeout):
            query = parse_qs(urlsplit(req.full_url).query)["filters[status][eq]"][0]
            return _Response({"content": [{}] * {"delivered": delivered, "bounced": bounced, "not_sent": not_sent}[query]})
        return fake

    def test_delivered_bounced_and_waiting(self):
        with patch("apps.messaging.text_status.request.urlopen", side_effect=self.answer(delivered=1)):
            self.assertEqual(text_status.check("eztexting:1").result, "delivered")
        with patch("apps.messaging.text_status.request.urlopen", side_effect=self.answer(bounced=1)):
            self.assertEqual(text_status.check("eztexting:2").result, "failed")
        with patch("apps.messaging.text_status.request.urlopen", side_effect=self.answer()):
            row = text_status.check("eztexting:3")
        self.assertEqual(row.result, "pending")
        with patch("apps.messaging.text_status.request.urlopen") as urlopen:
            text_status.check("eztexting:3")  # Too soon to ask again.
        urlopen.assert_not_called()

    def test_gives_up_after_three_days_and_ignores_other_references(self):
        self.assertIsNone(text_status.check("generic-ref"))
        row = TextDeliveryResult.objects.create(provider_reference="eztexting:9")
        TextDeliveryResult.objects.filter(pk=row.pk).update(first_seen_at=timezone.now() - timedelta(days=4))
        with patch("apps.messaging.text_status.request.urlopen") as urlopen:
            self.assertEqual(text_status.check("eztexting:9").result, "pending")
        urlopen.assert_not_called()
