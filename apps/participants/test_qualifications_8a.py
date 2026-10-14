"""Build 8A participant qualification foundation regression tests."""
import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.participants.models import (
    Participant, RegistrationForm, RegistrationFormVersion, RegistrationQuestion,
    RegistrationSubmission, QualificationCategory, QualificationCatalogEntry,
    ParticipantQualification, RegistrationQualificationMapping,
)
from apps.participants.qualification_services import process_registration_submission


class QualificationFoundationTests(TestCase):
    def setUp(self):
        self.participant = Participant.objects.create(first_name="Taylor", last_name="Worker")
        self.form = RegistrationForm.objects.create(slug="qualification-test", name="Qualification intake")
        self.version = RegistrationFormVersion.objects.create(
            form=self.form, version=1, status="published", snapshot={"source": "test"},
            published_at=timezone.now(),
        )
        self.question = RegistrationQuestion.objects.create(
            form=self.form, canonical_key="trade_interest", label="Which trade?",
            field_type="single_choice", options=["Electrical", "Welding"], is_active=True,
        )
        self.category = QualificationCategory.objects.create(name="Trades", slug="trades")
        self.catalog = QualificationCatalogEntry.objects.create(
            name="Electrical", kind="skill", category=self.category, description="Electrical work",
        )

    def mapping(self, *, question=None, mapping_type="choice", value="Electrical", entry=None):
        question = question or self.question
        return RegistrationQualificationMapping.objects.create(
            form=self.form, registration_question=question,
            question_key=question.canonical_key, mapping_type=mapping_type,
            answer_value=value, catalog_entry=entry if entry is not None else (self.catalog if mapping_type == "choice" else None),
            is_active=True, is_published=True,
        )

    def submission(self, answers, *, participant=None, status="submitted"):
        return RegistrationSubmission.objects.create(
            form_version=self.version, participant=participant or self.participant,
            status=status, answers=answers, answer_snapshot={"answers": answers, "version": 1},
            submitted_at=timezone.now(), age_classification="", idempotency_key=str(uuid.uuid4()),
        )

    def test_published_choice_mapping_is_created_on_shared_submission_path_and_is_repeat_safe(self):
        self.mapping()
        submission = self.submission({"trade_interest": "Electrical"}, participant=self.participant)
        self.assertEqual(submission.event_id, None)
        self.assertEqual(ParticipantQualification.objects.filter(participant=self.participant).count(), 1)
        qualification = ParticipantQualification.objects.get(participant=self.participant)
        self.assertEqual(qualification.catalog_entry, self.catalog)
        self.assertTrue(qualification.is_self_reported)
        self.assertEqual(qualification.source, "registration")
        self.assertEqual(qualification.source_answer_snapshot["answer"], "Electrical")
        self.assertEqual(qualification.mapping_snapshot["catalog_stable_id"], str(self.catalog.stable_id))
        self.assertEqual(process_registration_submission(submission), [])
        self.assertEqual(ParticipantQualification.objects.filter(source_submission=submission).count(), 1)

    def test_inactive_or_unpublished_mapping_does_not_populate_qualification(self):
        mapping = self.mapping()
        mapping.is_published = False
        mapping.save(update_fields=["is_published", "updated_at"])
        submission = self.submission({"trade_interest": "Electrical"})
        self.assertFalse(ParticipantQualification.objects.filter(source_submission=submission).exists())

    def test_unmapped_free_text_is_never_inferred_but_explicit_custom_mapping_is_supported(self):
        text_question = RegistrationQuestion.objects.create(
            form=self.form, canonical_key="custom_training", label="Other training",
            field_type="short_text", options=[], is_active=True,
        )
        unmapped = self.submission({"trade_interest": "unrelated text", "custom_training": "Forklift"})
        self.assertFalse(ParticipantQualification.objects.filter(source_submission=unmapped).exists())
        self.mapping(question=text_question, mapping_type="custom_text", value="")
        mapped = self.submission({"custom_training": "  Forklift  "})
        qualification = ParticipantQualification.objects.get(source_submission=mapped)
        self.assertEqual(qualification.custom_name, "Forklift")
        self.assertIsNone(qualification.catalog_entry_id)
        self.assertTrue(qualification.is_self_reported)

    def test_mapping_changes_do_not_overwrite_original_answer_or_mapping_snapshot(self):
        mapping = self.mapping()
        submission = self.submission({"trade_interest": "Electrical"})
        qualification = ParticipantQualification.objects.get(source_submission=submission)
        original_snapshot = qualification.mapping_snapshot.copy()
        replacement = QualificationCatalogEntry.objects.create(
            identifier="welding", name="Welding", kind="skill", category=self.category,
        )
        mapping.catalog_entry = replacement
        mapping.save(update_fields=["catalog_entry", "updated_at"])
        process_registration_submission(submission)
        qualification.refresh_from_db()
        self.assertEqual(qualification.catalog_entry, self.catalog)
        self.assertEqual(qualification.mapping_snapshot, original_snapshot)
        self.assertEqual(submission.answers["trade_interest"], "Electrical")

    def test_catalog_deactivation_preserves_historical_participant_association(self):
        self.mapping()
        submission = self.submission({"trade_interest": "Electrical"})
        self.catalog.is_active = False
        self.catalog.save(update_fields=["is_active", "updated_at"])
        qualification = ParticipantQualification.objects.get(source_submission=submission)
        self.assertEqual(qualification.catalog_entry_id, self.catalog.pk)
        self.assertFalse(qualification.catalog_entry.is_active)

    def test_staff_configuration_requires_explicit_backend_permission_for_get_and_post(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="regular-staff", password="test"); user.is_staff=True; user.is_active=True; user.save()
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("admin:participants_qualificationcatalogentry_add")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_authorized_staff_can_manage_records_but_configuration_is_separate(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="qualification-staff", password="test"); user.is_staff=True; user.is_active=True; user.save()
        permission = Permission.objects.get(content_type__app_label="participants", codename="manage_participant_qualifications")
        user.user_permissions.add(permission)
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        record_url = reverse("admin:participants_participantqualification_add")
        config_url = reverse("admin:participants_qualificationcatalogentry_add")
        self.assertEqual(self.client.get(record_url).status_code, 200)
        self.assertEqual(self.client.get(config_url).status_code, 403)

    def test_admin_configuration_permission_is_distinct_and_authorizes_catalog(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="catalog-admin", password="test"); user.is_staff=True; user.is_active=True; user.save()
        permission = Permission.objects.get(content_type__app_label="participants", codename="manage_qualification_catalog")
        user.user_permissions.add(permission)
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("admin:participants_qualificationcatalogentry_add")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:participants_registrationqualificationmapping_add")).status_code, 403)

    def test_admin_record_update_is_audited(self):
        self.mapping()
        from django.contrib import admin
        from django.test import RequestFactory
        from django.contrib.auth import get_user_model
        from apps.core.models import AuditLog
        from apps.participants.qualification_admin import ParticipantQualificationAdmin

        submission = self.submission({'trade_interest': 'Electrical'})
        qualification = ParticipantQualification.objects.get(source_submission=submission)
        user = get_user_model().objects.create_user(
            username='qualification-auditor', password='test', is_staff=True, is_active=True
        )
        request = RequestFactory().post('/admin/')
        request.user = user
        qualification.notes = 'Reviewed with participant'
        ParticipantQualificationAdmin(ParticipantQualification, admin.site).save_model(
            request, qualification, form=None, change=True
        )
        self.assertTrue(
            AuditLog.objects.filter(
                object_id=str(qualification.pk), source='admin', action='qualification.participant_record.update'
            ).exists()
        )



    def test_staff_interface_searches_adds_and_updates_with_audit(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        from django.urls import reverse
        from apps.core.models import AuditLog
        from apps.participants.qualification_models import ParticipantQualification

        user = get_user_model().objects.create_user(username="qualification-staff", password="test", is_staff=True, is_active=True)
        permissions = Permission.objects.filter(
            content_type__app_label="participants",
            codename__in=("view_participant", "view_participantqualification", "add_participantqualification", "change_participantqualification"),
        )
        user.user_permissions.add(*permissions)
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")

        directory = self.client.get(reverse("participants:staff-qualifications"), {"q": "Taylor"})
        self.assertEqual(directory.status_code, 200)
        self.assertContains(directory, "Taylor")
        participant_url = reverse("participants:staff-participant-qualifications", args=[self.participant.pk])
        created = self.client.post(participant_url, {
            "catalog_entry": str(self.catalog.pk),
            "custom_name": "",
            "experience_details": "Shop and field experience",
            "years_of_experience": "2.5",
            "notes": "Confirmed by staff",
            "is_self_reported": "on",
            "audit_reason": "Participant interview",
        })
        self.assertRedirects(created, participant_url)
        record = ParticipantQualification.objects.get(participant=self.participant)
        self.assertEqual(record.source, ParticipantQualification.Source.STAFF)
        self.assertEqual(record.created_by, user)
        self.assertEqual(record.experience_details, "Shop and field experience")

        edit_url = reverse("participants:staff-participant-qualification-edit", args=[self.participant.pk, record.pk])
        updated = self.client.post(edit_url, {
            "catalog_entry": str(self.catalog.pk),
            "custom_name": "",
            "experience_details": "Shop, field, and maintenance experience",
            "years_of_experience": "3.0",
            "notes": "Updated after follow-up",
            "is_self_reported": "on",
            "audit_reason": "Follow-up interview",
        })
        self.assertRedirects(updated, participant_url)
        record.refresh_from_db()
        self.assertEqual(record.years_of_experience, 3)
        self.assertEqual(record.notes, "Updated after follow-up")
        self.assertEqual(AuditLog.objects.filter(action__startswith="qualification.participant_record.").count(), 2)

    def test_staff_without_add_permission_cannot_post_qualification(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        from django.urls import reverse

        user = get_user_model().objects.create_user(username="qualification-viewer", password="test", is_staff=True, is_active=True)
        permissions = Permission.objects.filter(
            content_type__app_label="participants",
            codename__in=("view_participant", "view_participantqualification"),
        )
        user.user_permissions.add(*permissions)
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.post(
            reverse("participants:staff-participant-qualifications", args=[self.participant.pk]),
            {"custom_name": "Welding", "audit_reason": "Attempted unauthorized change"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ParticipantQualification.objects.filter(participant=self.participant).exists())
