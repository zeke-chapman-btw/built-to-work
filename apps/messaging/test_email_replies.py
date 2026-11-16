import base64
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.participants.models import Participant

from . import email_replies, google
from .models import EmailReply, GmailConnection, SentEmail

BACKEND = "django.contrib.auth.backends.ModelBackend"
STAFF = "staff@example.test"


def _b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _thread(sent_at):
    ms = str(int((sent_at + timedelta(minutes=5)).timestamp() * 1000))
    return {"messages": [
        {"id": "m-sent", "internalDate": str(int(sent_at.timestamp() * 1000)),
         "payload": {"headers": [{"name": "From", "value": f"Staff <{STAFF}>"}]}},
        {"id": "m-reply", "internalDate": ms, "snippet": "Yes",
         "payload": {"mimeType": "multipart/alternative", "headers": [
             {"name": "From", "value": "Pat <pat@example.test>"}, {"name": "Subject", "value": "Re: Saturday"}],
             "parts": [{"mimeType": "text/plain", "body": {"data": _b64("Yes, I'll be there.\n\nOn Mon, Staff wrote:\n> Can you work?")}}]}},
    ]}


@override_settings(GOOGLE_OAUTH_CLIENT_ID="id-test", GOOGLE_OAUTH_CLIENT_SECRET="secret-test")
class EmailReplyTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="er-admin", password="x-test-only")
        self.client.force_login(self.admin, backend=BACKEND)
        self.person = Participant.objects.create(first_name="SYNTHETIC", last_name="Pat", contact_email="pat@example.test")
        self.conn = GmailConnection.objects.create(user=self.admin, google_email=STAFF,
            refresh_token_encrypted=google.encrypt("refresh-test"), scopes="gmail.send " + google.READ_SCOPE)
        self.sent = SentEmail.objects.create(sender=self.admin, from_address=STAFF, participant=self.person,
            to_address="pat@example.test", subject="Saturday", status="sent", provider_reference="m-sent",
            thread_id="t1", sent_at=timezone.now() - timedelta(hours=1))

    def run_check(self):
        with patch.object(google, "access_token", return_value="tok"), \
             patch.object(google, "thread", return_value=_thread(self.sent.sent_at)) as fetch:
            added = email_replies.check_recent()
        return added, fetch

    def test_off_by_default_checks_nothing(self):
        added, fetch = self.run_check()
        self.assertEqual(added, 0)
        fetch.assert_not_called()

    def test_reply_copied_to_activity_once(self):
        self.client.post(reverse("messaging:connections-email-replies"), {"on": "1"})
        self.assertTrue(email_replies.site_enabled())
        added, _ = self.run_check()
        self.assertEqual(added, 1)
        reply = EmailReply.objects.get()
        self.assertEqual(reply.body, "Yes, I'll be there.")
        self.assertEqual((reply.interaction.direction, reply.interaction.activity_type), ("inbound", "email"))
        SentEmail.objects.update(replies_checked_at=None)
        self.assertEqual(self.run_check()[0], 0)
        self.assertEqual(EmailReply.objects.count(), 1)

    def test_staff_can_turn_off_and_send_only_connections_are_skipped(self):
        email_replies.set_enabled(actor=self.admin, enabled=True)
        self.client.post(reverse("messaging:gmail-replies"), {"on": "0"})
        self.assertEqual(self.run_check()[0], 0)
        GmailConnection.objects.update(track_replies=True, scopes="gmail.send")
        self.assertEqual(self.run_check()[0], 0)

    def test_connect_asks_for_read_only_when_turned_on(self):
        response = self.client.post(reverse("messaging:gmail-connect"))
        self.assertNotIn("gmail.readonly", response["Location"])
        email_replies.set_enabled(actor=self.admin, enabled=True)
        response = self.client.post(reverse("messaging:gmail-connect"))
        self.assertIn("gmail.readonly", response["Location"])
