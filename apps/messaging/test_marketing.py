import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.models import AuditLog
from apps.participants.intake_wizard import session_revision
from apps.participants.models import Participant
from apps.participants import test_intake_ui_11b as intake_ui

from . import mailchimp, marketing, store
from .models import MARKETING_WORDING, MarketingConsent


class SignupCheckboxTests(TestCase):
    setUp = intake_ui.IntakeUI11BTests.setUp
    answers = intake_ui.IntakeUI11BTests.answers
    begin = intake_ui.IntakeUI11BTests.begin
    step = intake_ui.IntakeUI11BTests.step

    def consent_step(self, session, url, marketing_box):
        answers = self.answers()
        values = {q["key"]: answers[q["key"]] for q in self.version.snapshot["questions"]
                  if q["step"] == 1 and q["type"] == "consent" and q["key"] in answers}
        session.refresh_from_db()
        values["revision"] = session_revision(session)
        if marketing_box:
            values["marketing_email"] = "yes"
        return self.client.post(url + "?step=2", values)

    def run_signup(self, marketing_box):
        session, url = self.begin()
        self.step(session, url, 1)
        page = self.client.get(url)
        self.assertContains(page, MARKETING_WORDING)
        self.assertContains(page, 'name="marketing_email"')
        self.assertEqual(self.consent_step(session, url, marketing_box).status_code, 302)
        for number in (3, 4, 5):
            self.assertEqual(self.step(session, url, number).status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.state, "submitted")
        return session

    def test_ticked_box_turns_marketing_on_with_proof(self):
        session = self.run_signup(True)
        row = MarketingConsent.objects.get()
        self.assertEqual((row.status, row.source, row.wording), ("on", "signup", MARKETING_WORDING))
        self.assertEqual(row.participant_id, session.submission.participant_id)
        self.assertEqual(row.submission_id, session.submission_id)
        self.assertTrue(AuditLog.objects.filter(source="marketing_consent").exists())

    def test_unticked_box_records_nothing(self):
        self.run_signup(False)
        self.assertFalse(MarketingConsent.objects.exists())


class StaffConsentTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="mk-admin", password="x-test-only")
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.person = Participant.objects.create(first_name="SYNTHETIC", last_name="Person", contact_email="p@example.test")
        self.url = reverse("messaging:marketing-edit", args=[self.person.pk])

    def test_turning_on_needs_a_reason_and_off_is_simple(self):
        response = self.client.post(self.url, {"status": "on", "note": ""})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MarketingConsent.objects.exists())
        self.client.post(self.url, {"status": "on", "note": "Paper form, Augusta event"})
        self.assertEqual(marketing.status_of(self.person), "on")
        self.client.post(self.url, {"status": "off", "note": ""})
        self.assertEqual(marketing.status_of(self.person), "off")
        self.assertEqual(len(marketing.history(self.person)), 2)

    def test_profile_shows_chip(self):
        marketing.set_status(participant=self.person, status="on", source="staff", actor=self.admin, note="x")
        page = self.client.get(reverse("participants:staff-profile", args=[self.person.pk]))
        self.assertContains(page, "Marketing email")
        self.assertContains(page, 'cp3-chip g')


