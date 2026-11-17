import re
import shutil
import tempfile
from datetime import timedelta

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.customers.test_domain import CustomerFixtures

from .models import AgreementVersion, CustomerOnboarding, OnboardingQuestion

BACKEND = "django.contrib.auth.backends.ModelBackend"
TMP = tempfile.mkdtemp(prefix="btw-onboarding-test-")


@override_settings(PRIVATE_ONBOARDING_ROOT=TMP)
class OnboardingTests(CustomerFixtures, TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        self.client.force_login(self.admin, backend=BACKEND)

    def hiring_answers(self):
        data = {}
        for q in OnboardingQuestion.objects.filter(is_active=True):
            key = f"q{q.pk}"
            if q.kind == "multi":
                data[key] = [q.option_list()[0]]
            elif q.kind in ("choice",):
                data[key] = q.option_list()[0]
            elif q.kind == "yesno":
                data[key] = "Yes"
            else:
                data[key] = f"SYNTHETIC answer to {q.label[:20]}"
        return data

    def company(self):
        return {"name": "SYNTHETIC Onboard Co", "legal_name": "SYNTHETIC Onboard LLC", "industry": "Construction",
                "website": "example.test", "phone": "706-555-0100", "business_email": "office@example.test",
                "address_line1": "1 Test Way", "city": "Athens", "state": "GA", "postal_code": "30601", "employees": "40"}

    def contacts(self):
        return {"main_first_name": "SYNTHETIC", "main_last_name": "Main", "main_email": "main@example.test", "main_portal": "on",
                "billing_first_name": "SYNTHETIC", "billing_last_name": "Billing", "billing_email": "ap@example.test",
                "other1_first_name": "SYNTHETIC", "other1_last_name": "Foreman", "other1_email": "foreman@example.test"}

    def test_seeded_questions_and_placeholder_agreement(self):
        self.assertGreaterEqual(OnboardingQuestion.objects.count(), 10)
        labels = list(OnboardingQuestion.objects.values_list("label", flat=True))
        self.assertTrue(any("Required qualifications" in label for label in labels))
        self.assertTrue(any("Standards" in label for label in labels))
        self.assertIn("DRAFT", AgreementVersion.current().body)

    def test_customer_link_full_flow_then_staff_creates_customer(self):
        self.client.post(reverse("onboarding:start"))
        row = CustomerOnboarding.objects.get()
        response = self.client.post(reverse("onboarding:send", args=[row.pk]), {"email": "main@example.test"})
        self.assertEqual(response.status_code, 302)
        link = re.search(r"https?://\S+/onboard/\S+/", mail.outbox[0].body).group(0)
        path = "/" + link.split("/", 3)[3]
        self.client.logout()
        self.assertContains(self.client.get(path, follow=True), "Company")
        base = path.rstrip("/") + "/"
        self.assertEqual(self.client.post(base + "company/", self.company()).status_code, 302)
        self.assertEqual(self.client.post(base + "contacts/", self.contacts()).status_code, 302)
        self.assertEqual(self.client.post(base + "hiring/", self.hiring_answers()).status_code, 302)
        self.assertEqual(self.client.get(base + "setup/").status_code, 404)
        unsigned = self.client.post(base + "documents/", {})
        self.assertContains(unsigned, "Type your full name")
        w9 = SimpleUploadedFile("w9.pdf", b"%PDF-1.4 SYNTHETIC", content_type="application/pdf")
        signed = self.client.post(base + "documents/", {"w9": w9, "agree": "on", "signed_name": "SYNTHETIC Main", "signed_title": "Owner"})
        self.assertEqual(signed.status_code, 302)
        review = self.client.get(base + "review/")
        self.assertContains(review, "Signed by SYNTHETIC Main")
        self.assertContains(self.client.post(base + "review/"), "Thank you")
        self.assertContains(self.client.get(base + "company/"), "We have your onboarding form")
        row.refresh_from_db()
        self.assertEqual(row.status, "submitted")
        self.assertIn("DRAFT", row.agreement_text)

        self.client.force_login(self.admin, backend=BACKEND)
        today = timezone.localdate()
        self.client.post(reverse("onboarding:step", args=[row.pk, "setup"]), {
            "lead_limit": 2, "member_limit": 5, "workforce_need_creation": "on", "account_manager": str(self.admin.pk),
            "monthly_amount": "1500", "starts_on": today.isoformat(), "ends_on": (today + timedelta(days=365)).isoformat()})
        self.assertContains(self.client.get(reverse("onboarding:step", args=[row.pk, "review"])), "Create customer")
        with self.captureOnCommitCallbacks(execute=True):
            created = self.client.post(reverse("onboarding:create", args=[row.pk]))
        row.refresh_from_db()
        org = row.organization
        self.assertRedirects(created, reverse("customers:workspace", args=[org.pk, "overview"]), fetch_redirect_response=False)
        self.assertEqual((org.name, org.status, org.primary_contact.email), ("SYNTHETIC Onboard Co", "active", "main@example.test"))
        self.assertEqual(org.contacts.count(), 3)
        self.assertEqual(org.locations.get().city, "Athens")
        self.assertEqual((org.access_plan.lead_limit, org.access_plan.member_limit), (2, 5))
        self.assertEqual((org.billing.account_manager, org.billing.billing_email), (self.admin, "ap@example.test"))
        agreement = org.monthly_agreements.get()
        self.assertEqual((agreement.amount_cents, agreement.status), (150000, "not_set_up"))
        docs = self.client.get(reverse("customers:workspace", args=[org.pk, "documents"]))
        self.assertContains(docs, "W-9")
        self.assertContains(docs, "Signed by SYNTHETIC Main")
        self.assertNotContains(docs, "Missing")
        from .models import CustomerDocument
        agreement_doc = CustomerDocument.objects.get(organization=org, kind="agreement")
        self.assertContains(self.client.get(reverse("onboarding:document-file", args=[org.pk, agreement_doc.pk])), "DRAFT")
        download = self.client.get(reverse("onboarding:w9", args=[row.pk]))
        self.assertEqual(b"".join(download.streaming_content), b"%PDF-1.4 SYNTHETIC")
        # Creating twice never makes a second customer.
        self.client.post(reverse("onboarding:create", args=[row.pk]))
        from apps.customers.models import CustomerOrganization
        self.assertEqual(CustomerOrganization.objects.filter(name="SYNTHETIC Onboard Co").count(), 1)

    def test_staff_can_fill_it_in_without_signature(self):
        self.client.post(reverse("onboarding:start"))
        row = CustomerOnboarding.objects.get()
        self.client.post(reverse("onboarding:step", args=[row.pk, "company"]), self.company())
        response = self.client.post(reverse("onboarding:step", args=[row.pk, "documents"]), {})
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(reverse("onboarding:list")), "SYNTHETIC Onboard Co")

    def test_settings_questions_and_new_agreement_version(self):
        page = self.client.get(reverse("onboarding:settings"))
        self.assertContains(page, "Placeholder: replace before sending")
        response = self.client.post(reverse("onboarding:question-new"), {"section": "people", "label": "SYNTHETIC new question",
                                    "kind": "choice", "options": "A\nB", "position": 5, "is_active": "on"})
        self.assertEqual(response.status_code, 302)
        self.client.post(reverse("onboarding:agreement"), {"title": "BTW Agreement", "body": "SYNTHETIC real terms"})
        self.assertEqual((AgreementVersion.current().version, AgreementVersion.current().body), (2, "SYNTHETIC real terms"))
        self.assertEqual(AgreementVersion.objects.count(), 2)

    def test_bad_link_and_staff_only(self):
        self.assertEqual(self.client.get("/onboard/nonsense/").status_code, 404)
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create_user(username="ob-staff", password="x-test-only", is_staff=True)
        self.client.force_login(other, backend=BACKEND)
        self.assertEqual(self.client.get(reverse("onboarding:list")).status_code, 403)


