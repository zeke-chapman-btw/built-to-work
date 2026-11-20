"""Importing a past-event export: fixed values, combined Yes/No columns, US dates and year ranges."""
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.participants.historical import (columns_spec, create_batch, fixed_spec, guess_mapping, import_ready,
                                          preview_batch)
from apps.participants.models import HistoricalEventRecord, HistoricalSimulatorScore, Participant

HEADER = ("DateCaptured,EventName,FName,LName,Phone,Email,Address,Address2,City,State,Zip,DOB,JobStatus,"
          "InterestedInJobOpportunities,TopIndustryHeavyEquipmentOperator,TopIndustryWelder,TopIndustryConcreteWorkerFinisher,"
          "TopIndustryOther,TopIndustryOtherFreeText,YearsConstructionExperience,TradeCertificationsCDL,TradeCertificationsEPA608,"
          "EXCTruckLoadingTotalScore,TriviaTotalScore,DuckHunterScore")
ROWS = [
    "11/14/2026 14:00:45,SAMPLE Skills Challenge,SYNTHETIC,Alpha,7065550101,alpha@example.test,1 Test St,,Testville,GA,30901,"
    "3/4/2008,Student,Yes,Yes,No,Yes,Yes,Drone pilot,1-3 years,No,Yes,45.83,12,800",
    "11/14/2026 14:05:00,SAMPLE Skills Challenge,SYNTHETIC,Bravo,7065550102,bravo@example.test,2 Test St,Apt 4,Testville,GA,30901,"
    "12/21/1999,Unemployed and looking for work,Maybe,No,Yes,No,No,,Less than 1 year,,,,,",
]


