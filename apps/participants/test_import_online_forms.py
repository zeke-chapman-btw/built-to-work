"""Online form exports: one Name column, long question headers, "Maybe" answers, repeated sign-ups."""
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.participants.historical import _read_rows, create_batch, guess_mapping, import_ready, preview_batch

CSV = (
    "Entry ID,Name,Email,Phone,Address,City,State,Current Job Status,"
    "What industry do you work in/ would like to work in?,Work History,Skills & Experience ,"
    "Licenses/ Certifications,Education,Are you willing to travel for work?,Where did you hear about us?,Opt In\n"
    "1,Mary Ann Smith,mary@example.test,7065550101,1 Main St,Athens,GA,Unemployed and looking for work,"
    "Construction,N/A,Welding,OSHA 10,High school,Maybe,Social Media,\"Yes, I opt In\"\n"
    "2,Joe Brown,joe@example.test,7065550102,2 Main St,Macon,GA,Retired,Manufacturing,"
    + "x" * 5000 + ",Other,Other,Some college,Yes,Other,No\n"
    "3,Joseph Brown,joe@example.test,7065550199,2 Main St,Macon,GA,Retired,Manufacturing,,,,,No,,No\n"
)


class OnlineFormImportTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        setting = override_settings(PRIVATE_HISTORICAL_ROOT=temp.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.actor = get_user_model().objects.create_superuser(username="forms-admin", email="f@example.test", password="pw")
        self.batch = create_batch(uploaded_file=SimpleUploadedFile("forms.csv", CSV.encode()), actor=self.actor)

    def test_columns_are_matched_and_rows_read(self):
        headers, _rows = _read_rows(self.batch)
        mapping = guess_mapping(headers)
        self.assertEqual(mapping["full_name"], "Name")
        self.assertNotIn("first_name", mapping)
        self.assertEqual(mapping["found_us"], "Where did you hear about us?")
        self.assertEqual(mapping["willing_to_travel"], "Are you willing to travel for work?")
        self.assertEqual(mapping["staff_note"], "Work History")
        self.assertIn("Licenses/ Certifications", mapping["education"])
        rows = {r.row_number: r for r in preview_batch(self.batch, mapping, actor=self.actor)}
        mary = rows[2].normalized_data
        self.assertEqual((mary["first_name"], mary["last_name"]), ("Mary Ann", "Smith"))
        self.assertNotIn("willing_to_travel", mary)  # "Maybe" stays unrecorded
        self.assertEqual(mary["staff_note"], "")  # "N/A" says nothing
        self.assertIn("OSHA 10", mary["education"])
        joe = rows[3].normalized_data
        self.assertEqual(rows[3].status, "ready")
        self.assertLessEqual(len(joe["staff_note"]), 4000)  # long work history is cut to fit, not refused
        self.assertEqual((joe["skills"], joe["found_us"]), ("", ""))
        self.assertEqual(rows[4].status, "review")  # same email filled in twice
        self.assertEqual(rows[4].error, "Possible duplicate in this file.")

    def test_preview_page_lists_names_and_counts(self):
        headers, _rows = _read_rows(self.batch)
        preview_batch(self.batch, guess_mapping(headers), actor=self.actor)
        self.client.force_login(self.actor, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("participants:historical-batch", args=[self.batch.pk]))
        self.assertEqual(response.status_code, 200, response.get("Location"))
        page = response.content.decode()
        self.assertIn("Mary Ann Smith", page)
        self.assertIn("of the 2 ready rows", page)
        self.assertIn("Needs a look", page)
        review = self.client.get(reverse("participants:historical-batch", args=[self.batch.pk]) + "?show=review").content.decode()
        self.assertIn("Joseph Brown", review)
        self.assertNotIn("Mary Ann Smith", review)

    def test_type_is_chosen_per_upload_and_a_few_can_go_in_first(self):
        headers, _rows = _read_rows(self.batch)
        self.client.force_login(self.actor, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("participants:historical-map", args=[self.batch.pk])
        page = self.client.get(url).content.decode()
        self.assertIn("Participant type for this upload", page)
        post = {"map_full_name": "Name", "map_email": "Email", "map_phone": "Phone", "type_choice": "workforce"}
        self.assertEqual(self.client.post(url, post).status_code, 302)
        self.batch.refresh_from_db()
        rows = {r.row_number: r for r in self.batch.rows.all()}
        self.assertEqual({r.normalized_data.get("participant_type") for r in rows.values() if r.status == "ready"}, {"workforce"})
        self.client.post(url, dict(post, type_choice=""))
        self.assertFalse(any(r.normalized_data.get("participant_type") for r in self.batch.rows.all()))  # left blank: no guess
        self.client.post(url, post)
        self.assertEqual(import_ready(self.batch, actor=self.actor, limit=1), 1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, "previewed")  # the rest wait for later
        self.assertEqual(self.batch.rows.get(row_number=2).participant.primary_identifier, "workforce")
        self.assertEqual(self.batch.rows.get(row_number=3).status, "ready")
        self.assertEqual(import_ready(self.batch, actor=self.actor), 1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, "imported")


class AddressQuestionTests(TestCase):
    def test_street_address_question_can_be_added_without_a_length(self):
        from apps.participants.intake_contracts import validate_contract
        from apps.participants.intake_defaults import recommended_contract
        contract = recommended_contract()
        contract["questions"].insert(6, {"key": "street_address", "label": "Street address", "type": "short_text", "step": 1,
                                         "required": False, "destination": "street_address"})
        validate_contract(contract)  # used to fail: "Text length exceeds the approved destination limit"


class ShortRowTests(TestCase):
    def test_a_row_that_stops_early_is_read_as_blank_at_the_end(self):
        import tempfile
        from django.test import override_settings
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        setting = override_settings(PRIVATE_HISTORICAL_ROOT=temp.name); setting.enable(); self.addCleanup(setting.disable)
        actor = get_user_model().objects.create_superuser("short-row-admin", "s@example.test", "pw")
        batch = create_batch(uploaded_file=SimpleUploadedFile("s.csv", b"Name,Email,Phone,Notes\nPat Lee,pat@example.test\nSam Roe,sam@example.test,7065550100,hi,extra\n"), actor=actor)
        headers, rows = _read_rows(batch)
        first, second = rows[0][1], rows[1][1]
        self.assertEqual(first["Phone"], "")
        self.assertNotIn("__row_error__", first)
        self.assertIn("__row_error__", second)  # more values than columns is still a problem


class UploadEventPickerTests(TestCase):
    def test_one_event_for_the_whole_file(self):
        import tempfile
        from datetime import timedelta
        from django.test import override_settings
        from django.utils import timezone
        from apps.events.models import Event
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        setting = override_settings(PRIVATE_HISTORICAL_ROOT=temp.name); setting.enable(); self.addCleanup(setting.disable)
        actor = get_user_model().objects.create_superuser("pick-admin", "k@example.test", "x-test-only")
        start = timezone.now() - timedelta(days=200)
        event = Event.objects.create(name="SYNTHETIC Talladega", status="completed", start_at=start, end_at=start + timedelta(hours=8))
        batch = create_batch(uploaded_file=SimpleUploadedFile("t.csv", b"Name,Email\nPat Lee,pat@example.test\n"), actor=actor)
        self.client.force_login(actor, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("participants:historical-map", args=[batch.pk])
        page = self.client.get(url).content.decode()
        self.assertIn(f"SYNTHETIC Talladega · {event.year}", page)
        self.client.post(url, {"map_full_name": "Name", "map_email": "Email", "upload_event": str(event.pk)})
        row = batch.rows.get()
        self.assertEqual(row.normalized_data.get("event_uuid"), str(event.pk))
