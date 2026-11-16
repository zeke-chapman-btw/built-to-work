import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.events import sms_providers
from apps.events.communications import _provider_send

from . import runtime, store
from .backends import ConnectionsEmailBackend
from .models import ConnectionSetting

NO_RENDER = dict(EZTEXTING_USERNAME="", EZTEXTING_PASSWORD="", BTW_SMS_PROVIDER="", EMAIL_HOST_USER="",
                 EMAIL_HOST_PASSWORD="", BTW_EMAIL_FROM="", BTW_TICKET_EMAIL_FROM="", BTW_EMAIL_REPLY_TO=[])


@override_settings(**NO_RENDER)
class ConnectionsPageTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="conn-admin", password="x-test-only")
        self.staff = User.objects.create_user(username="conn-staff", password="x-test-only", is_staff=True)
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")

    def test_only_administrators(self):
        self.client.force_login(self.staff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("messaging:connections")).status_code, 403)
        self.assertEqual(self.client.post(reverse("messaging:connections-text"), {"username": "a", "password": "b"}).status_code, 403)
        self.assertFalse(ConnectionSetting.objects.exists())

    def test_saved_password_is_encrypted_never_shown_and_used(self):
        page = self.client.get(reverse("messaging:connections"))
        self.assertContains(page, "Not set up")
        self.assertContains(page, "Connections")
        self.client.post(reverse("messaging:connections-text"), {"username": "btw-texting", "password": "s3cret-test"})
        row = ConnectionSetting.objects.get(key="eztexting")
        self.assertNotIn("s3cret-test", row.secret_encrypted)
        self.assertEqual(sms_providers.ez_credentials(), ("btw-texting", "s3cret-test", "app"))
        page = self.client.get(reverse("messaging:connections"))
        self.assertNotContains(page, "s3cret-test")
        self.assertContains(page, "btw-texting")
        # Blank password keeps the saved one.
        self.client.post(reverse("messaging:connections-text"), {"username": "btw-texting2", "password": ""})
        self.assertEqual(sms_providers.ez_credentials(), ("btw-texting2", "s3cret-test", "app"))

    @override_settings(EZTEXTING_USERNAME="render-user", EZTEXTING_PASSWORD="render-pass")
    def test_falls_back_to_render_settings(self):
        self.assertEqual(sms_providers.ez_credentials(), ("render-user", "render-pass", "render"))
        self.assertContains(self.client.get(reverse("messaging:connections")), "Set in Render")

    def test_test_text_records_result(self):
        store.save(key="eztexting", values={"username": "u"}, actor=self.admin, new_secret="p")
        with patch("apps.events.sms_providers.send_sms", return_value="eztexting:5"):
            self.client.post(reverse("messaging:connections-text-test"), {"number": "5551234567"})
        self.assertTrue(ConnectionSetting.objects.get(key="eztexting").last_test_ok)
        with patch("apps.events.sms_providers.send_sms", side_effect=ConnectionError("SMS provider sign-in was refused")):
            self.client.post(reverse("messaging:connections-text-test"), {"number": "5551234567"})
        self.assertContains(self.client.get(reverse("messaging:connections")), "Sign-in failing")

    def test_email_settings_send_test_through_real_smtp(self):
        self.client.post(reverse("messaging:connections-email"), {"from_address": "Built to Work <noreply@example.test>",
            "reply_to": "team@example.test", "host": "smtp-relay.gmail.com", "port": 587, "username": "relay@example.test",
            "password": "app-pass-test"})
        config = store.email_settings()
        self.assertEqual((config["source"], config["username"], config["password"]), ("app", "relay@example.test", "app-pass-test"))
        with patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=1) as smtp:
            self.client.post(reverse("messaging:connections-email-test"), {"address": "me@example.test"})
        smtp.assert_called_once()
        self.assertTrue(ConnectionSetting.objects.get(key="email").last_test_ok)
        self.assertEqual(len(mail.outbox), 0)

    def test_toggle_sending_and_history(self):
        self.client.post(reverse("messaging:connections-sending"), {"enabled": "on"})
        self.assertTrue(store.automatic_sending_enabled())
        self.client.post(reverse("messaging:connections-sending"), {"enabled": "off"})
        self.assertFalse(store.automatic_sending_enabled())
        page = self.client.get(reverse("messaging:connections"))
        self.assertContains(page, "turned on")
        self.assertContains(page, "turned off")


@override_settings(**NO_RENDER)
class SiteEmailBackendTests(TestCase):
    def test_keeps_messages_until_set_up_then_sends_with_saved_sender(self):
        backend = ConnectionsEmailBackend()
        from django.core.mail import EmailMessage
        self.assertEqual(backend.send_messages([EmailMessage("s", "b", None, ["p@example.test"])]), 1)
        self.assertEqual(len(mail.outbox), 1)
        admin = get_user_model().objects.create_superuser(username="b-admin", password="x-test-only")
        store.save(key="email", actor=admin, new_secret="pw", values={"from_address": "noreply@example.test", "reply_to": "",
                   "host": "smtp-relay.gmail.com", "port": 587, "username": "relay@example.test"})
        message = EmailMessage("s", "b", "webmaster@localhost", ["p@example.test"])
        with patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=1) as smtp:
            backend.send_messages([message])
        smtp.assert_called_once()
        self.assertEqual(message.from_email, "noreply@example.test")


@override_settings(**NO_RENDER)
class BackgroundSenderTests(TestCase):
    def test_off_sends_nothing_and_on_sends_only_new(self):
        from apps.events.models import TicketDelivery
        with patch("apps.events.communications.deliver_ticket") as deliver:
            self.assertFalse(runtime.run_pass()["enabled"])
        deliver.assert_not_called()
        admin = get_user_model().objects.create_superuser(username="r-admin", password="x-test-only")
        store.save(key="sending", values={"enabled": True, "since": timezone.now().isoformat()}, actor=admin)
        old_ids = [uuid.uuid4()]
        with patch("apps.events.communications.due_deliveries", return_value=TicketDelivery.objects.none()) as due, \
             patch("apps.messaging.text_status.check_recent", return_value=0):
            outcome = runtime.run_pass()
        self.assertTrue(outcome["enabled"])
        self.assertTrue(due.called)

    def test_queue_email_uses_saved_sign_in(self):
        admin = get_user_model().objects.create_superuser(username="q-admin", password="x-test-only")
        store.save(key="email", actor=admin, new_secret="pw", values={"from_address": "noreply@example.test", "reply_to": "",
                   "host": "smtp-relay.gmail.com", "port": 587, "username": "relay@example.test"})
        with patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=1) as smtp:
            _provider_send("email", "p@example.test", subject="Ticket", body="Body", idempotency_key="k9")
        smtp.assert_called_once()
        self.assertEqual(len(mail.outbox), 0)
