"""Online form exports: one Name column, long question headers, "Maybe" answers, repeated sign-ups."""
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.participants.historical import _read_rows, create_batch, guess_mapping, preview_batch

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
        self.assertIn("Import 2 ready rows", page)
        self.assertIn("Needs a look", page)
        review = self.client.get(reverse("participants:historical-batch", args=[self.batch.pk]) + "?show=review").content.decode()
        self.assertIn("Joseph Brown", review)
        self.assertNotIn("Mary Ann Smith", review)
