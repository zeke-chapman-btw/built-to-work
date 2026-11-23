"""A form's Results page: counts, how far people get, typical time, the list and a full download."""
import csv
import io
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .intake_models import IntakeSession
from .models import RegistrationSubmission
from . import test_intake_ui_11b as base


class FormResultsTests(TestCase):
    def setUp(self):
        base.IntakeUI11BTests.setUp(self)
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")

    def test_results_count_funnel_and_download(self):
        now = timezone.now()
        form = self.version.form
        submission = RegistrationSubmission.objects.create(form_version=self.version, status="submitted", submitted_at=now,
            answers={"first_name": "SYNTHETIC", "last_name": "=cmd", "travel_willingness": "yes"},
            answer_snapshot={"contract": self.version.snapshot}, intake_metadata={"channel": "online"}, source="smart_intake")
        fields = dict(form_version=self.version, contract_hash="x", consent_document=self.document, consent_hash="x", channel="online", browser_key="b")
        done = IntakeSession.objects.create(current_step=4, state="submitted", submission=submission, **fields)
        IntakeSession.objects.filter(pk=done.pk).update(created_at=now - timedelta(minutes=6))
        IntakeSession.objects.create(current_step=1, **fields)
        IntakeSession.objects.create(current_step=2, **fields)
        page = self.client.get(reverse("participants:intake-config-results", args=[form.pk])).content.decode()
        self.assertIn("Download all (1)", page)
        self.assertIn("3 started", page)
        self.assertIn("1 · 33%", page)  # finished
        self.assertIn("6 min", page)
        response = self.client.get(reverse("participants:intake-config-results", args=[form.pk]) + "?download=1")
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        header, row = rows
        self.assertEqual(row[header.index("Where")], "Website")
        travel = next(q for q in self.version.snapshot["questions"] if q["key"] == "travel_willingness")
        self.assertEqual(row[header.index(travel["label"])], next(o["label"] for o in travel["options"] if o["value"] == "yes"))
        self.assertIn("'=cmd", ";".join(row))  # never runs as a formula in Excel
        self.assertContains(self.client.get(reverse("participants:intake-config-detail", args=[form.pk])), "Results · 1")
        RegistrationSubmission.objects.create(form_version=self.version, status="submitted", submitted_at=now, source="historical_import",
                                              answers={}, answer_snapshot={})
        self.assertIn("Download all (1)", self.client.get(reverse("participants:intake-config-results", args=[form.pk])).content.decode())
        self.assertIn("Download all (1)", self.client.get(reverse("participants:intake-config-results", args=[form.pk]) + "?source=imported").content.decode())
