"""Culture Index: one survey link sent from the profile, shown in the portal, results copied in nightly by email."""
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.participants.models import Participant

from . import cultureindex, store
from .models import CultureIndexResult, MessageTemplate
from .templates_views import fill

ET = ZoneInfo("America/New_York")
SURVEY = {"surveyId": "S-1", "firstName": "Pat", "lastName": "Lee", "email": "PAT@example.test",
          "surveyDate": "2026-11-17T15:00:00Z", "traitPattern": "Craftsman", "surveyReportLink": "https://example.test/r",
          "traitPatternDotsLink": "https://example.test/d",
          "appliedToPositionsMatches": [{"positionName": "Welder", "matchPercent": 71}, {"positionName": "Electrician", "matchPercent": 88}]}


class CultureIndexTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="ci-admin", password="x-test-only")
        self.person = Participant.objects.create(first_name="SYNTHETIC", last_name="Lee", contact_email="pat@example.test",
                                                 contact_phone="7065550111")

    def test_link_fills_into_the_seeded_template(self):
        template = MessageTemplate.objects.get(name="Culture Index survey")
        self.assertIn(cultureindex.DEFAULT_LINK, fill(template.body, self.person, self.admin))
        store.save(key="cultureindex", values={"survey_link": "https://example.test/new"}, actor=self.admin)
        self.assertIn("https://example.test/new", fill("{culture_index_link}"))

    def test_results_are_matched_by_email_and_kept_once(self):
        outcome = cultureindex.save_results([SURVEY, {"surveyId": "S-2", "email": "nobody@example.test"}, "junk"])
        self.assertEqual(outcome, {"added": 2, "matched": 1, "seen": 3})
        result = CultureIndexResult.objects.get(survey_id="S-1")
        self.assertEqual(result.participant, self.person)
        self.assertEqual([m["name"] for m in result.job_matches], ["Electrician", "Welder"])
        cultureindex.save_results([dict(SURVEY, traitPattern="Specialist")])
        self.assertEqual(CultureIndexResult.objects.filter(survey_id="S-1").get().pattern, "Specialist")

    def test_nightly_only_with_a_key_and_once_a_night(self):
        night = datetime(2026, 11, 18, 2, 30, tzinfo=ET)
        self.assertFalse(cultureindex.due(night))
        store.save(key="cultureindex", values={}, actor=self.admin, new_secret="key-test-only")
        self.assertFalse(cultureindex.due(datetime(2026, 11, 18, 1, 0, tzinfo=ET)))
        self.assertTrue(cultureindex.due(night))
        with patch.object(cultureindex, "call", return_value={"value": [SURVEY]}), \
                patch("django.utils.timezone.now", return_value=night):
            self.assertEqual(cultureindex.run_nightly(night)["matched"], 1)
        self.assertFalse(cultureindex.due(datetime(2026, 11, 18, 14, 0, tzinfo=ET)))
        self.assertTrue(cultureindex.due(datetime(2026, 11, 19, 3, 0, tzinfo=ET)))

    def test_profile_card_offers_the_link_and_shows_results(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("participants:staff-profile", args=[self.person.pk])
        page = self.client.get(url).content.decode()
        self.assertIn("Email survey link", page)
        self.assertIn("Text survey link", page)
        cultureindex.save_results([SURVEY])
        page = self.client.get(url).content.decode()
        self.assertIn("Craftsman", page)
        self.assertIn("Electrician · 88%", page)

    def test_connections_tiles_and_setup(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        page = self.client.get(reverse("messaging:connections")).content.decode()
        for name in ("EZ Texting", "Mailchimp", "Culture Index", "Stripe", "Staff Gmail", "Link ready · no API key"):
            self.assertIn(name, page)
        with patch.object(cultureindex, "call", return_value=[]):
            response = self.client.post(reverse("messaging:connections-culture-index"),
                                        {"survey_link": cultureindex.DEFAULT_LINK, "portal": "on", "api_key": "key-test-only"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(cultureindex.api_key(), "key-test-only")
        self.assertNotIn("key-test-only", self.client.get(reverse("messaging:connections")).content.decode())
        self.client.post(reverse("messaging:connections-culture-index"), {"survey_link": cultureindex.DEFAULT_LINK})
        self.assertFalse(cultureindex.in_portal())
        self.assertEqual(cultureindex.api_key(), "key-test-only")  # blank keeps the saved key

    def test_key_is_kept_when_the_check_cannot_reach_culture_index(self):
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("messaging:connections-culture-index")
        with patch("apps.messaging.cultureindex.request.urlopen", side_effect=ConnectionResetError()):
            response = self.client.post(url, {"survey_link": cultureindex.DEFAULT_LINK, "api_key": "key-test-only"}, follow=True)
        self.assertEqual(cultureindex.api_key(), "key-test-only")
        self.assertContains(response, "the check didn")
        with patch.object(cultureindex, "call", side_effect=cultureindex.CultureIndexError("refused (401: Access denied)", refused=True)):
            response = self.client.post(url, {"survey_link": cultureindex.DEFAULT_LINK, "api_key": "other-test-only"}, follow=True)
        self.assertEqual(cultureindex.api_key(), "other-test-only")  # kept, so it can be tried again
        self.assertContains(response, "Access denied")
        self.assertContains(self.client.get(reverse("messaging:connections")), "Key not working")


class PagingTests(TestCase):
    def test_every_page_is_copied(self):
        admin = get_user_model().objects.create_superuser(username="ci-pages", password="x-test-only")
        store.save(key="cultureindex", values={}, actor=admin, new_secret="key-test-only")
        pages = {0: [{"surveyId": n, "email": f"p{n}@example.test"} for n in range(1, 51)],
                 50: [{"surveyId": n, "email": f"p{n}@example.test"} for n in range(51, 71)], 70: []}
        with patch.object(cultureindex, "call", side_effect=lambda path, key=None, query=None: pages[query["Skip"]]):
            outcome = cultureindex.sync()
        self.assertEqual(outcome["seen"], 70)
        self.assertEqual(CultureIndexResult.objects.count(), 70)

    def test_an_api_that_ignores_paging_stops_after_one_repeat(self):
        admin = get_user_model().objects.create_superuser(username="ci-pages2", password="x-test-only")
        store.save(key="cultureindex", values={}, actor=admin, new_secret="key-test-only")
        same = [{"surveyId": n} for n in range(1, 6)]
        with patch.object(cultureindex, "call", return_value=same) as call:
            self.assertEqual(cultureindex.sync()["seen"], 5)
        self.assertEqual(call.call_count, 2)
