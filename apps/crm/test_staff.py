"""End-to-end staff CRM entry, context, history and existing-Task preservation."""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.customers.models import CustomerContact, CustomerMembership
from apps.customers.test_portal import BACKEND
from apps.operations.models import Task
from apps.recruiting.test_employer import EmployerFixtures
from apps.staffing.models import StaffProfile

from . import services
from .models import ContactRelationship, Interaction, InteractionRevision


class StaffCrmTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        self.grant = self.release()
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.stamp = timezone.now() - timedelta(days=2)

    def data(self, **changes):
        data = {"operation_id": str(uuid.uuid4()), "activity_type": "call", "summary": "SYNTHETIC staff touchpoint",
            "occurred_at": self.stamp.strftime("%Y-%m-%dT%H:%M"), "direction": "outbound", "outcome": "Spoke with participant"}
        data.update(changes)
        return data

    def create(self, **changes):
        data = {"activity_type": "call", "summary": "SYNTHETIC original", "occurred_at": self.stamp}
        data.update(changes)
        return services.create_internal(actor=self.admin, participant_id=self.person.pk, data=data, operation_id=uuid.uuid4())

    def test_participant_form_saves_once_returns_to_profile_and_retains_original_sections(self):
        url = reverse("crm:participant-new", args=[self.person.pk])
        payload = self.data()
        self.assertContains(self.client.get(url), "Log interaction")
        response = self.client.post(url, payload)
        self.assertRedirects(response, reverse("participants:staff-profile", args=[self.person.pk]) + "#btw-interactions")
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(Interaction.objects.count(), 1)
        profile = self.client.get(reverse("participants:staff-profile", args=[self.person.pk]))
        self.assertContains(profile, payload["summary"])  # recent touchpoints show on the profile
        relationship = self.client.get(reverse("participants:staff-profile", args=[self.person.pk]), {"section": "relationship"})
        self.assertContains(relationship, payload["summary"])
        self.assertContains(relationship, "BTW touchpoints")
        self.assertContains(relationship, "From employers")
        self.assertContains(profile, 'id="profile-skills"')
        self.assertContains(profile, 'id="profile-documents"')

    def test_invalid_form_keeps_answers_without_creating_activity(self):
        response = self.client.post(reverse("crm:participant-new", args=[self.person.pk]),
            self.data(summary="SYNTHETIC retained answer", occurred_at="not a timestamp"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SYNTHETIC retained answer")
        self.assertContains(response, "Review the highlighted details")
        self.assertEqual(Interaction.objects.count(), 0)

    def test_submitted_ownership_and_provider_status_are_rejected(self):
        for key, value in [("audience", "employer"), ("organization_id", str(self.organization.pk)), ("source", "provider"), ("delivery_status", "delivered")]:
            response = self.client.post(reverse("crm:participant-new", args=[self.person.pk]), self.data(**{key: value}))
            self.assertContains(response, "Only the displayed interaction details may be submitted")
        self.assertFalse(Interaction.objects.exists())

    def test_customer_meeting_links_existing_contact_and_engagement_in_one_record(self):
        contact = CustomerContact.objects.create(organization=self.organization, first_name="SYNTHETIC", last_name="Hiring")
        response = self.client.post(reverse("crm:customer-new", args=[self.organization.pk]),
            self.data(activity_type="customer_meeting", contact=str(contact.pk), engagement=str(self.opportunity.pk)))
        self.assertRedirects(response, reverse("customers:workspace", args=[self.organization.pk, "crm"]))
        row = Interaction.objects.get()
        self.assertEqual(row.contact, contact)
        self.assertEqual(row.engagement, self.opportunity)
        self.assertEqual(row.need, self.need)
        self.assertEqual(row.organization, self.organization)
        self.assertIsNone(row.participant)

    def test_foreign_customer_contact_and_engagement_are_not_selectable(self):
        from apps.customers.models import CustomerOrganization
        other = CustomerOrganization.objects.create(name="SYNTHETIC foreign relationship")
        contact = CustomerContact.objects.create(organization=other, first_name="SYNTHETIC", last_name="Other")
        response = self.client.post(reverse("crm:customer-new", args=[self.organization.pk]), self.data(contact=str(contact.pk)))
        self.assertContains(response, "Select a valid choice")
        self.assertFalse(Interaction.objects.exists())

    def test_completed_campaign_can_be_associated_with_a_backdated_company_touchpoint(self):
        from apps.customers.models import Campaign
        campaign = Campaign.objects.create(organization=self.organization, name="SYNTHETIC retained campaign", status="completed")
        response = self.client.post(reverse("crm:customer-new", args=[self.organization.pk]),
            self.data(campaign=str(campaign.pk), activity_type="recruiting"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Interaction.objects.get().campaign, campaign)

    def test_employer_rows_remain_in_separate_staff_context_and_cannot_be_edited_by_staff(self):
        row = services.create_employer(actor=self.customer, organization_id=self.organization.pk, disclosure_id=self.grant.pk,
            data={"activity_type": "interview", "summary": "SYNTHETIC employer-only interview", "occurred_at": self.stamp}, operation_id=uuid.uuid4())
        self.create()
        response = self.client.get(reverse("participants:staff-profile", args=[self.person.pk]), {"section": "relationship"})
        self.assertEqual([item["id"] for item in response.context["crm_employer_rows"]], [row.pk])
        self.assertNotIn(row.pk, [item["id"] for item in response.context["crm_btw_rows"]])
        self.assertEqual(self.client.get(reverse("crm:correct", args=[row.pk])).status_code, 403)
        self.assertFalse(response.context["crm_employer_rows"][0]["can_edit"])

    def test_corrections_preserve_author_with_stale_edit_and_archive_recovery(self):
        row = self.create()
        url = reverse("crm:correct", args=[row.pk])
        payload = self.data(summary="SYNTHETIC corrected", revision=1, reason="SYNTHETIC precise wording")
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        stale = self.client.post(url, {**payload, "summary": "SYNTHETIC stale overwrite"})
        self.assertContains(stale, "This interaction changed")
        row.refresh_from_db()
        self.assertEqual(row.created_by, self.admin)
        self.assertEqual(InteractionRevision.objects.filter(interaction=row).count(), 2)
        archive = reverse("crm:archive", args=[row.pk, "archive"])
        self.assertEqual(self.client.post(archive, {"revision": 2, "reason": "SYNTHETIC archive duplicate"}).status_code, 302)
        row.refresh_from_db()
        self.assertIsNotNone(row.archived_at)
        self.assertEqual(self.client.post(reverse("crm:archive", args=[row.pk, "restore"]),
            {"revision": 3, "reason": "SYNTHETIC restore review"}).status_code, 302)
        row.refresh_from_db()
        self.assertIsNone(row.archived_at)

    def test_followup_uses_existing_task_and_keeps_link_idempotent(self):
        row = self.create()
        staff = StaffProfile.objects.create(user=self.admin, first_name="SYNTHETIC", last_name="Followup")
        data = {"operation_id": str(uuid.uuid4()), "title": "SYNTHETIC follow-up", "assigned_staff": str(staff.pk),
            "priority": "normal", "revision": "new", "description": "SYNTHETIC manual task"}
        url = reverse("crm:followup", args=[row.pk])
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(Task.objects.count(), 1)
        self.assertContains(self.client.get(reverse("crm:detail", args=[row.pk])), "SYNTHETIC follow-up")

    def test_contact_preferences_use_native_primary_without_account_membership_side_effect(self):
        person = CustomerContact.objects.create(organization=self.organization, first_name="SYNTHETIC", last_name="Contact")
        counts = get_user_model().objects.count(), CustomerMembership.objects.count()
        response = self.client.post(reverse("crm:contact", args=[self.organization.pk, person.pk]), {
            "revision": 0, "role": "Hiring partner", "is_primary": "on", "email_preference": "preferred",
            "sms_preference": "avoid", "phone_preference": "unknown", "reason": "SYNTHETIC requested preferences"})
        self.assertEqual(response.status_code, 302)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.primary_contact_id, person.pk)
        self.assertEqual(ContactRelationship.objects.get(contact=person).sms_preference, "avoid")
        self.assertEqual(counts, (get_user_model().objects.count(), CustomerMembership.objects.count()))

    def test_customer_and_unauthenticated_sessions_cannot_enter_staff_history(self):
        row = self.create()
        self.client.logout()
        self.assertEqual(self.client.get(reverse("crm:detail", args=[row.pk])).status_code, 302)
        self.client.force_login(self.customer, backend=BACKEND)
        for url in [reverse("crm:detail", args=[row.pk]), reverse("crm:participant-new", args=[self.person.pk]),
                    reverse("crm:customer-new", args=[self.organization.pk])]:
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_history_filtering_and_pagination_never_mix_audiences(self):
        for n in range(12):
            self.create(summary="SYNTHETIC call " + str(n))
        self.create(activity_type="sms", summary="SYNTHETIC manual text")
        url = reverse("participants:staff-profile", args=[self.person.pk])
        first = self.client.get(url, {"section": "relationship"})
        self.assertEqual(len(first.context["crm_btw_rows"]), 10)
        filtered = self.client.get(url, {"section": "relationship", "crm_type": "sms"})
        self.assertEqual([item["summary"] for item in filtered.context["crm_btw_rows"]], ["SYNTHETIC manual text"])
        self.assertEqual(filtered.context["crm_employer_rows"], [])

    def test_archived_interaction_cannot_create_or_link_followups_by_direct_request(self):
        from django.core.exceptions import ValidationError
        row = self.create()
        services.archive(actor=self.admin, interaction_id=row.pk, expected_revision=1, why="SYNTHETIC controlled archive")
        self.assertEqual(self.client.get(reverse("crm:followup", args=[row.pk])).status_code, 403)
        with self.assertRaises(ValidationError):
            services.create_followup(actor=self.admin, interaction_id=row.pk, data={}, operation_id=uuid.uuid4())
        with self.assertRaises(ValidationError):
            services.link_task(actor=self.admin, interaction_id=row.pk, task_id=uuid.uuid4())
        self.assertFalse(Task.objects.exists())

    def test_archived_company_contact_settings_are_read_only_in_service_and_view(self):
        from django.core.exceptions import ValidationError
        contact = CustomerContact.objects.create(organization=self.organization, first_name="SYNTHETIC", last_name="Archived")
        self.organization.status = "archived"
        self.organization.save()
        self.assertEqual(self.client.get(reverse("crm:contact", args=[self.organization.pk, contact.pk])).status_code, 403)
        with self.assertRaises(ValidationError):
            services.save_contact_relationship(actor=self.admin, contact_id=contact.pk, data={"role": "SYNTHETIC rejected"},
                expected_revision=0, why="SYNTHETIC direct service check")
        self.assertFalse(ContactRelationship.objects.exists())

    def test_archived_participant_profile_retains_authorized_read_only_interactions(self):
        row = self.create()
        self.person.archived_at = timezone.now()
        self.person.save()
        response = self.client.get(reverse("participants:staff-profile", args=[self.person.pk]), {"section": "relationship"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, row.summary)
        self.assertFalse(response.context["crm_can_add"])
        self.assertFalse(response.context["crm_btw_rows"][0]["can_edit"])
        self.assertEqual(self.client.get(reverse("crm:participant-new", args=[self.person.pk])).status_code, 403)
