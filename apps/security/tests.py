import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from . import services, totp
from .models import ErrorAlert, SecuritySettings, TwoStepDevice

BACKEND = "django.contrib.auth.backends.ModelBackend"


class TotpTests(TestCase):
    def test_known_vector_and_window(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238 test key
        self.assertEqual(totp.code_at(secret, 59 // 30), "287082")
        now = time.time()
        code = totp.code_at(secret, totp.current_step(now))
        self.assertIsNotNone(totp.verify(secret, code, now=now))
        self.assertIsNone(totp.verify(secret, code, now=now, after_step=totp.current_step(now)))
        self.assertIsNone(totp.verify(secret, "000000x", now=now))


class TwoStepTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="ts-admin", password="x-test-only", email="ts@example.test")

    def enroll(self):
        self.client.force_login(self.user, backend=BACKEND)
        self.assertContains(self.client.get(reverse("security:setup")), "Text me a code")
        self.client.get(reverse("security:setup") + "?method=app")
        secret = services.secret_of(TwoStepDevice.objects.get(user=self.user))
        response = self.client.post(reverse("security:setup"), {"method": "app", "code": totp.code_at(secret, totp.current_step())})
        self.assertContains(response, "recovery codes")
        return secret, [line.strip() for line in response.content.decode().split("<pre")[1].split(">", 1)[1].split("</pre>")[0].split()]

    def test_enroll_then_sign_in_needs_code_and_recovery_code_works_once(self):
        secret, codes = self.enroll()
        self.assertEqual(len(codes), 10)
        self.assertEqual(self.client.get(reverse("internal:settings")).status_code, 200)
        self.client.logout()
        self.client.force_login(self.user, backend=BACKEND)
        gate = self.client.get(reverse("internal:settings"))
        self.assertRedirects(gate, reverse("security:verify") + "?next=" + reverse("internal:settings").replace("/", "%2F"),
                             fetch_redirect_response=False)
        bad = self.client.post(reverse("security:verify"), {"code": "123456", "next": "/settings/"})
        self.assertContains(bad, "That code")
        ok = self.client.post(reverse("security:verify"), {"code": codes[0], "next": "/settings/"})
        self.assertRedirects(ok, "/settings/", fetch_redirect_response=False)
        self.client.logout()
        self.client.force_login(self.user, backend=BACKEND)
        again = self.client.post(reverse("security:verify"), {"code": codes[0], "next": "/settings/"})
        self.assertContains(again, "That code")
        step_code = totp.code_at(secret, totp.current_step() + 1)
        self.assertEqual(self.client.post(reverse("security:verify"), {"code": step_code, "next": "/settings/"}).status_code, 302)

    def test_required_for_managers_sends_them_to_setup(self):
        row = SecuritySettings.current()
        row.require_two_step_managers = True
        row.save()
        self.client.force_login(self.user, backend=BACKEND)
        response = self.client.get(reverse("internal:home"))
        self.assertRedirects(response, reverse("security:setup") + "?required=1", fetch_redirect_response=False)
        self.assertEqual(self.client.post(reverse("security:off")).status_code, 302)

    def test_settings_page_and_legal_pages(self):
        self.client.force_login(self.user, backend=BACKEND)
        page = self.client.get(reverse("security:settings"))
        self.assertContains(page, "Two-step sign-in")
        self.assertContains(self.client.get("/privacy/"), "DRAFT")
        self.client.post(reverse("security:legal-edit", args=["terms"]), {"body": "SYNTHETIC terms\n\nSecond paragraph"})
        self.client.logout()
        terms = self.client.get("/terms/")
        self.assertContains(terms, "SYNTHETIC terms")
        self.assertContains(terms, "Second paragraph")


@override_settings(BTW_ERROR_ALERTS=True)
class ErrorAlertTests(TestCase):
    def test_error_emails_admins_once_an_hour(self):
        import logging
        from . import alerts
        get_user_model().objects.create_superuser(username="ea-admin", password="x-test-only", email="ea@example.test")
        handler = alerts.AdminAlertHandler()
        try:
            raise ValueError("SYNTHETIC boom")
        except ValueError:
            import sys
            record = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "Internal Server Error", None, sys.exc_info())
        with patch.object(alerts.threading, "Thread") as thread:
            thread.side_effect = lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})()
            with patch("django.db.close_old_connections"):
                handler.emit(record)
                handler.emit(record)
        row = ErrorAlert.objects.get()
        self.assertEqual(row.count, 2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("SYNTHETIC boom", mail.outbox[0].subject)


class SentCodeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="sc-admin", password="x-test-only", email="sc@example.test")

    def code_from_mail(self, index=-1):
        import re
        return re.search(r"code is (\d{6})", mail.outbox[index].body).group(1)

    def test_email_code_setup_and_sign_in(self):
        self.client.force_login(self.user, backend=BACKEND)
        page = self.client.get(reverse("security:setup") + "?method=email")
        self.assertContains(page, "sc…@example.test")
        self.assertEqual(mail.outbox[-1].to, ["sc@example.test"])
        done = self.client.post(reverse("security:setup"), {"method": "email", "code": self.code_from_mail()})
        self.assertContains(done, "recovery codes")
        self.assertEqual(TwoStepDevice.objects.get(user=self.user).method, "email")
        self.client.logout()
        self.client.force_login(self.user, backend=BACKEND)
        before = len(mail.outbox)
        self.client.get(reverse("security:verify") + "?next=/settings/")
        self.client.get(reverse("security:verify") + "?next=/settings/")  # a refresh doesn't send another
        self.assertEqual(len(mail.outbox), before + 1)
        code = self.code_from_mail()
        self.assertContains(self.client.post(reverse("security:verify"), {"code": "000000", "next": "/settings/"}), "That code")
        ok = self.client.post(reverse("security:verify"), {"code": code, "next": "/settings/"})
        self.assertRedirects(ok, "/settings/", fetch_redirect_response=False)
        self.client.logout()
        self.client.force_login(self.user, backend=BACKEND)
        self.client.get(reverse("security:verify"))
        self.assertContains(self.client.post(reverse("security:verify"), {"code": code}), "That code")

    def test_text_code_uses_staff_phone(self):
        from apps.messaging import store
        from apps.staffing.models import StaffProfile
        StaffProfile.objects.create(first_name="SC", last_name="Admin", phone="(706) 555-0123", user=self.user)
        store.save(key="eztexting", values={"username": "btw"}, actor=self.user, new_secret="pw-test-only")
        self.client.force_login(self.user, backend=BACKEND)
        with patch("apps.events.sms_providers.send_sms", return_value="ez:1") as sent:
            page = self.client.get(reverse("security:setup") + "?method=text")
        self.assertContains(page, "ending in 0123")
        number, body = sent.call_args.args
        self.assertEqual(number, "7065550123")
        import re
        code = re.search(r"code is (\d{6})", body).group(1)
        self.assertContains(self.client.post(reverse("security:setup"), {"method": "text", "code": code}), "recovery codes")


