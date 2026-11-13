"""Profile card cleanup, column-matched participant import, Home quick actions and the generic touchpoint start."""
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.crm.models import Interaction
from apps.internal_ui.dashboard import quick_actions_for
from apps.participants.historical import create_batch, guess_mapping, import_ready, preview_batch
from apps.participants.models import ParticipantCareerProfile, Participant
from apps.participants.workspace_models import ParticipantAcquisition
from apps.recruiting.test_employer import EmployerFixtures


class ProfileCleanupTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")

    def test_work_preferences_drop_locations_and_availability_and_use_clear_labels(self):
        page = self.client.get(reverse("participants:staff-profile", args=[self.person.pk]))
        self.assertContains(page, "Looking for work")
        self.assertContains(page, "Work desired")
        self.assertContains(page, "Participant type")
        self.assertNotContains(page, "<dt>Locations</dt>")
        self.assertNotContains(page, "<dt>Availability</dt>")
        editor = self.client.get(reverse("participants:profile-card-edit", args=[self.person.pk, "preferences"]), HTTP_X_BTW_EDITOR="1")
        self.assertNotContains(editor, 'name="preferred_locations"')
        self.assertNotContains(editor, 'name="availability_classification"')
        contact = self.client.get(reverse("participants:profile-card-edit", args=[self.person.pk, "identity"]), HTTP_X_BTW_EDITOR="1")
        self.assertNotContains(contact, 'name="primary_identifier"')

    def test_details_editor_saves_participant_type_with_how_they_found_us(self):
        url = reverse("participants:acquisition-edit", args=[self.person.pk])
        page = self.client.get(url, HTTP_X_BTW_EDITOR="1")
        form = page.context["form"]
        self.assertIn("participant_type", form.fields)
        data = {"participant_type": "student", "type_revision": form.initial["type_revision"], "channel": "online",
                "online_source": "Facebook", "revision": form.initial["revision"]}
        response = self.client.post(url, data, HTTP_X_BTW_EDITOR="1")
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.person.refresh_from_db()
        self.assertEqual(self.person.primary_identifier, "student")
        self.assertEqual(ParticipantAcquisition.objects.get(participant=self.person).online_source, "Facebook")

    def test_staff_profile_editor_keeps_preferred_locations(self):
        career, _ = ParticipantCareerProfile.objects.get_or_create(participant=self.person)
        career.preferred_locations = "Augusta"
        career.save()
        from apps.participants.workspace_forms import CareerProfileForm
        self.assertNotIn("preferred_locations", CareerProfileForm().fields)

    def test_home_quick_actions_are_short_and_include_touchpoint_and_customer(self):
        actions = {a["id"]: a for a in quick_actions_for(self.admin)}
        self.assertEqual(actions["create-task"]["label"], "+ Task")
        self.assertEqual(actions["add-customer"]["url"], reverse("customers:new"))
        self.assertEqual(actions["log-touchpoint"]["url"], reverse("crm:start"))
        page = self.client.get(reverse("internal:home"))
        self.assertContains(page, "+ Touchpoint")
        self.assertContains(page, "+ Customer")

    def test_touchpoint_start_finds_participant_then_opens_their_form(self):
        url = reverse("crm:start")
        self.assertContains(self.client.get(url, HTTP_X_BTW_EDITOR="1"), "Log a touchpoint")
        response = self.client.post(url, {"about": "participant", "find": self.person.contact_email}, HTTP_X_BTW_EDITOR="1")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["next"].startswith(reverse("crm:participant-new", args=[self.person.pk])))
        response = self.client.post(url, {"about": "customer", "find": self.organization.name[:6]}, HTTP_X_BTW_EDITOR="1")
        if response["Content-Type"].startswith("application/json"):
            self.assertIn(str(self.organization.pk), response.json()["next"])
        else:
            self.assertContains(response, self.organization.name)
        self.assertEqual(Interaction.objects.count(), 0)

    def test_customer_overview_lists_touchpoints_and_contact_edit(self):
        from apps.crm import services
        from django.utils import timezone
        import uuid
        services.create_internal(actor=self.admin, organization_id=self.organization.pk, operation_id=uuid.uuid4(),
            data={"activity_type": "call", "summary": "SYNTHETIC called the plant manager", "occurred_at": timezone.now()})
        page = self.client.get(reverse("customers:workspace", args=[self.organization.pk, "overview"]))
        self.assertContains(page, "SYNTHETIC called the plant manager")
        contact = self.organization.contacts.filter(is_active=True).first()
        if contact:
            self.assertContains(page, reverse("customers:child-edit", args=[self.organization.pk, "contact", contact.pk]))


class ColumnMatchedImportTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        setting = override_settings(PRIVATE_HISTORICAL_ROOT=self.temp.name)
        setting.enable()
        self.addCleanup(setting.disable)
        from django.contrib.auth import get_user_model
        self.actor = get_user_model().objects.create_superuser(username="import-admin", email="i@example.test", password="x")

    def test_columns_are_guessed_and_profile_answers_imported(self):
        text = ("First,Surname,E-mail,Type,Lead Source,Seeking work,Full or part time,Trade interests,Travel,Notes\n"
                "Pat,Example,pat@example.test,Job seeker,Facebook,Yes,Part time,Welding,no,Met at fair\n")
        batch = create_batch(uploaded_file=SimpleUploadedFile("people.csv", text.encode()), actor=self.actor)
        mapping = guess_mapping(text.splitlines()[0].split(","))
        self.assertEqual(mapping["last_name"], "Surname")
        self.assertEqual(mapping["found_us"], "Lead Source")
        self.assertEqual(mapping["work_desired"], "Full or part time")
        preview_batch(batch, mapping, actor=self.actor)
        self.assertEqual(import_ready(batch, actor=self.actor), 1)
        person = Participant.objects.get(contact_email="pat@example.test")
        self.assertEqual(person.primary_identifier, "job_seeker")
        self.assertEqual(person.career_profile.employment_interest, "seeking")
        self.assertEqual(person.career_profile.preferred_work_type, "part_time")
        self.assertIs(person.career_profile.willing_to_travel, False)
        self.assertEqual(person.acquisition.online_source, "Facebook")
        self.assertEqual(person.staff_notes.count(), 1)

    def test_unknown_choice_is_a_row_error(self):
        text = "First,Last,Type\nPat,Example,Astronaut\n"
        batch = create_batch(uploaded_file=SimpleUploadedFile("people.csv", text.encode()), actor=self.actor)
        rows = list(preview_batch(batch, {"first_name": "First", "last_name": "Last", "participant_type": "Type"}, actor=self.actor))
        self.assertEqual(rows[0].status, "error")
        self.assertIn("Participant type", rows[0].error)
