import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.customers.test_domain import WHY, CustomerFixtures

from . import pricing, services, stripe_api
from .models import BillingProfile, MonthlyAgreement, PositionCharge, StripeSettings

BACKEND = "django.contrib.auth.backends.ModelBackend"


class FakeStripe:
    """Records calls and answers like Stripe would."""
    def __init__(self, intent_status="processing"):
        self.calls, self.intent_status = [], intent_status

    def __call__(self, method, path, data=None, **kw):
        self.calls.append((method, path, data or {}))
        if path == "customers":
            return {"id": "cus_test1"}
        if path.startswith("customers/"):
            return {"id": path.split("/")[1]}
        if path == "checkout/sessions":
            return {"id": "cs_test1", "url": "https://checkout.stripe.test/c/1"}
        if path.startswith("checkout/sessions/"):
            return {"id": "cs_test1", "customer": "cus_test1", "setup_intent": "seti_1"}
        if path.startswith("setup_intents/"):
            return {"id": "seti_1", "status": "succeeded", "customer": "cus_test1", "mandate": "mandate_1",
                    "payment_method": {"id": "pm_1", "us_bank_account": {"bank_name": "SYNTHETIC BANK", "last4": "6789"}}}
        if path == "payment_intents":
            return {"id": "pi_1", "status": self.intent_status}
        if path == "products":
            return {"id": "prod_1"}
        if path == "subscriptions":
            return {"id": "sub_1", "status": "active"}
        if path.startswith("subscriptions/"):
            return {"id": "sub_1", "status": "canceled"}
        if path == "balance":
            return {"object": "balance"}
        raise AssertionError(path)

    def paths(self):
        return [p for _m, p, _d in self.calls]


def connect_stripe():
    row = StripeSettings.current()
    row.secret_key_encrypted = stripe_api.encrypt("sk_test_SYNTHETIC")
    row.webhook_secret_encrypted = stripe_api.encrypt("whsec_SYNTHETIC")
    row.save()


class PricingTests(TestCase):
    def test_fees(self):
        self.assertEqual(pricing.position_fee("skilled", 3)[0], 5000)
        self.assertEqual(pricing.position_fee("skilled", 6)[0], 9000)
        self.assertEqual(pricing.position_fee("skilled", 9)[0], 12000)
        self.assertEqual(pricing.position_fee("skilled", 15)[0], 18000)
        self.assertEqual(pricing.position_fee("general", 4)[0], 4000)
        self.assertEqual(pricing.position_fee("none", 4)[0], 0)
        self.assertIn("$5,000 + $4,000 + $3,000", pricing.position_fee("skilled", 9)[1])