@override_settings()
class EventExportImportTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        setting = override_settings(PRIVATE_HISTORICAL_ROOT=self.temp.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.actor = get_user_model().objects.create_superuser(username="hx-admin", password="x-test-only")

    def batch(self):
        text = HEADER + "\n" + "\n".join(ROWS) + "\n"
        return create_batch(uploaded_file=SimpleUploadedFile("export.csv", text.encode(), content_type="text/csv"),
                            actor=self.actor)

    def mapping(self):
        mapping = guess_mapping(HEADER.split(","))
        mapping.update({
            "participant_type": "JobStatus",
            "found_us": fixed_spec("Event: SAMPLE Skills Challenge"),
            "trades_wanted": columns_spec(["TopIndustryHeavyEquipmentOperator", "TopIndustryWelder",
                                           "TopIndustryConcreteWorkerFinisher", "TopIndustryOther", "TopIndustryOtherFreeText"]),
            "skills": columns_spec(["TradeCertificationsCDL", "TradeCertificationsEPA608"]),
            "staff_note": columns_spec(["TriviaTotalScore", "DuckHunterScore"]),
            "simulator_score": "EXCTruckLoadingTotalScore",
            "score_method": fixed_spec("Excavator truck loading"),
            "score_source": fixed_spec("Past event export"),
        })
        return mapping

    def test_guess_matches_the_export_columns(self):
        guess = guess_mapping(HEADER.split(","))
        for field, column in {"first_name": "FName", "last_name": "LName", "date_of_birth": "DOB",
                              "address_line_1": "Address", "address_line_2": "Address2", "postal_code": "Zip",
                              "employment_status": "JobStatus", "looking_for_work": "InterestedInJobOpportunities",
                              "years_experience": "YearsConstructionExperience", "event_name": "EventName",
                              "event_date": "DateCaptured"}.items():
            self.assertEqual(guess.get(field), column, field)

    def test_full_export_imports(self):
        batch = self.batch()
        rows = list(preview_batch(batch, self.mapping(), actor=self.actor))
        self.assertEqual([r.status for r in rows], ["ready", "ready"], [r.error for r in rows])
        self.assertEqual(import_ready(batch, actor=self.actor), 2)
        alpha = Participant.objects.get(last_name="Alpha")
        self.assertEqual(str(alpha.date_of_birth), "2008-03-04")
        self.assertEqual((alpha.address_line_1, alpha.primary_identifier), ("1 Test St", "student"))
        career = alpha.career_profile
        self.assertEqual(career.career_interests, "Heavy Equipment Operator, Concrete Worker Finisher, Other, Other: Drone pilot")
        self.assertEqual(career.skills_interests, "EPA 608")
        self.assertEqual((career.employment_interest, career.employment_status, str(career.years_of_experience)),
                         ("seeking", "Student", "1.0"))
        from apps.participants.models import ParticipantNote
        self.assertEqual(ParticipantNote.objects.get(participant=alpha).content, "Trivia Total Score: 12\nDuck Hunter Score: 800")
        record = HistoricalEventRecord.objects.get(participant=alpha)
        self.assertEqual((record.event_name, str(record.event_date)), ("SAMPLE Skills Challenge", "2026-11-14"))
        self.assertEqual(float(HistoricalSimulatorScore.objects.get(participant=alpha, kind="simulator").raw_score), 45.83)
        bravo = Participant.objects.get(last_name="Bravo")
        self.assertEqual((bravo.primary_identifier, bravo.address_line_2), ("workforce", "Apt 4"))
        self.assertEqual(str(bravo.career_profile.years_of_experience), "0.0")
        self.assertFalse(HistoricalSimulatorScore.objects.filter(participant=bravo).exists())

    def test_mapping_page_saves_fixed_values_and_combined_columns(self):
        batch = self.batch()
        self.client.force_login(self.actor, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("participants:historical-map", args=[batch.pk])
        page = self.client.get(url)
        self.assertContains(page, "Same value for every row")
        response = self.client.post(url, {"map_first_name": "FName", "map_last_name": "LName",
            "map_found_us": "__fixed__", "fixed_found_us": "Event: SAMPLE",
            "map_trades_wanted": "__cols__", "cols_trades_wanted": ["TopIndustryWelder", "TopIndustryHeavyEquipmentOperator"],
            "template_name": "Event export"})
        self.assertIn(response.status_code, (200, 302))
        batch.refresh_from_db()
        self.assertEqual(batch.mapping["found_us"], fixed_spec("Event: SAMPLE"))
        self.assertEqual(batch.mapping["trades_wanted"], columns_spec(["TopIndustryWelder", "TopIndustryHeavyEquipmentOperator"]))
        again = self.client.get(url)
        self.assertContains(again, 'value="Event: SAMPLE"')


class ConsentImportTests(EventExportImportTests):
    HEAD = "DateCaptured,FName,LName,Email,ConsentToTerms"
    ROWS = ["11/14/2026 14:00:45,SYNTHETIC,Charlie,charlie@example.test,Yes",
            "11/14/2026 14:02:00,SYNTHETIC,Delta,delta@example.test,No"]

    def consent_batch(self):
        text = self.HEAD + "\n" + "\n".join(self.ROWS) + "\n"
        return create_batch(uploaded_file=SimpleUploadedFile("consent.csv", text.encode(), content_type="text/csv"),
                            actor=self.actor)

    def setup_consent(self):
        import hashlib
        from apps.participants.intake_lifecycle import create_form, publish, revision
        from apps.participants.models import ConsentDocumentVersion
        body = "SYNTHETIC test-only consent"
        self.document = ConsentDocumentVersion.objects.create(key="btw-intake", version="2026-1", title="Sign-up consent",
            body=body, content_hash=hashlib.sha256(body.encode()).hexdigest(), is_approved=True)
        draft = create_form(actor=self.actor, name="SYNTHETIC sign-up")
        publish(actor=self.actor, version_id=draft.pk, expected_revision=revision(draft))

    def test_guess_and_record_consent_at_spreadsheet_time(self):
        from apps.participants.models import ConsentAcceptance
        self.setup_consent()
        guess = guess_mapping(self.HEAD.split(","))
        self.assertEqual((guess["consent_agreed"], guess["consent_at"]), ("ConsentToTerms", "DateCaptured"))
        batch = self.consent_batch()
        rows = list(preview_batch(batch, guess, actor=self.actor))
        self.assertEqual([r.status for r in rows], ["ready", "ready"], [r.error for r in rows])
        self.assertEqual(import_ready(batch, actor=self.actor), 2)
        acceptance = ConsentAcceptance.objects.get()
        self.assertEqual(acceptance.participant.last_name, "Charlie")
        self.assertEqual(acceptance.document, self.document)
        from django.utils import timezone
        self.assertEqual(timezone.localtime(acceptance.accepted_at).strftime("%Y-%m-%d %H:%M:%S"), "2026-11-14 14:00:45")
        self.assertEqual(acceptance.submission.source, "historical_import")
        self.assertTrue(acceptance.metadata["historical_import"])

    def test_consent_without_stored_document_is_flagged(self):
        batch = self.consent_batch()
        rows = list(preview_batch(batch, guess_mapping(self.HEAD.split(",")), actor=self.actor))
        self.assertEqual(rows[0].status, "error")
        self.assertIn("no approved sign-up consent", rows[0].error)
        self.assertEqual(rows[1].status, "ready")