class _Resp:
    def __init__(self, data=None):
        self._d = json.dumps(data or {}).encode()
    def read(self):
        return self._d
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class MailchimpTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="mc-admin", password="x-test-only")
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.person = Participant.objects.create(first_name="SYNTHETIC", last_name="Welder", state="GA",
                                                 contact_email="Welder@Example.test")

    def connect(self):
        calls = []
        def fake(req, timeout):
            calls.append((req.get_method(), req.full_url, req.data))
            if req.full_url.endswith("/lists?fields=lists.id%2Clists.name%2Clists.stats.member_count&count=50"):
                return _Resp({"lists": [{"id": "aud1", "name": "BTW", "stats": {"member_count": 0}}]})
            return _Resp({"webhooks": []} if req.get_method() == "GET" else {})
        with patch("apps.messaging.mailchimp.request.urlopen", side_effect=fake):
            response = self.client.post(reverse("messaging:mailchimp-setup"), {"api_key": "abc123-us21"})
            self.assertContains(response, "BTW (0 contacts)", status_code=200)
            self.client.post(reverse("messaging:mailchimp-setup"), {"step": "audience", "audience_id": "aud1"})
        return calls

    def test_connect_stores_key_encrypted_and_registers_webhook(self):
        calls = self.connect()
        self.assertTrue(mailchimp.connected())
        row = mailchimp.settings_row()
        self.assertNotIn("abc123", row.secret_encrypted)
        self.assertTrue(any(m == "POST" and u.endswith("/lists/aud1/webhooks") for m, u, _ in calls))
        self.assertTrue(all("us21.api.mailchimp.com" in u for _, u, _ in calls))
        self.assertTrue(row.values["webhook_ok"])

    def test_sync_adds_opted_in_people_with_tags_and_archives_others(self):
        self.connect()
        marketing.set_status(participant=self.person, status="on", source="staff", actor=self.admin, note="x")
        sent = []
        with patch("apps.messaging.mailchimp.request.urlopen", side_effect=lambda req, timeout: sent.append(req) or _Resp()):
            self.assertEqual(mailchimp.sync_pending(), 1)
        put = sent[0]
        self.assertEqual(put.get_method(), "PUT")
        self.assertIn(mailchimp.subscriber_hash("welder@example.test"), put.full_url)
        tags = json.loads(sent[1].data)["tags"]
        self.assertIn({"name": "State: GA", "status": "active"}, tags)
        self.assertEqual(MarketingConsent.objects.get().synced_email, "welder@example.test")
        marketing.set_status(participant=self.person, status="off", source="staff", actor=self.admin)
        with patch("apps.messaging.mailchimp.request.urlopen", side_effect=lambda req, timeout: sent.append(req) or _Resp()):
            mailchimp.sync_pending()
        self.assertEqual(sent[-1].get_method(), "DELETE")
        self.assertEqual(MarketingConsent.objects.get().synced_email, "")

    def test_unsubscribe_webhook_turns_off_and_needs_secret(self):
        self.connect()
        marketing.set_status(participant=self.person, status="on", source="staff", actor=self.admin, note="x")
        with patch("apps.messaging.mailchimp.request.urlopen", return_value=_Resp()):
            mailchimp.sync_pending()
        secret = mailchimp.settings_row().values["webhook_secret"]
        self.client.logout()
        self.assertEqual(self.client.post(reverse("messaging:mailchimp-webhook", args=["wrong"]), {}).status_code, 404)
        self.assertEqual(self.client.get(reverse("messaging:mailchimp-webhook", args=[secret])).status_code, 200)
        self.client.post(reverse("messaging:mailchimp-webhook", args=[secret]),
                         {"type": "unsubscribe", "data[email]": "welder@example.test"})
        row = MarketingConsent.objects.get()
        self.assertEqual((row.status, row.source, row.needs_sync), ("unsubscribed", "mailchimp", True))

    def test_profile_change_marks_for_resync(self):
        self.connect()
        marketing.set_status(participant=self.person, status="on", source="staff", actor=self.admin, note="x")
        with patch("apps.messaging.mailchimp.request.urlopen", return_value=_Resp()):
            mailchimp.sync_pending()
        self.assertFalse(MarketingConsent.objects.get().needs_sync)
        self.person.city = "Augusta"
        self.person.save()
        self.assertTrue(MarketingConsent.objects.get().needs_sync)

    def test_connections_page_shows_mailchimp_card(self):
        page = self.client.get(reverse("messaging:connections"))
        self.assertContains(page, "Marketing email · Mailchimp")
        self.assertContains(page, "Connect Mailchimp")