class BillingFlowTests(CustomerFixtures, TestCase):
    def setUp(self):
        self.org_a = self.org(name="SYNTHETIC Alpha company")
        self.client.force_login(self.admin, backend=BACKEND)

    def reviewed_need(self):
        need = self.draft(self.org_a, self.payload(self.org_a))
        for action in ("submit", "review"):
            self.transition(self.org_a, need, action)
        need.refresh_from_db()
        self.org_a.refresh_from_db()
        return need

    def approve(self, need, **fee):
        data = {"org_revision": self.org_a.revision, "record_revision": need.revision, "working_id": need.working_revision_id,
                "reason": WHY, "fee_kind": "skilled", "candidates": 9, "people": 1}
        data.update(fee)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("customers:need-action", args=[self.org_a.pk, need.pk, "approve"]), data)

    def ready_bank(self, fake):
        connect_stripe()
        with patch.object(stripe_api, "call", fake):
            profile = services.profile_for(self.org_a)
            services.ensure_customer(profile)
            services.finish_setup("seti_1")
        return BillingProfile.objects.get(pk=profile.pk)

    def test_approval_charges_bank_right_away(self):
        fake = FakeStripe()
        profile = self.ready_bank(fake)
        self.assertEqual((profile.bank_status, profile.bank_label, profile.mandate_id), ("ready", "SYNTHETIC BANK ••6789", "mandate_1"))
        need = self.reviewed_need()
        with patch.object(stripe_api, "call", fake):
            response = self.approve(need)
        self.assertEqual(response.status_code, 302)
        charge = PositionCharge.objects.get()
        self.assertEqual((charge.amount_cents, charge.status, charge.payment_intent_id), (1200000, "processing", "pi_1"))
        _m, _p, data = [c for c in fake.calls if c[1] == "payment_intents"][0]
        self.assertEqual((data["amount"], data["payment_method"], data["mandate"], data["payment_method_types"]),
                         (1200000, "pm_1", "mandate_1", ["us_bank_account"]))
        page = self.client.get(reverse("customers:workspace", args=[self.org_a.pk, "billing"]))
        self.assertContains(page, "12,000") if "12,000" in page.content.decode() else self.assertContains(page, "12000")
        self.assertContains(page, "Processing")

    def test_approval_without_bank_waits_then_charges_when_bank_added(self):
        connect_stripe()
        need = self.reviewed_need()
        fake = FakeStripe(intent_status="succeeded")
        with patch.object(stripe_api, "call", fake):
            self.approve(need, fee_kind="general", people=4)
            charge = PositionCharge.objects.get()
            self.assertEqual((charge.amount_cents, charge.status), (400000, "waiting"))
            profile = services.profile_for(self.org_a)
            services.ensure_customer(profile)
            services.finish_setup("seti_1")
        charge.refresh_from_db()
        self.assertEqual(charge.status, "paid")

    def test_no_charge_and_skilled_sets_of_three(self):
        need = self.reviewed_need()
        bad = self.approve(need, candidates=7)
        self.assertEqual(bad.status_code, 200)
        self.assertFalse(PositionCharge.objects.exists())
        self.approve(need, fee_kind="none")
        self.assertEqual(PositionCharge.objects.get().status, "no_charge")

    def test_only_admins_and_managers_can_approve(self):
        from django.contrib.auth import get_user_model
        need = self.reviewed_need()
        other = get_user_model().objects.create_user(username="bl-staff", password="x-test-only", is_staff=True)
        self.client.force_login(other, backend=BACKEND)
        self.assertIn(self.approve(need).status_code, (302, 403))
        self.assertFalse(PositionCharge.objects.exists())

    def test_webhook_updates_charge_and_checks_signature(self):
        fake = FakeStripe()
        self.ready_bank(fake)
        need = self.reviewed_need()
        with patch.object(stripe_api, "call", fake):
            self.approve(need)
        event = json.dumps({"id": "evt_1", "type": "payment_intent.succeeded",
                            "data": {"object": {"id": "pi_1", "status": "succeeded"}}}).encode()
        self.client.logout()
        bad = self.client.post(reverse("billing:webhook"), event, content_type="application/json", HTTP_STRIPE_SIGNATURE="t=1,v1=x")
        self.assertEqual(bad.status_code, 400)
        stamp = int(time.time())
        sig = hmac.new(b"whsec_SYNTHETIC", f"{stamp}.".encode() + event, hashlib.sha256).hexdigest()
        for _ in range(2):
            ok = self.client.post(reverse("billing:webhook"), event, content_type="application/json",
                                  HTTP_STRIPE_SIGNATURE=f"t={stamp},v1={sig}")
            self.assertEqual(ok.status_code, 200)
        self.assertEqual(PositionCharge.objects.get().status, "paid")

    def test_monthly_agreement_with_end_date_and_renewal(self):
        fake = FakeStripe()
        self.ready_bank(fake)
        today = timezone.localdate()
        with patch.object(stripe_api, "call", fake):
            response = self.client.post(reverse("billing:agreement-new", args=[self.org_a.pk]), {
                "amount": "2500", "starts_on": today.isoformat(), "ends_on": (today + timedelta(days=180)).isoformat(),
                "description": "SYNTHETIC service"})
        self.assertEqual(response.status_code, 302)
        agreement = MonthlyAgreement.objects.get()
        self.assertEqual((agreement.status, agreement.subscription_id, agreement.amount_cents), ("active", "sub_1", 250000))
        _m, _p, data = [c for c in fake.calls if c[1] == "subscriptions"][0]
        self.assertIn("cancel_at", data)
        self.assertEqual(data["items"][0]["price_data"]["unit_amount"], 250000)
        renew = self.client.get(reverse("billing:agreement-renew", args=[self.org_a.pk, agreement.pk]))
        self.assertContains(renew, (agreement.ends_on + timedelta(days=1)).isoformat())

    def test_bank_link_email_and_public_page(self):
        connect_stripe()
        from apps.customers.models import CustomerContact
        contact = CustomerContact.objects.create(organization=self.org_a, first_name="SYNTHETIC", last_name="Payer",
                                                 email="payer@example.test")
        self.org_a.primary_contact = contact
        self.org_a.save(update_fields=["primary_contact"])
        self.client.post(reverse("billing:send-bank-link", args=[self.org_a.pk]))
        self.assertEqual(mail.outbox[0].to, ["payer@example.test"])
        link = [w for w in mail.outbox[0].body.split() if "/pay/bank/" in w][0]
        path = "/" + link.split("/", 3)[3]
        self.client.logout()
        page = self.client.get(path)
        self.assertContains(page, "Connect bank account")
        with patch.object(stripe_api, "call", FakeStripe()):
            go = self.client.get(path + "?go=1")
        self.assertEqual(go["Location"], "https://checkout.stripe.test/c/1")
        self.assertContains(self.client.get("/pay/bank/nonsense/"), "expired")

    def test_settings_page_saves_keys_without_showing_them(self):
        page = self.client.post(reverse("billing:settings"), {"secret_key": "sk_test_SYNTHETICKEY", "webhook_secret": ""})
        self.assertEqual(page.status_code, 302)
        self.assertEqual(stripe_api.secret_key(), "sk_test_SYNTHETICKEY")
        shown = self.client.get(reverse("billing:settings"))
        self.assertContains(shown, "Test mode")
        self.assertNotContains(shown, "sk_test_SYNTHETICKEY")
        self.assertContains(shown, "/billing/stripe/webhook/")

    def test_agreement_ending_reminder_goes_to_contact_and_manager(self):
        from apps.customers.models import CustomerContact
        from apps.reminders import engine
        from apps.reminders.models import ReminderNotice, ReminderRule, ReminderSetup
        contact = CustomerContact.objects.create(organization=self.org_a, first_name="SYNTHETIC", last_name="Payer",
                                                 email="payer@example.test")
        self.org_a.primary_contact = contact
        self.org_a.save(update_fields=["primary_contact"])
        profile = services.profile_for(self.org_a)
        profile.account_manager = self.admin
        profile.save()
        today = timezone.localdate()
        MonthlyAgreement.objects.create(organization=self.org_a, amount_cents=100000, starts_on=today - timedelta(days=300),
                                        ends_on=today + timedelta(days=7), status="active", approved_by=self.admin)
        setup = ReminderSetup.current()
        setup.is_on, setup.turned_on_at = True, timezone.now() - timedelta(days=1)
        setup.save()
        rule = ReminderRule.objects.get(key="agreement_ending")
        late = timezone.now().replace(hour=20)
        engine.run_rule(rule, now=late)
        self.assertEqual(ReminderNotice.objects.get().user, self.admin)
        to = sorted(m.to[0] for m in mail.outbox)
        self.assertIn("payer@example.test", to)
        customer_mail = [m for m in mail.outbox if m.to == ["payer@example.test"]][0]
        self.assertIn("does not renew on its own", customer_mail.body)
        self.assertNotIn("/customers/", customer_mail.body)