@override_settings(PRIVATE_ONBOARDING_ROOT=TMP)
class DocumentTests(CustomerFixtures, TestCase):
    def setUp(self):
        self.org_a = self.org(name="SYNTHETIC Docs Co")
        self.client.force_login(self.admin, backend=BACKEND)

    def test_upload_edit_download_remove_and_expiry_reminder(self):
        from .models import CustomerDocument
        url = reverse("onboarding:document-new", args=[self.org_a.pk])
        self.assertContains(self.client.get(reverse("customers:workspace", args=[self.org_a.pk, "documents"])), "Missing")
        upload = SimpleUploadedFile("coi.pdf", b"%PDF SYNTHETIC COI", content_type="application/pdf")
        soon = timezone.localdate() + timedelta(days=7)
        response = self.client.post(url, {"kind": "insurance", "title": "", "expires_on": soon.isoformat(), "upload": upload},
                                    HTTP_X_BTW_EDITOR="1")
        self.assertEqual(response.status_code, 200)
        doc = CustomerDocument.objects.get()
        self.assertEqual((doc.title, doc.file_name), ("Certificate of insurance", "coi.pdf"))
        page = self.client.get(reverse("customers:workspace", args=[self.org_a.pk, "documents"]))
        self.assertContains(page, "Expires soon")
        got = self.client.get(reverse("onboarding:document-file", args=[self.org_a.pk, doc.pk]))
        self.assertEqual(b"".join(got.streaming_content), b"%PDF SYNTHETIC COI")
        self.assertEqual(self.client.post(url, {"kind": "other"}, HTTP_X_BTW_EDITOR="1").status_code, 400)
        from apps.billing.services import profile_for
        from apps.reminders import engine
        from apps.reminders.models import ReminderNotice, ReminderRule, ReminderSetup
        profile = profile_for(self.org_a)
        profile.account_manager = self.admin
        profile.save()
        setup = ReminderSetup.current()
        setup.is_on, setup.turned_on_at = True, timezone.now()
        setup.save()
        engine.run_rule(ReminderRule.objects.get(key="document_expiring"), now=timezone.now().replace(hour=20))
        self.assertIn("Certificate of insurance", ReminderNotice.objects.get().title)
        self.client.post(reverse("onboarding:document-edit", args=[self.org_a.pk, doc.pk]), {"kind": "insurance", "title": "COI", "remove": "1"})
        doc.refresh_from_db()
        self.assertIsNotNone(doc.removed_at)
        self.assertNotContains(self.client.get(reverse("customers:workspace", args=[self.org_a.pk, "documents"])), "COI")
