"""The slimmer recommended sign-up and the rebuilt Forms pages."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.participants.intake_contracts import validate_contract
from apps.participants.intake_defaults import RETIRED_FROM_RECOMMENDED, recommended_contract
from apps.participants.intake_lifecycle import create_form, revision


class RecommendedIntakeTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="forms-admin", email="f@example.test", password="x")
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")

    def test_recommended_set_is_valid_and_drops_overlaps(self):
        contract = validate_contract(recommended_contract())
        keys = {q["key"] for q in contract["questions"]}
        self.assertFalse(keys & set(RETIRED_FROM_RECOMMENDED))
        group = next(q for q in contract["questions"] if q["key"] == "experiences")
        self.assertNotIn("paid", {c["key"] for c in group["children"]})
        priorities = next(q for q in contract["questions"] if q["key"] == "career_priorities")
        self.assertEqual(priorities["max_selections"], 3)
        self.assertIn("benefits", {o["value"] for o in priorities["options"]})
        certs = next(q for q in contract["questions"] if q["key"] == "certification_choices")
        self.assertEqual(certs["exclusive"], ["none"])
        self.assertNotIn("rule", certs)

    def test_make_recommended_draft_from_form_page(self):
        version = create_form(actor=self.admin, name="SYNTHETIC Long form")
        url = reverse("participants:intake-config-detail", args=[version.form_id])
        page = self.client.get(url)
        self.assertContains(page, "Shorter sign-up available")
        self.client.post(url, {"action": "use_recommended", "revision": revision(version)})
        latest = version.form.versions.order_by("-version").first()
        self.assertEqual(latest.status, "draft")
        self.assertNotIn("street_address", {q["key"] for q in latest.snapshot["questions"]})
        self.assertNotContains(self.client.get(url), "Shorter sign-up available")

    def test_forms_hub_lists_forms_and_places(self):
        create_form(actor=self.admin, name="SYNTHETIC Hub form")
        page = self.client.get(reverse("participants:intake-config-index"))
        self.assertContains(page, "Your forms")
        self.assertContains(page, "SYNTHETIC Hub form")
        self.assertContains(page, "General sign-up link")

    def test_new_form_from_hub_starts_with_recommended_questions(self):
        self.client.post(reverse("participants:intake-config-index"), {"name": "SYNTHETIC Fresh"})
        from apps.participants.models import RegistrationForm
        form = RegistrationForm.objects.get(name="SYNTHETIC Fresh")
        keys = {q["key"] for q in form.versions.get().snapshot["questions"]}
        self.assertNotIn("prior_employment", keys)