import shutil as _shutil
import tempfile as _tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.customers.test_portal import PortalFixtures

_TMP = _tempfile.mkdtemp(prefix="btw-portal-billing-")


@override_settings(PRIVATE_ONBOARDING_ROOT=_TMP)
class PortalBillingTests(PortalFixtures, TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        _shutil.rmtree(_TMP, ignore_errors=True)

    def test_lead_sees_billing_documents_and_can_upload(self):
        from apps.onboarding.models import CustomerDocument
        org, member, user = self.lead()
        MonthlyAgreement.objects.create(organization=org, amount_cents=150000, starts_on=timezone.localdate(),
                                        ends_on=timezone.localdate() + timedelta(days=300), status="active", approved_by=self.admin)
        CustomerDocument.objects.create(organization=org, kind="other", title="SYNTHETIC staff note", shared=False)
        CustomerDocument.objects.create(organization=org, kind="w9", title="W-9", file_name="w9.pdf",
                                        file=SimpleUploadedFile("w9.pdf", b"%PDF w9"))
        self.customer_login(user)
        page = self.client.get(reverse("customer_portal:workspace", args=["billing"]))
        self.assertContains(page, "Billing &amp; documents")
        self.assertContains(page, "1,500.00") if "1,500.00" in page.content.decode() else self.assertContains(page, "1500.00")
        self.assertContains(page, "W-9")
        self.assertNotContains(page, "SYNTHETIC staff note")
        upload = SimpleUploadedFile("coi.pdf", b"%PDF coi", content_type="application/pdf")
        response = self.client.post(reverse("billing:portal-upload"), {"kind": "insurance", "upload": upload})
        self.assertEqual(response.status_code, 302)
        doc = CustomerDocument.objects.get(kind="insurance")
        self.assertTrue(doc.from_customer)
        w9 = CustomerDocument.objects.get(kind="w9")
        got = self.client.get(reverse("billing:portal-document", args=[w9.pk]))
        self.assertEqual(b"".join(got.streaming_content), b"%PDF w9")
        hidden = CustomerDocument.objects.get(shared=False)
        self.assertEqual(self.client.get(reverse("billing:portal-document", args=[hidden.pk])).status_code, 404)
        self.client.force_login(self.admin, backend=BACKEND)
        self.assertContains(self.client.get(reverse("customers:workspace", args=[org.pk, "documents"])), "From customer")


class DeliveryTests(BillingFlowTests):
    def test_paid_for_vs_delivered_and_no_double_charge_on_reapproval(self):
        from . import delivery
        need = self.reviewed_need()
        self.approve(need, fee_kind="skilled", candidates=6)
        need.refresh_from_db()
        p = delivery.progress(need)
        self.assertEqual((p["paid_for"], p["delivered"], p["remaining"], p["unit"]), (6, 0, 6, "candidates"))
        page = self.client.get(reverse("customers:workspace", args=[self.org_a.pk, "billing"]))
        self.assertContains(page, "0 of 6 candidates delivered")
        # A changed version is approved again: the screen defaults to no new charge.
        self.org_a.refresh_from_db()
        data = self.payload(self.org_a)
        from apps.customers import services as svc
        svc.save_hiring_draft(actor=self.admin, org_id=self.org_a.pk, expected_org_revision=self.org_a.revision, data=data,
                              need_id=need.pk, expected_need_revision=need.revision, expected_working_id=None, why=WHY)
        need.refresh_from_db()
        for action in ("submit", "review"):
            self.transition(self.org_a, need, action)
        need.refresh_from_db()
        self.org_a.refresh_from_db()
        screen = self.client.get(reverse("customers:need-action", args=[self.org_a.pk, need.pk, "approve"]))
        self.assertContains(screen, "already charged")


class BusinessOverviewTests(BillingFlowTests):
    def test_business_page_shows_money_customers_funnel_and_events(self):
        need = self.reviewed_need()
        connect_stripe()
        with patch.object(stripe_api, "call", FakeStripe(intent_status="succeeded")):
            self.ready_bank(FakeStripe(intent_status="succeeded"))
            self.approve(need, fee_kind="general", people=2)
        page = self.client.get(reverse("billing:business") + "?range=all")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Position fees collected")
        self.assertContains(page, "$2,000")
        self.assertContains(page, "SYNTHETIC Alpha company")
        self.assertContains(page, "Participant path")
        self.assertContains(self.client.get(reverse("internal:reports")), reverse("billing:business"))
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create_user(username="bz-staff", password="x-test-only", is_staff=True)
        self.client.force_login(other, backend=BACKEND)
        self.assertEqual(self.client.get(reverse("billing:business")).status_code, 403)
