from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.participants.models import Participant

from . import bounces

GMAIL_BOUNCE = b"""From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>
To: noreply@example.test
Subject: Delivery Status Notification (Failure)
X-Failed-Recipients: wrong.address@example.test
Content-Type: multipart/report; report-type=delivery-status; boundary="b1"

--b1
Content-Type: text/plain; charset="UTF-8"

Address not found
Your message wasn't delivered to wrong.address@example.test because the address couldn't be found.

--b1
Content-Type: message/delivery-status

Reporting-MTA: dns; googlemail.com
Final-Recipient: rfc822; wrong.address@example.test
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp; 550 5.1.1 The email account that you tried to reach does not exist.

--b1--
"""
ORDINARY = b"From: Pat <pat@example.test>\nSubject: Re: your ticket\n\nThanks!\n"


class BounceTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="b-admin", password="x-test-only")
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.person = Participant.objects.create(first_name="SYNTHETIC", last_name="Bounce",
                                                 contact_email="Wrong.Address@example.test")

    def test_parse_gmail_bounce_and_ignore_ordinary_mail(self):
        failed, reason = bounces.parse(GMAIL_BOUNCE, own_address="noreply@example.test")
        self.assertEqual(failed, ["wrong.address@example.test"])
        self.assertIn("does not exist", reason)
        self.assertEqual(bounces.parse(ORDINARY), ([], ""))

    def test_bounced_address_pauses_automatic_email_and_staff_can_clear(self):
        from apps.events.communications import _provider_send
        bounces.record("wrong.address@example.test", "550 does not exist")
        page = self.client.get(reverse("participants:staff-profile", args=[self.person.pk]))
        self.assertContains(page, "Email bounced")
        with patch("apps.messaging.store.email_settings", return_value={"from_address": "noreply@example.test",
                                                                        "reply_to": [], "source": ""}):
            with self.assertRaisesMessage(ValueError, "bounced"):
                _provider_send("email", "wrong.address@example.test", subject="x", body="y", idempotency_key="kb")
        self.client.post(reverse("messaging:bounce-clear", args=[self.person.pk]))
        self.assertFalse(bounces.bounced("wrong.address@example.test"))
        self.assertNotContains(self.client.get(reverse("participants:staff-profile", args=[self.person.pk])), "Email bounced")

    def test_inbox_check_reads_notices_with_saved_sign_in(self):
        class Box:
            def __init__(self, *a, **k): pass
            def login(self, u, p): self.user = u
            def select(self, name): return ("OK", [b"1"])
            def search(self, *a): return ("OK", [b"1 2"])
            def fetch(self, mid, what): return ("OK", [(b"1", GMAIL_BOUNCE if mid == b"1" else ORDINARY)])
            def logout(self): pass
        config = {"source": "app", "host": "smtp.gmail.com", "username": "noreply@example.test", "password": "pw-test"}
        with patch("apps.messaging.store.email_settings", return_value=config), patch("imaplib.IMAP4_SSL", Box):
            self.assertEqual(bounces.check_inbox(), 1)
        self.assertTrue(bounces.bounced("wrong.address@example.test"))
