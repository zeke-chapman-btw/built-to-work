import base64
import json
from io import BytesIO, StringIO
from unittest.mock import patch
from urllib import error

from django.core import mail
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from apps.events import sms_providers
from apps.events.communications import _provider_send


class _Response:
    def __init__(self, status=201, body=b'{"id": "987"}'):
        self.status, self._body, self.headers = status, body, {}
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


EZ = dict(EZTEXTING_USERNAME="btw-app", EZTEXTING_PASSWORD="test-only", BTW_SMS_PROVIDER="")


class EzTextingTests(SimpleTestCase):
    @override_settings(**EZ)
    def test_sends_with_basic_sign_in_and_eleven_digit_number(self):
        with patch("apps.events.sms_providers.request.urlopen", return_value=_Response()) as urlopen:
            reference = _provider_send("sms", "(555) 123-4567", subject="x", body="Hello", idempotency_key="k1")
        self.assertEqual(reference, "eztexting:987")
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://a.eztexting.com/v1/messages")
        self.assertEqual(json.loads(req.data), {"toNumbers": ["15551234567"], "message": "Hello"})
        expected = base64.b64encode(b"btw-app:test-only").decode()
        self.assertEqual(req.get_header("Authorization"), f"Basic {expected}")

    @override_settings(**EZ)
    def test_bad_number_is_rejected_without_calling_provider(self):
        with patch("apps.events.sms_providers.request.urlopen") as urlopen:
            with self.assertRaises(ValueError):
                sms_providers.send_sms("12345", "Hi", idempotency_key="k")
        urlopen.assert_not_called()

    @override_settings(**EZ)
    def test_error_mapping(self):
        cases = {429: ConnectionError, 401: ConnectionError, 400: ValueError, 503: TimeoutError}
        for code, exc_type in cases.items():
            http = error.HTTPError("u", code, "x", {}, BytesIO(b""))
            with self.subTest(code=code), patch("apps.events.sms_providers.request.urlopen", side_effect=http):
                with self.assertRaises(exc_type):
                    sms_providers.send_sms("5551234567", "Hi", idempotency_key="k")

    @override_settings(EZTEXTING_USERNAME="", EZTEXTING_PASSWORD="", BTW_SMS_PROVIDER="", BTW_SMS_PROVIDER_URL="", BTW_SMS_PROVIDER_TOKEN="")
    def test_not_configured_retries_later(self):
        self.assertFalse(sms_providers.is_configured())
        with self.assertRaises(ConnectionError):
            sms_providers.send_sms("5551234567", "Hi", idempotency_key="k")


class EmailSenderTests(SimpleTestCase):
    @override_settings(BTW_TICKET_EMAIL_FROM="", BTW_EMAIL_FROM="Built to Work <noreply@example.test>",
                       BTW_EMAIL_REPLY_TO=["team@example.test"])
    def test_generic_sender_and_reply_to(self):
        _provider_send("email", "p@example.test", subject="Ticket", body="Body", idempotency_key="k2")
        sent = mail.outbox[-1]
        self.assertEqual(sent.from_email, "Built to Work <noreply@example.test>")
        self.assertEqual(sent.reply_to, ["team@example.test"])

    @override_settings(**EZ, BTW_EMAIL_FROM="noreply@example.test", BTW_EMAIL_REPLY_TO=[])
    def test_check_command_reports_without_secrets(self):
        out = StringIO()
        call_command("check_messaging", stdout=out)
        text = out.getvalue()
        self.assertIn("provider=eztexting configured=yes", text)
        self.assertIn("test mode", text)
        self.assertNotIn("test-only", text)
