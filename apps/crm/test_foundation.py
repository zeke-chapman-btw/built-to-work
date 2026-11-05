import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.core.models import AuditLog
from apps.customers.models import CustomerContact, CustomerIdentity, CustomerMembership
from apps.operations.models import Task, TaskNotification
from apps.recruiting import employer
from apps.recruiting.models import CandidateDisclosure, CandidateOpportunity, EmployerCandidateFeedback
from apps.recruiting.test_employer import EmployerFixtures
from apps.staffing.models import StaffProfile

from . import access, services
from .models import ActivityType, ContactRelationship, Interaction, InteractionRevision, InteractionTaskLink


class CrmFoundationTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        self.grant = self.release()
        self.stamp = timezone.now() - timedelta(days=2)

    def details(self, **changes):
        data = {"activity_type": "call", "direction": "outbound", "summary": "SYNTHETIC follow-up call",
                "occurred_at": self.stamp, "outcome": "Conversation completed", "notes": "SYNTHETIC private details"}
        data.update(changes)
        return data

    def internal(self, **changes):
        data = {"actor": self.admin, "participant_id": self.person.pk,
                "data": self.details(), "operation_id": uuid.uuid4()}
        data.update(changes)
        return services.create_internal(**data)

    def employer_interaction(self, **changes):
        data = {"actor": self.customer, "organization_id": self.organization.pk, "disclosure_id": self.grant.pk,
                "data": self.details(summary="SYNTHETIC employer conversation"), "operation_id": uuid.uuid4()}
        data.update(changes)
        return services.create_employer(**data)

    def test_backdating_retains_creation_author_and_separate_histories(self):
        btw = self.internal()
        customer = self.employer_interaction()
        self.assertEqual(btw.occurred_at, self.stamp)
        self.assertGreater(btw.created_at, btw.occurred_at)
        self.assertEqual(btw.created_by, self.admin)
        self.assertEqual(customer.created_by, self.customer)
        self.assertIsNone(btw.owner_organization_id)
        self.assertEqual(customer.owner_organization, self.organization)
        self.assertEqual([r.pk for r in services.staff_history(self.admin, audience="btw", participant_id=self.person.pk)], [btw.pk])
        self.assertEqual([r.pk for r in services.staff_history(self.admin, audience="employer", participant_id=self.person.pk)], [customer.pk])
        projection = services.employer_history(self.customer, self.organization.pk, self.grant.pk)
        self.assertEqual([r["id"] for r in projection], [customer.pk])
        dumped = str(projection)
        self.assertNotIn(btw.summary, dumped)
        for forbidden in ("participant_id", "contact_email", "payload_fingerprint", "responsible_staff", "task_links", "disclosure"):
            self.assertNotIn(forbidden, dumped)

    def test_common_activity_types_and_other_are_recordable(self):
        for kind in ActivityType.values:
            with self.subTest(kind=kind):
                item = self.internal(data=self.details(activity_type=kind, summary="SYNTHETIC " + kind))
                self.assertEqual(item.activity_type, kind)
                self.assertEqual(item.source, "manual")
                self.assertEqual(item.delivery_status, "")

    def test_unsupported_type_future_time_and_blank_summary_do_not_create_rows(self):
        for values in ({"activity_type": "invented"}, {"occurred_at": timezone.now() + timedelta(days=1)}, {"summary": "   "}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.internal(data=self.details(**values))
        self.assertEqual(Interaction.objects.count(), 0)

    def test_manual_source_cannot_be_forged_or_send_messages(self):
        with self.assertRaises(ValidationError):
            self.internal(data=self.details(source="provider", delivery_status="sent"))
        before = (Task.objects.count(), TaskNotification.objects.count())
        with patch("django.core.mail.send_mail", side_effect=AssertionError("CRM cannot send")):
            for kind in ("email", "sms", "call"):
                self.internal(data=self.details(activity_type=kind))
        self.assertEqual((Task.objects.count(), TaskNotification.objects.count()), before)

    def test_idempotency_is_payload_actor_and_relationship_bound(self):
        operation = uuid.uuid4()
        first = self.internal(operation_id=operation)
        self.assertEqual(self.internal(operation_id=operation).pk, first.pk)
        self.assertEqual(first.history.count(), 1)
        with self.assertRaises(ValidationError):
            self.internal(operation_id=operation, data=self.details(summary="Different attempt"))
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.employer_interaction(operation_id=operation)
        operation = uuid.uuid4()
        second = self.employer_interaction(operation_id=operation)
        self.assertEqual(self.employer_interaction(operation_id=operation).pk, second.pk)
        with self.assertRaises(ValidationError):
            self.employer_interaction(operation_id=operation, data=self.details(summary="Altered employer replay"))

    def test_corrections_preserve_original_author_creation_context_and_snapshots(self):
        row = self.employer_interaction()
        original = (row.created_by_id, row.created_at, row.owner_organization_id, row.membership_id, row.disclosure_id)
        row = services.correct(actor=self.customer, organization_id=self.organization.pk, interaction_id=row.pk,
            expected_revision=1, data={"summary": "SYNTHETIC clarified call", "outcome": "Interview scheduled externally"}, why="SYNTHETIC corrected actual outcome")
        self.assertEqual(original, (row.created_by_id, row.created_at, row.owner_organization_id, row.membership_id, row.disclosure_id))
        self.assertEqual(row.revision, 2)
        self.assertEqual(row.history.first().snapshot["summary"], "SYNTHETIC employer conversation")
        self.assertEqual(row.history.last().snapshot["summary"], "SYNTHETIC clarified call")
        with self.assertRaises(ValidationError):
            services.correct(actor=self.customer, organization_id=self.organization.pk, interaction_id=row.pk,
                expected_revision=1, data={"summary": "stale overwrite"}, why="SYNTHETIC stale attempt")
        with self.assertRaises(ValidationError):
            services.correct(actor=self.customer, organization_id=self.organization.pk, interaction_id=row.pk,
                expected_revision=2, data={"owner_organization": self.organization}, why="SYNTHETIC forged scope")

    def test_internal_correction_and_archival_are_audited_and_non_destructive(self):
        row = self.internal()
        row = services.correct(actor=self.admin, interaction_id=row.pk, expected_revision=1,
            data={"summary": "SYNTHETIC clarified internal call"}, why="SYNTHETIC clarification")
        row = services.archive(actor=self.admin, interaction_id=row.pk, expected_revision=2, why="SYNTHETIC archive")
        self.assertTrue(Interaction.objects.filter(pk=row.pk).exists())
        self.assertEqual(services.staff_history(self.admin, audience="btw", participant_id=self.person.pk), [])
        self.assertEqual(len(services.staff_history(self.admin, audience="btw", participant_id=self.person.pk, include_archived=True)), 1)
        row = services.archive(actor=self.admin, interaction_id=row.pk, expected_revision=3, why="SYNTHETIC restore", restore=True)
        self.assertIsNone(row.archived_at)
        self.assertEqual(list(row.history.values_list("action", flat=True)), ["created", "corrected", "archived", "restored"])
        self.assertEqual(AuditLog.objects.filter(object_id=str(row.pk), action__startswith="crm.interaction.").count(), 4)

    def test_ownership_author_and_revision_snapshots_cannot_be_rewritten(self):
        row = self.internal()
        row.audience = "employer"
        with self.assertRaises(ValidationError):
            row.save()
        history = row.history.first()
        history.reason = "replace old reason"
        with self.assertRaises(ValidationError):
            history.save()
        with self.assertRaises(ValidationError):
            history.delete()

    def test_customer_participant_and_unprivileged_staff_cannot_read_or_write_btw_history(self):
        row = self.internal()
        worker = get_user_model().objects.create_user(username="synthetic-crm-worker", is_staff=True)
        for user in (self.customer, self.portal, worker):
            with self.subTest(user=user.pk):
                with self.assertRaises(PermissionDenied):
                    services.staff_record(user, row.pk)
                with self.assertRaises(PermissionDenied):
                    self.internal(actor=user)

    def test_foreign_employer_direct_ids_never_grant_read_write_or_correction(self):
        row = self.employer_interaction()
        other, member, user = self.lead()
        for action in (
            lambda: services.employer_history(user, other.pk, self.grant.pk),
            lambda: self.employer_interaction(actor=user, organization_id=other.pk),
            lambda: services.correct(actor=user, organization_id=other.pk, interaction_id=row.pk,
                expected_revision=1, data={"summary": "SYNTHETIC forbidden"}, why="SYNTHETIC attempt"),
            lambda: services.archive(actor=user, organization_id=other.pk, interaction_id=row.pk,
                expected_revision=1, why="SYNTHETIC attempt"),
        ):
            with self.assertRaises(PermissionDenied):
                action()
        self.assertEqual(row.history.count(), 1)

    def test_members_require_existing_scoped_response_permission_and_recheck_revocation(self):
        member = self.membership(self.organization, role="member")
        user = self.activate(member)
        with self.assertRaises(PermissionDenied):
            self.employer_interaction(actor=user)
        permission = employer.member_permission(actor=self.admin, disclosure_id=self.grant.pk, membership_id=member.pk,
            can_respond=True, expected_revision=0, why="SYNTHETIC scoped candidate response authority")
        row = self.employer_interaction(actor=user)
        employer.member_permission(actor=self.admin, disclosure_id=self.grant.pk, membership_id=member.pk,
            can_respond=False, expected_revision=permission.revision, why="SYNTHETIC response authority withdrawn")
        self.assertEqual(len(services.employer_history(user, self.organization.pk, self.grant.pk)), 1)
        with self.assertRaises(PermissionDenied):
            services.correct(actor=user, organization_id=self.organization.pk, interaction_id=row.pk,
                expected_revision=1, data={"outcome": "Forbidden"}, why="SYNTHETIC revoked permission")

    def test_disclosure_withdrawal_removes_employer_access_but_retains_btw_history(self):
        row = self.employer_interaction()
        employer.revoke(actor=self.admin, disclosure_id=self.grant.pk, expected_revision=self.grant.revision,
            why="SYNTHETIC release withdrawn")
        with self.assertRaises(PermissionDenied):
            services.employer_history(self.customer, self.organization.pk, self.grant.pk)
        with self.assertRaises(PermissionDenied):
            self.employer_interaction()
        with self.assertRaises(PermissionDenied):
            services.correct(actor=self.customer, organization_id=self.organization.pk, interaction_id=row.pk,
                expected_revision=1, data={"outcome": "Forbidden"}, why="SYNTHETIC revoked release")
        self.assertEqual(services.staff_record(self.admin, row.pk).pk, row.pk)
        self.assertEqual(row.history.count(), 1)

    def test_suspended_membership_and_stale_password_session_fail_closed(self):
        from apps.customers import services as customer_services
        member = self.membership(self.organization, role="member")
        user = self.activate(member)
        employer.member_permission(actor=self.admin, disclosure_id=self.grant.pk, membership_id=member.pk,
            can_respond=True, expected_revision=0, why="SYNTHETIC scoped response permission")
        row = self.employer_interaction(actor=user)
        member.refresh_from_db()
        self.organization.refresh_from_db()
        customer_services.save_membership(actor=self.admin, org_id=self.organization.pk,
            expected_org_revision=self.organization.revision, membership_id=member.pk,
            expected_revision=member.revision, role="member", state="suspended", why="SYNTHETIC authorized suspension")
        with self.assertRaises(PermissionDenied):
            services.employer_history(user, self.organization.pk, self.grant.pk)
        self.admin.security_revision += 1
        self.admin.save(update_fields=["security_revision"])
        old = get_user_model().objects.get(pk=self.admin.pk)
        old.security_revision -= 1
        with self.assertRaises(PermissionDenied):
            services.staff_record(old, row.pk)

    def test_recruiting_stage_hire_feedback_and_disclosures_do_not_change_when_logging(self):
        before = (self.candidate.stage_id, self.candidate.outcome, self.candidate.active, self.candidate.revision,
                  CandidateDisclosure.objects.count(), EmployerCandidateFeedback.objects.count())
        self.employer_interaction(data=self.details(activity_type="interview", outcome="Employer reports hired externally"))
        self.candidate.refresh_from_db()
        after = (self.candidate.stage_id, self.candidate.outcome, self.candidate.active, self.candidate.revision,
                 CandidateDisclosure.objects.count(), EmployerCandidateFeedback.objects.count())
        self.assertEqual(before, after)

    def test_related_contact_and_recruiting_engagement_are_one_interaction(self):
        contact = CustomerContact.objects.create(organization=self.organization, first_name="SYNTHETIC", last_name="Contact")
        row = self.internal(organization_id=self.organization.pk, contact_id=contact.pk, engagement_id=self.opportunity.pk,
            candidate_id=self.candidate.pk, data=self.details(activity_type="customer_meeting"))
        self.assertEqual(row.need, self.need)
        self.assertEqual(row.contact, contact)
        self.assertEqual(row.participant, self.person)
        self.assertEqual(Interaction.objects.count(), 1)
        self.assertEqual(services.staff_history(self.admin, audience="btw", organization_id=self.organization.pk)[0].pk, row.pk)

    def test_foreign_contacts_campaigns_and_contexts_are_rejected(self):
        other = self.org(name="SYNTHETIC foreign CRM company")
        contact = CustomerContact.objects.create(organization=other, first_name="SYNTHETIC", last_name="Other contact")
        with self.assertRaises(CustomerContact.DoesNotExist):
            self.internal(organization_id=self.organization.pk, contact_id=contact.pk)
        with self.assertRaises(PermissionDenied):
            self.internal(organization_id=other.pk, engagement_id=self.opportunity.pk)
        self.assertEqual(Interaction.objects.count(), 0)

    def test_followup_is_explicit_uses_existing_tasks_and_is_idempotent(self):
        row = self.internal()
        self.assertEqual(Task.objects.count(), 0)
        staff = StaffProfile.objects.create(user=self.admin, first_name="SYNTHETIC", last_name="Follow-up")
        data = {"title": "SYNTHETIC manual CRM follow-up", "assigned_staff": str(staff.pk), "priority": "normal",
                "revision": "new", "description": "SYNTHETIC next step", "due_date": ""}
        operation = uuid.uuid4()
        task, form = services.create_followup(actor=self.admin, interaction_id=row.pk, data=data, operation_id=operation)
        self.assertIsNotNone(task, form.errors if form else "")
        self.assertTrue(task.participant_links.filter(participant=self.person).exists())
        self.assertTrue(task.crm_links.filter(interaction=row).exists())
        self.assertEqual(services.create_followup(actor=self.admin, interaction_id=row.pk, data=data, operation_id=operation)[0].pk, task.pk)
        with self.assertRaises(ValidationError):
            services.create_followup(actor=self.admin, interaction_id=row.pk, data={**data, "title": "Changed replay"}, operation_id=operation)
        self.assertEqual(Task.objects.count(), 1)
        self.assertEqual(InteractionTaskLink.objects.count(), 1)

    def test_employer_cannot_create_or_link_internal_tasks(self):
        row = self.employer_interaction()
        with self.assertRaises(PermissionDenied):
            services.create_followup(actor=self.customer, interaction_id=row.pk, data={}, operation_id=uuid.uuid4())
        self.assertEqual(Task.objects.count(), 0)

    def test_responsible_staff_must_be_current_and_employer_cannot_set_it(self):
        staff = StaffProfile.objects.create(user=self.admin, first_name="SYNTHETIC", last_name="Responsible")
        row = self.internal(data=self.details(responsible_staff=staff))
        self.assertEqual(row.responsible_staff, staff)
        with self.assertRaises(PermissionDenied):
            self.employer_interaction(data=self.details(responsible_staff=staff))
        staff.is_active = False
        staff.save()
        with self.assertRaises(ValidationError):
            self.internal(data=self.details(responsible_staff=staff))
        corrected = services.correct(actor=self.admin, interaction_id=row.pk, expected_revision=1,
            data={"responsible_staff": staff, "notes": "SYNTHETIC clarify without reassigning retired staff"},
            why="SYNTHETIC preserve historical responsibility")
        self.assertEqual(corrected.responsible_staff_id, staff.pk)

    def test_primary_contact_and_preferences_do_not_create_accounts_memberships_or_consents(self):
        a = CustomerContact.objects.create(organization=self.organization, first_name="SYNTHETIC", last_name="Primary A")
        b = CustomerContact.objects.create(organization=self.organization, first_name="SYNTHETIC", last_name="Primary B")
        counts = (get_user_model().objects.count(), CustomerIdentity.objects.count(), CustomerMembership.objects.count())
        first = services.save_contact_relationship(actor=self.admin, contact_id=a.pk,
            data={"role": "Hiring contact", "is_primary": True, "sms_preference": "avoid"}, expected_revision=0, why="SYNTHETIC recorded contact request")
        services.save_contact_relationship(actor=self.admin, contact_id=b.pk, data={"is_primary": True}, expected_revision=0,
            why="SYNTHETIC explicit primary contact replacement")
        first.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertEqual(first.sms_preference, "avoid")
        self.assertEqual(first.email_preference, "unknown")
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.primary_contact_id, b.pk)
        self.assertEqual(sum(item.is_primary for item in ContactRelationship.objects.select_related("organization")), 1)
        self.assertEqual(counts, (get_user_model().objects.count(), CustomerIdentity.objects.count(), CustomerMembership.objects.count()))
        with self.assertRaises(PermissionDenied):
            services.save_contact_relationship(actor=self.customer, contact_id=a.pk, data={}, expected_revision=2, why="SYNTHETIC forbidden")
        services.save_contact_relationship(actor=self.admin, contact_id=a.pk,
            data={"role": "Updated hiring contact"}, expected_revision=1, why="SYNTHETIC update relationship")
        with self.assertRaises(ValidationError):
            services.save_contact_relationship(actor=self.admin, contact_id=a.pk, data={}, expected_revision=1, why="SYNTHETIC stale")

    def test_database_owner_constraint_and_correction_rollback_retain_original(self):
        row = self.internal()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Interaction.objects.filter(pk=row.pk).update(owner_organization=self.organization)
        with self.assertRaises(ValidationError):
            services.correct(actor=self.admin, interaction_id=row.pk, expected_revision=1,
                data={"summary": ""}, why="SYNTHETIC invalid correction")
        row.refresh_from_db()
        self.assertEqual(row.revision, 1)
        self.assertEqual(row.summary, "SYNTHETIC follow-up call")
        self.assertEqual(InteractionRevision.objects.filter(interaction=row).count(), 1)