class HelpTests(TestCase):
    def test_help_page_and_menu_link(self):
        user = get_user_model().objects.create_superuser(username="hp-admin", password="x-test-only")
        self.client.force_login(user, backend=BACKEND)
        page = self.client.get(reverse("security:help"))
        self.assertContains(page, "Getting started")
        self.assertContains(page, "Adding a staff member")
        self.assertContains(self.client.get(reverse("internal:home")), reverse("security:help"))

    def test_add_staff_can_assign_reminder_roles(self):
        from apps.reminders.models import ReminderRole
        from apps.accounts.roles import role_configuration
        role_configuration(apply=True)
        admin = get_user_model().objects.create_superuser(username="hp-admin2", password="x-test-only")
        self.client.force_login(admin, backend=BACKEND)
        form = self.client.get(reverse("accounts:staff-create"))
        self.assertContains(form, "Reminder roles they cover")
        response = self.client.post(reverse("accounts:staff-create"), {
            "first_name": "SYNTHETIC", "last_name": "Recruiter", "email": "sr@example.test", "phone": "",
            "login_mode": "new", "username": "synthetic-recruiter", "login_email": "sr@example.test", "roles": ["recruiter"],
            "reminder_roles": ["recruiting_lead"], "my_pay_access": "no", "confirm_identity": "on", "is_active": "on"})
        self.assertIn(response.status_code, (200, 302))
        made = get_user_model().objects.filter(username="synthetic-recruiter").first()
        if response.status_code == 302:
            self.assertIn(made, ReminderRole.objects.get(key="recruiting_lead").holders.all())
        else:
            self.fail(str(response.context["form"].errors))


from apps.customers.test_portal import PortalFixtures


class CustomerTwoStepTests(PortalFixtures, TestCase):
    def test_customer_can_turn_it_on_and_is_asked_next_time(self):
        org, member, user = self.lead()
        self.customer_login(user)
        account = self.client.get(reverse("customer_portal:workspace", args=["account"]))
        self.assertContains(account, reverse("security:setup"))
        self.client.get(reverse("security:setup") + "?method=email")
        import re
        code = re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)
        self.assertContains(self.client.post(reverse("security:setup"), {"method": "email", "code": code}), "recovery codes")
        self.client.logout()
        self.customer_login(user)
        gate = self.client.get(reverse("customer_portal:home"))
        self.assertEqual(gate.status_code, 302)
        self.assertIn(reverse("security:verify"), gate["Location"])
        code = re.search(r"code is (\d{6})", self.client.get(gate["Location"]) and mail.outbox[-1].body).group(1)
        ok = self.client.post(reverse("security:verify"), {"code": code, "next": reverse("customer_portal:home")})
        self.assertRedirects(ok, reverse("customer_portal:home"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("customer_portal:home")).status_code, 200)

    def test_required_for_customers(self):
        row = SecuritySettings.current()
        row.require_two_step_customers = True
        row.save()
        org, member, user = self.lead()
        self.customer_login(user)
        gate = self.client.get(reverse("customer_portal:home"))
        self.assertIn(reverse("security:setup"), gate["Location"])
