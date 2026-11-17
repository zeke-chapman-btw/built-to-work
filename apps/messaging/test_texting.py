import base64
import hashlib
import hmac
import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.crm.models import Interaction
from apps.participants.models import Participant

from . import store, texting
from .models import TextMessage, TextOptOut

BACKEND = "django.contrib.auth.backends.ModelBackend"


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TextingTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.admin = users.create_superuser(username="tx-admin", password="x-test-only", first_name="Zed", last_name="Admin",
                                            email="zed@example.test")
        self.other = users.create_superuser(username="tx-other", password="x-test-only", first_name="Olive", last_name="Other",
                                            email="olive@example.test")
        self.client.force_login(self.admin, backend=BACKEND)
        self.person = Participant.objects.create(first_name="SYNTHETIC", last_name="Texter", contact_phone="(706) 555-0142")
        store.save(key="eztexting", values={"username": "btw"}, actor=self.admin, new_secret="pw-test-only")

    def send(self, replies_to=None, body="Can you work Saturday?"):
        with patch("apps.events.sms_providers.send_sms", return_value="eztexting:991") as sent:
            response = self.client.post(reverse("messaging:text-participant", args=[self.person.pk]), {
                "operation_id": str(uuid.uuid4()), "body": body, "replies_to": str((replies_to or self.admin).pk)})
        return response, sent

    def test_profile_has_text_button_and_sending_records_who_gets_replies(self):
        page = self.client.get(reverse("participants:staff-profile", args=[self.person.pk]))
        self.assertContains(page, reverse("messaging:text-participant", args=[self.person.pk]))
        response, sent = self.send(replies_to=self.other)
        self.assertEqual(response.status_code, 302)
        sent.assert_called_once()
        self.assertEqual(sent.call_args.args[0], "7065550142")
        row = TextMessage.objects.get()
        self.assertEqual((row.direction, row.sent_by, row.replies_to, row.state), ("out", self.admin, self.other, "sent"))
        self.assertEqual(row.interaction.source, "provider")
        self.assertEqual(row.interaction.activity_type, "sms")

    def test_double_submit_sends_once(self):
        op = str(uuid.uuid4())
        with patch("apps.events.sms_providers.send_sms", return_value="eztexting:1") as sent:
            for _ in range(2):
                self.client.post(reverse("messaging:text-participant", args=[self.person.pk]),
                                 {"operation_id": op, "body": "Hi", "replies_to": str(self.admin.pk)})
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(TextMessage.objects.count(), 1)

    def test_failed_send_is_marked_and_shown(self):
        with patch("apps.events.sms_providers.send_sms", side_effect=ValueError("SMS provider rejected the message")):
            response = self.client.post(reverse("messaging:text-participant", args=[self.person.pk]),
                                        {"operation_id": str(uuid.uuid4()), "body": "Hi", "replies_to": str(self.admin.pk)})
        self.assertContains(response, "The text was not sent", status_code=400)
        self.assertEqual(TextMessage.objects.get().state, "failed")

    def test_reply_goes_to_chosen_person_and_shows_in_their_inbox(self):
        self.send(replies_to=self.other)
        texting.receive({"type": "inbound_text.received", "fromNumber": "17065550142", "message": "Yes I can"})
        reply = TextMessage.objects.get(direction="in")
        self.assertEqual((reply.replies_to, reply.participant, reply.state), (self.other, self.person, "new"))
        self.assertEqual(reply.interaction.direction, "inbound")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("olive@example.test", mail.outbox[0].to)
        self.client.force_login(self.other, backend=BACKEND)
        page = self.client.get(reverse("messaging:texts"))
        self.assertContains(page, "Yes I can")
        self.client.force_login(self.admin, backend=BACKEND)
        self.assertNotContains(self.client.get(reverse("messaging:texts")), "Yes I can")
        self.assertContains(self.client.get(reverse("messaging:texts") + "?tab=all"), "Yes I can")

    def test_unmatched_reply_goes_to_default_owner_or_unassigned(self):
        texting.receive({"fromNumber": "14045550000", "message": "Who is this?"})
        self.assertIsNone(TextMessage.objects.get().replies_to)
        texting.set_owner(actor=self.admin, owner=self.other)
        texting.receive({"fromNumber": "14045550000", "message": "Hello?"})
        self.assertEqual(TextMessage.objects.latest("pk").replies_to, self.other)

    def test_stop_blocks_texting_until_start(self):
        texting.receive({"fromNumber": "17065550142", "message": "STOP"})
        self.assertTrue(TextOptOut.objects.filter(phone="7065550142").exists())
        response, sent = self.send()
        sent.assert_not_called()
        self.assertContains(response, "replied STOP", status_code=400)
        texting.receive({"fromNumber": "17065550142", "message": "start"})
        self.assertFalse(TextOptOut.objects.exists())

    def test_handled_and_reassign(self):
        texting.receive({"fromNumber": "17065550142", "message": "Question"})
        row = TextMessage.objects.get()
        self.client.post(reverse("messaging:text-reassign", args=[row.pk]), {"owner": str(self.other.pk)})
        row.refresh_from_db()
        self.assertEqual(row.replies_to, self.other)
        self.client.post(reverse("messaging:text-handled", args=[row.pk]))
        row.refresh_from_db()
        self.assertEqual((row.state, row.handled_by), ("done", self.admin))

    def test_replying_from_inbox_marks_reply_handled(self):
        texting.receive({"fromNumber": "17065550142", "message": "Question"})
        reply = TextMessage.objects.get()
        with patch("apps.events.sms_providers.send_sms", return_value="eztexting:5"):
            self.client.post(reverse("messaging:text-participant", args=[self.person.pk]) + f"?reply={reply.pk}",
                             {"operation_id": str(uuid.uuid4()), "body": "Answer", "replies_to": str(self.admin.pk)})
        reply.refresh_from_db()
        self.assertEqual(reply.state, "done")

    def turn_on(self):
        posted = []
        def fake(req, timeout):
            posted.append(json.loads(req.data))
            return _Resp()
        with patch("urllib.request.urlopen", side_effect=fake):
            response = self.client.post(reverse("messaging:connections-replies"), {"forward": "on", "owner": str(self.other.pk)})
        self.assertEqual(response.status_code, 302)
        return posted[0]

    def test_turn_on_registers_webhook_and_webhook_checks_signature(self):
        body = self.turn_on()
        self.assertEqual(body["type"], "inbound_text.received")
        self.assertIn("/messaging/eztexting/webhook/", body["callbackUrl"])
        self.assertNotIn(body["secret"], json.dumps(store.ConnectionSetting.objects.get(key="replies").values)
                         if hasattr(store, "ConnectionSetting") else json.dumps(texting.reply_settings()[1]))
        self.assertEqual(texting.default_owner(), self.other)
        path = "/" + body["callbackUrl"].split("/", 3)[3]
        payload = json.dumps({"type": "inbound_text.received", "fromNumber": "17065550142", "message": "Hi"}).encode()
        self.client.logout()
        bad = self.client.post(path, payload, content_type="application/json", HTTP_X_SIGNATURE_256="nope")
        self.assertEqual(bad.status_code, 403)
        sig = base64.b64encode(hmac.new(body["secret"].encode(), payload, hashlib.sha256).digest()).decode()
        good = self.client.post(path, payload, content_type="application/json", HTTP_X_SIGNATURE_256=sig)
        self.assertEqual(good.status_code, 200)
        self.assertEqual(TextMessage.objects.get().replies_to, self.other)
        wrong = self.client.post("/messaging/eztexting/webhook/wrong/", payload, content_type="application/json",
                                 HTTP_X_SIGNATURE_256=sig)
        self.assertEqual(wrong.status_code, 403)

    def test_connections_page_and_my_stuff_card(self):
        self.assertContains(self.client.get(reverse("messaging:connections")), "Turn on replies")
        self.send()
        texting.receive({"fromNumber": "17065550142", "message": "Yo"})
        self.assertContains(self.client.get(reverse("internal:home")), 'aria-label="1 new"')
        self.assertNotContains(self.client.get(reverse("my:schedule")), "texted you back")


class NewTextTests(TextingTests):
    def test_inbox_new_text_search_finds_people_with_mobile_numbers(self):
        self.assertContains(self.client.get(reverse("messaging:texts")), reverse("messaging:new-text"))
        Participant.objects.create(first_name="SYNTHETIC", last_name="Nophone", contact_email="np@example.test")
        page = self.client.get(reverse("messaging:new-text") + "?q=Texter", HTTP_X_BTW_EDITOR="1")
        self.assertEqual(page["X-BTW-Editor"], "1")
        self.assertContains(page, reverse("messaging:text-participant", args=[self.person.pk]))
        self.assertContains(page, "(706) 555-0142")
        self.assertNotContains(self.client.get(reverse("messaging:new-text") + "?q=Nophone"), "Nophone</strong>")
        self.assertContains(self.client.get(reverse("messaging:new-text") + "?q=0142"), "Texter")
