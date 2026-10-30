"""Customer contracts exercised through services and actual database invariants."""
import uuid
from decimal import Decimal
from datetime import date, timedelta

from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.models import Group, Permission, AnonymousUser
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import IntegrityError, transaction, connection
from django.test import TestCase
from django.utils import timezone

from apps.accounts.roles import role_configuration, ROLE_GROUPS, MANAGER_PERMISSIONS_V1, permission_names, roles_for
from apps.accounts import staff_services
from apps.accounts.models import StaffInvitation
from apps.participants.models import Participant, ParticipantAccount
from apps.staffing.models import StaffProfile
from apps.core.models import AuditLog
from apps.customers import services as svc
from apps.customers.access import organizations, require_manager
from apps.customers.models import (CustomerOrganization as Org, CustomerContact, CustomerLocation,
                     CustomerIdentity, CustomerMembership as Member, CustomerRelationship,
                     CustomerAccessPlan, Campaign, WorkforceNeed as Need,
                     WorkforceNeedRevision as Revision, CustomerAuditScope,
                     TERM_FIELD_NAMES)
from apps.customers.validation import submission_errors, ANSWER_TOPICS

User = get_user_model()
WHY = "Synthetic Build 13 verification"


class CustomerFixtures:
    @classmethod
    def setUpTestData(cls):
        role_configuration(apply=True)
        cls.admin = User.objects.create_superuser("synthetic-b13-admin", "admin@example.test", "Synthetic-test-password-123")
        cls.manager = User.objects.create_user("synthetic-b13-manager", is_staff=True, password="Synthetic-test-password-123")
        cls.manager.groups.add(Group.objects.get(name=ROLE_GROUPS["manager"]))
        cls.recruiter = User.objects.create_user("synthetic-b13-recruiter", is_staff=True)
        cls.recruiter.groups.add(Group.objects.get(name=ROLE_GROUPS["recruiter"]))
        cls.worker = User.objects.create_user("synthetic-b13-worker", is_staff=True)
        cls.worker.groups.add(Group.objects.get(name=ROLE_GROUPS["event_staff"]))
        cls.combined = User.objects.create_user("synthetic-b13-combined", is_staff=True)
        cls.combined.groups.add(Group.objects.get(name=ROLE_GROUPS["manager"]), Group.objects.get(name=ROLE_GROUPS["event_staff"]))
        cls.unaffiliated = User.objects.create_user("synthetic-b13-unaffiliated", is_staff=True)

    def org(self, *, configured=True, name="SYNTHETIC customer"):
        org = svc.save_organization(actor=self.admin, data={"name": name}, why=WHY)
        if configured:
            self.plan(org)
        return org

    def plan(self, org, **overrides):
        org.refresh_from_db()
        policy = org.access_plan
        data = {"lead_limit": 4, "member_limit": 3, "workforce_need_creation": True}
        data.update(overrides)
        result = svc.save_access_plan(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision,
                                      expected_revision=policy.revision, data=data, why=WHY)
        org.refresh_from_db()
        return result

    def membership(self, org, *, role="lead", identity=None, state="enabled", actor=None, designate=None):
        org.refresh_from_db()
        kwargs = {"identity_id": identity.pk} if identity else {"identity_data": {"first_name": "SYNTHETIC", "last_name": "Customer user", "email": uuid.uuid4().hex + "@example.test"}}
        result = svc.save_membership(actor=actor or self.admin, org_id=org.pk,
                                    expected_org_revision=org.revision, role=role, state=state,
                                    designate_primary=(not org.provisioned and role == "lead" and state == "enabled") if designate is None else designate,
                                    why=WHY, **kwargs)
        org.refresh_from_db()
        return result

    def child(self, org, kind, data):
        org.refresh_from_db()
        obj = svc.save_child(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision,
                             kind=kind, data=data, why=WHY)
        org.refresh_from_db()
        return obj

    def payload(self, org):
        location = self.child(org, "location", {"name": "SYNTHETIC work site", "address_line1": "100 Example Road", "city": "Example City", "state": "GA", "country": "United States"})
        contact = self.child(org, "contact", {"first_name": "SYNTHETIC", "last_name": "Hiring contact", "email": "contact@example.test"})
        data = {"title": "SYNTHETIC Welder", "trade": "Welding", "description": "Fabrication and repair of steel assemblies.",
                "worker_quantity": 2, "employment_type": "full_time", "location_mode": "location", "location": location,
                "pay_response": "provided", "pay_minimum": Decimal("20"), "pay_maximum": Decimal("30"), "pay_currency": "USD", "pay_basis": "hour",
                "overtime_response": "no", "shift": "day", "schedule_details": "Monday to Friday, 8 AM to 4 PM", "hours_mode": "specified", "hours_per_week": Decimal("40"),
                "start_mode": "timeframe", "start_timeframe": "Within 30 days", "duration_response": "ongoing", "travel_response": "no", "overnight_response": "no",
                "primary_contact": contact, "hiring_timeline": "Interview within two weeks"}
        data.update({topic + "_response": "none" for topic in ANSWER_TOPICS})
        return data

    def draft(self, org, data=None, need=None):
        if data is None:
            data = self.payload(org)
        org.refresh_from_db()
        if need:
            need.refresh_from_db()
        return svc.save_hiring_draft(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision,
                                     data=data, need_id=need.pk if need else None,
                                     expected_need_revision=need.revision if need else None,
                                     expected_working_id=need.working_revision_id if need else None, why=WHY)

    def transition(self, org, need, action, *, actor=None):
        org.refresh_from_db()
        need.refresh_from_db()
        result = svc.revision_action(actor=actor or self.admin, org_id=org.pk, need_id=need.pk,
                                     expected_org_revision=org.revision, expected_need_revision=need.revision,
                                     revision_id=need.working_revision_id, action=action, why=WHY)
        org.refresh_from_db()
        need.refresh_from_db()
        return result

    def approved_need(self, org):
        data = self.payload(org)
        need = self.draft(org, data)
        for action in ("submit", "review", "approve"):
            self.transition(org, need, action)
        return need

    def campaign(self, org, name="SYNTHETIC recruiting effort"):
        org.refresh_from_db()
        result = svc.save_campaign(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision,
                                   data={"name": name, "description": "Coordinated hiring effort", "objectives": "Fill approved Workforce Needs"}, why=WHY)
        org.refresh_from_db()
        return result

    def db_rejects(self, operation):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                operation()
                connection.check_constraints()


class AuthorityAndIdentityTests(CustomerFixtures, TestCase):
    def test_only_active_administrators_managers_and_combined_roles(self):
        for actor in (self.admin, self.manager, self.combined):
            with self.subTest(actor=actor.username):
                self.assertEqual(require_manager(actor).pk, actor.pk)
        for actor in (self.recruiter, self.worker, self.unaffiliated, AnonymousUser()):
            with self.subTest(actor=getattr(actor, "username", "anonymous")):
                with self.assertRaises(PermissionDenied):
                    svc.save_organization(actor=actor, data={"name": "Denied"}, why=WHY)
                with self.assertRaises(PermissionDenied):
                    organizations(actor)

    def test_single_specific_manager_permission_extension(self):
        self.assertEqual(permission_names("manager") - MANAGER_PERMISSIONS_V1, {"customers.manage_customers"})
        self.assertEqual(permission_names("event_staff"), set())
        self.assertEqual(len(permission_names("recruiter")), 6)

    def test_direct_customer_permission_without_manager_role_does_not_grant_access(self):
        self.unaffiliated.user_permissions.add(Permission.objects.get(content_type__app_label="customers", codename="manage_customers"))
        with self.assertRaises(PermissionDenied):
            require_manager(self.unaffiliated)

    def test_manager_role_without_reviewed_permission_is_denied(self):
        Group.objects.get(name=ROLE_GROUPS["manager"]).permissions.remove(Permission.objects.get(content_type__app_label="customers", codename="manage_customers"))
        with self.assertRaises(PermissionDenied):
            require_manager(self.manager)

    def test_stale_authority_cache_cannot_write(self):
        self.assertIn("manager", roles_for(self.manager))
        self.manager.groups.clear()
        with self.assertRaises(PermissionDenied):
            svc.save_organization(actor=self.manager, data={"name": "Denied"}, why=WHY)

    def test_deactivated_actor_cannot_use_stale_instance(self):
        User.objects.filter(pk=self.manager.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            svc.save_organization(actor=self.manager, data={"name": "Denied"}, why=WHY)

    def test_forged_organization_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            svc.save_organization(actor=self.admin, data={"name": "Forged", "provisioned": True}, why=WHY)
        self.assertFalse(Org.objects.exists())

    def test_customer_authentication_is_prepared_inactive_and_has_no_tokens(self):
        membership = self.membership(self.org())
        identity = membership.identity
        user = identity.user
        self.assertFalse(user.is_active or user.is_staff or user.is_superuser or user.has_usable_password())
        self.assertEqual(user.email, "")
        self.assertIsNone(authenticate(username=user.username, password="anything"))
        self.assertFalse(StaffInvitation.objects.filter(user=user).exists())
        self.assertFalse(user.groups.exists() or user.user_permissions.exists())
        with self.assertRaises(PermissionDenied):
            require_manager(user)

    def test_customer_cannot_become_staff_or_receive_staff_invitation(self):
        user = self.membership(self.org()).identity.user
        with self.assertRaises(PermissionDenied):
            staff_services.save_account(actor=self.admin, user_id=user.pk, data={"username": user.username, "email": "other@example.test", "roles": ["recruiter"], "confirm_identity": True, "revision": staff_services.revision(user), "reason": WHY})
        with self.assertRaises(PermissionDenied):
            staff_services.issue_invitation(user=user, actor=self.admin, expected_revision=staff_services.revision(user), reason=WHY)
        user.refresh_from_db()
        self.assertFalse(user.is_staff or user.is_active)

    def test_database_blocks_customer_auth_flags_password_and_email(self):
        user = self.membership(self.org()).identity.user
        for change in ({"is_active": True}, {"is_staff": True}, {"is_superuser": True}, {"password": "pbkdf2_fake_usable"}, {"email": "active@example.test"}):
            with self.subTest(change=list(change)):
                self.db_rejects(lambda: User.objects.filter(pk=user.pk).update(**change))

    def test_database_blocks_customer_grants_and_other_identity_links(self):
        user = self.membership(self.org()).identity.user
        participant = Participant.objects.create(first_name="SYNTHETIC", last_name="Person")
        operations = [
            lambda: user.groups.add(Group.objects.get(name=ROLE_GROUPS["manager"])),
            lambda: user.user_permissions.add(Permission.objects.get(content_type__app_label="customers", codename="manage_customers")),
            lambda: ParticipantAccount.objects.create(user=user, participant=participant, login_email="portal@example.test"),
            lambda: StaffProfile.objects.create(user=user, first_name="SYNTHETIC", last_name="Denied"),
            lambda: StaffInvitation.objects.create(user=user, security_revision=0, expires_at=timezone.now() + timedelta(days=1)),
        ]
        for operation in operations:
            self.db_rejects(operation)

    def test_existing_staff_identity_cannot_be_classified_as_customer(self):
        self.db_rejects(lambda: CustomerIdentity.objects.create(user=self.manager, first_name="Denied", email="denied@example.test"))

    def test_participant_identity_cannot_be_reclassified_even_if_inactive(self):
        user = User.objects.create_user("synthetic-existing-participant", is_active=False)
        participant = Participant.objects.create(first_name="SYNTHETIC", last_name="Participant")
        ParticipantAccount.objects.create(user=user, participant=participant, login_email="participant@example.test")
        self.db_rejects(lambda: CustomerIdentity.objects.create(user=user, first_name="Denied", email="denied@example.test"))

    def test_same_email_does_not_automatically_link_or_merge(self):
        a, b = self.org(), self.org(name="SYNTHETIC second company")
        first = self.membership(a)
        before = User.objects.count()
        b.refresh_from_db()
        with self.assertRaises(ValidationError):
            svc.save_membership(actor=self.admin, org_id=b.pk, expected_org_revision=b.revision, role="lead", designate_primary=True,
                                identity_data={"first_name": "SYNTHETIC", "email": first.identity.email.upper()}, why=WHY)
        self.assertEqual(User.objects.count(), before)
        b.refresh_from_db()
        self.assertFalse(b.provisioned)
        self.assertFalse(b.memberships.exists())

    def test_contact_without_login_does_not_create_identity(self):
        org = self.org()
        before = User.objects.count()
        contact = self.child(org, "contact", {"first_name": "SYNTHETIC", "email": "contact@example.test"})
        self.assertTrue(contact.pk)
        self.assertEqual(User.objects.count(), before)
        self.assertFalse(CustomerIdentity.objects.exists())


class OrganizationAndSeatTests(CustomerFixtures, TestCase):
    def test_new_suspended_membership_requires_configured_allowance_but_uses_no_seat(self):
        org = self.org(configured=False)
        identities = CustomerIdentity.objects.count()
        with self.assertRaises(ValidationError):
            self.membership(org, state="suspended")
        self.assertEqual(CustomerIdentity.objects.count(), identities)
        self.assertEqual(org.memberships.count(), 0)
        self.plan(org, lead_limit=0)
        member = self.membership(org, state="suspended")
        self.assertEqual(member.state, "suspended")
        self.assertEqual(svc.seat_usage(org), {"lead": 0, "member": 0})

    def test_initial_organization_is_unprovisioned_and_limits_are_not_invented(self):
        org = self.org(configured=False)
        self.assertEqual(org.status, "prospect")
        self.assertFalse(org.provisioned)
        self.assertIsNone(org.primary_lead_id)
        self.assertIsNone(org.access_plan.lead_limit)
        self.assertIsNone(org.access_plan.member_limit)
        self.assertIsNone(org.access_plan.candidate_matching)

    def test_unconfigured_and_zero_allowances_block_new_enabled_memberships(self):
        for limit in (None, 0):
            org = self.org()
            self.plan(org, lead_limit=limit)
            before = User.objects.count()
            with self.assertRaises(ValidationError):
                self.membership(org)
            self.assertEqual(User.objects.count(), before)
            self.assertFalse(org.memberships.exists())

    def test_first_enabled_user_must_be_explicit_primary_lead(self):
        org = self.org()
        for role, designate in (("member", False), ("lead", False)):
            with self.assertRaises(ValidationError):
                self.membership(org, role=role, designate=designate)
        lead = self.membership(org)
        self.assertTrue(org.provisioned)
        self.assertEqual(org.primary_lead_id, lead.pk)
        self.assertEqual(svc.seat_usage(org), {"lead": 1, "member": 0})

    def test_primary_lead_counts_toward_lead_limit(self):
        org = self.org()
        self.plan(org, lead_limit=1)
        self.membership(org)
        with self.assertRaises(ValidationError):
            self.membership(org)

    def test_cannot_suspend_demote_or_remove_primary_without_transfer(self):
        org = self.org()
        primary = self.membership(org)
        for role, state in (("member", "enabled"), ("lead", "suspended"), ("lead", "removed")):
            with self.assertRaises(ValidationError):
                svc.save_membership(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision,
                                    membership_id=primary.pk, expected_revision=primary.revision, role=role, state=state, why=WHY)
        self.assertEqual(Org.objects.get(pk=org.pk).primary_lead_id, primary.pk)

    def test_primary_replacement_retains_old_lead_unless_explicitly_changed(self):
        org = self.org()
        old, new = self.membership(org), self.membership(org)
        svc.transfer_primary(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision, target_id=new.pk, why=WHY)
        old.refresh_from_db()
        org.refresh_from_db()
        self.assertEqual(org.primary_lead_id, new.pk)
        self.assertEqual(old.role, "lead")

    def test_explicit_seat_neutral_primary_swap_remains_possible_when_over_cap(self):
        org = self.org()
        self.plan(org, lead_limit=1, member_limit=1)
        old, new = self.membership(org), self.membership(org, role="member")
        self.plan(org, lead_limit=0, member_limit=0)
        svc.transfer_primary(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision,
                             target_id=new.pk, previous_role="member", why="Explicitly swap Primary Lead roles")
        old.refresh_from_db()
        new.refresh_from_db()
        org.refresh_from_db()
        self.assertEqual((old.role, new.role, org.primary_lead_id), ("member", "lead", new.pk))
        self.assertEqual(svc.seat_usage(org), {"lead": 1, "member": 1})

    def test_limit_reduction_keeps_memberships_and_blocks_increasing_overage(self):
        org = self.org()
        self.membership(org)
        member = self.membership(org, role="member")
        self.plan(org, member_limit=0)
        member.refresh_from_db()
        self.assertEqual(member.state, "enabled")
        with self.assertRaises(ValidationError):
            self.membership(org, role="member")

    def test_membership_reactivation_observes_capacity(self):
        org = self.org()
        self.membership(org)
        a = self.membership(org, role="member")
        svc.save_membership(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision, membership_id=a.pk,
                            expected_revision=a.revision, role="member", state="suspended", why=WHY)
        org.refresh_from_db()
        self.plan(org, member_limit=1)
        self.membership(org, role="member")
        a.refresh_from_db()
        with self.assertRaises(ValidationError):
            svc.save_membership(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision, membership_id=a.pk,
                                expected_revision=a.revision, role="member", state="enabled", why=WHY)

    def test_explicit_multi_company_membership_and_removal_are_independent(self):
        a, b = self.org(), self.org(name="SYNTHETIC division")
        first = self.membership(a)
        second = self.membership(b, identity=first.identity)
        replacement = self.membership(a)
        svc.transfer_primary(actor=self.admin, org_id=a.pk, expected_org_revision=a.revision, target_id=replacement.pk, why=WHY)
        a.refresh_from_db()
        first.refresh_from_db()
        original = (first.identity.user.password, first.identity.user.security_revision)
        svc.save_membership(actor=self.admin, org_id=a.pk, expected_org_revision=a.revision, membership_id=first.pk,
                            expected_revision=first.revision, role="lead", state="removed", why=WHY)
        second.refresh_from_db()
        first.identity.user.refresh_from_db()
        self.assertEqual(second.state, "enabled")
        self.assertEqual(Org.objects.get(pk=b.pk).primary_lead_id, second.pk)
        self.assertEqual((first.identity.user.password, first.identity.user.security_revision), original)

    def test_connected_companies_do_not_inherit_memberships_or_entitlements(self):
        a, b = self.org(), self.org(configured=False)
        self.membership(a)
        svc.save_relationship(actor=self.admin, org_id=a.pk, target_id=b.pk, kind="parent", active=True,
                              expected_org_revision=a.revision, why=WHY)
        self.assertFalse(b.memberships.exists())
        b.refresh_from_db()
        self.assertIsNone(b.access_plan.workforce_need_creation)
        self.assertFalse(b.provisioned)

    def test_hierarchical_cycles_are_rejected(self):
        a, b = self.org(), self.org()
        svc.save_relationship(actor=self.admin, org_id=a.pk, target_id=b.pk, kind="division", active=True, expected_org_revision=a.revision, why=WHY)
        b.refresh_from_db()
        with self.assertRaises(ValidationError):
            svc.save_relationship(actor=self.admin, org_id=b.pk, target_id=a.pk, kind="parent", active=True, expected_org_revision=b.revision, why=WHY)

    def test_archive_restore_preserves_child_records_and_connected_org(self):
        org, other = self.org(), self.org()
        member = self.membership(org)
        need = self.approved_need(org)
        campaign = self.campaign(org)
        org.refresh_from_db()
        svc.organization_status(actor=self.admin, org_id=org.pk, expected_revision=org.revision, action="archive", why=WHY)
        org.refresh_from_db()
        need.refresh_from_db()
        campaign.refresh_from_db()
        member.refresh_from_db()
        self.assertEqual((need.status, campaign.status, member.state), ("active", "planning", "enabled"))
        self.assertEqual(Org.objects.get(pk=other.pk).status, "prospect")
        with self.assertRaises(ValidationError):
            self.child(org, "contact", {"first_name": "Denied"})
        svc.organization_status(actor=self.admin, org_id=org.pk, expected_revision=org.revision, action="restore", why=WHY)
        self.assertEqual(Org.objects.get(pk=org.pk).status, "prospect")

    def test_stale_policy_and_member_changes_are_rejected(self):
        org = self.org()
        version = org.revision
        lead = self.membership(org)
        with self.assertRaises(ValidationError):
            svc.save_access_plan(actor=self.admin, org_id=org.pk, expected_org_revision=version,
                                 expected_revision=org.access_plan.revision, data={"member_limit": 99}, why=WHY)
        with self.assertRaises(ValidationError):
            svc.save_membership(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision,
                                membership_id=lead.pk, expected_revision=-1, role="lead", why=WHY)

    def test_database_rejects_cross_org_contact_and_primary_lead(self):
        a, b = self.org(), self.org()
        lead_a, lead_b = self.membership(a), self.membership(b)
        contact = self.child(a, "contact", {"first_name": "SYNTHETIC"})
        self.db_rejects(lambda: Member.objects.filter(pk=lead_b.pk).update(contact=contact))
        self.db_rejects(lambda: Org.objects.filter(pk=b.pk).update(primary_lead=lead_a))

    def test_stored_entitlements_do_not_grant_customer_authentication(self):
        org = self.org()
        user = self.membership(org).identity.user
        self.plan(org, candidate_matching=True, directory_search=True, expanded_candidate_profile=True, authorized_candidate_contact=True)
        user.refresh_from_db()
        self.assertFalse(user.is_active or user.is_staff or user.has_usable_password())
        self.assertFalse(user.user_permissions.exists())


class WorkforceNeedTests(CustomerFixtures, TestCase):
    def test_incomplete_draft_is_allowed_and_submission_is_field_specific(self):
        org = self.org()
        need = self.draft(org, {})
        self.assertEqual(need.status, "draft")
        with self.assertRaises(ValidationError) as caught:
            self.transition(org, need, "submit")
        self.assertIn("worker_quantity", caught.exception.message_dict)
        self.assertIn("primary_contact", caught.exception.message_dict)
        self.assertEqual(Need.objects.get(pk=need.pk).status, "draft")

    def test_every_required_submission_topic_is_checked(self):
        org = self.org()
        data = self.payload(org)
        need = self.draft(org, data)
        revision = need.working_revision
        self.assertEqual(submission_errors(revision), {})
        required = ["title", "trade", "description", "worker_quantity", "employment_type", "location_mode", "pay_response", "shift", "schedule_details", "hours_mode", "start_mode", "duration_response", "primary_contact", "hiring_timeline"]
        required += [topic + "_response" for topic in ANSWER_TOPICS]
        required += [topic + "_response" for topic in ("overtime", "travel", "overnight")]
        for field in required:
            original = getattr(revision, field)
            setattr(revision, field, None if field in {"worker_quantity", "primary_contact"} else "")
            with self.subTest(topic=field):
                self.assertIn(field, submission_errors(revision))
            setattr(revision, field, original)

    def test_none_and_na_are_distinct_and_na_requires_explanation(self):
        org = self.org()
        need = self.draft(org)
        revision = need.working_revision
        self.assertEqual(submission_errors(revision), {})
        revision.benefits_response = "not_applicable"
        self.assertIn("benefits_details", submission_errors(revision))
        revision.benefits_details = "Not applicable to this independent contracting engagement."
        self.assertNotIn("benefits_details", submission_errors(revision))
        revision.benefits_response = ""
        self.assertIn("benefits_response", submission_errors(revision))

    def test_provided_topics_require_meaningful_details(self):
        org = self.org()
        revision = self.draft(org).working_revision
        for topic in ANSWER_TOPICS:
            setattr(revision, topic + "_response", "provided")
            setattr(revision, topic + "_details", "  ")
            with self.subTest(topic=topic):
                self.assertIn(topic + "_details", submission_errors(revision))
            setattr(revision, topic + "_response", "none")

    def test_pay_na_is_explicit_and_cannot_hide_amounts(self):
        org = self.org()
        revision = self.draft(org).working_revision
        revision.pay_response, revision.pay_details = "not_applicable", "Explain the non-cash arrangement for BTW review."
        self.assertIn("pay_minimum", submission_errors(revision))
        revision.pay_minimum = revision.pay_maximum = None
        self.assertEqual(submission_errors(revision), {})

    def test_invalid_quantities_ranges_hours_and_dates_are_rejected_even_for_drafts(self):
        org = self.org()
        data = self.payload(org)
        cases = [{"worker_quantity": 0}, {"worker_quantity": -1}, {"pay_minimum": Decimal("-1")},
                 {"pay_minimum": Decimal("40"), "pay_maximum": Decimal("20")},
                 {"hours_per_week": Decimal("169")}, {"required_experience_years": Decimal("-1")},
                 {"start_date": date(2030, 4, 2), "end_date": date(2030, 4, 1)}, {"pay_currency": "US"}]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    self.draft(org, dict(data, **changes))
        self.assertFalse(Need.objects.exists())

    def test_custom_remote_variable_schedule_and_timeframe_contracts(self):
        org = self.org()
        revision = self.draft(org).working_revision
        revision.location_mode, revision.location, revision.location_details = "remote", None, ""
        self.assertIn("location_details", submission_errors(revision))
        revision.location_details = "Remote within the United States; no on-site requirement."
        revision.hours_mode, revision.hours_per_week = "variable", None
        self.assertEqual(submission_errors(revision), {})
        revision.start_mode, revision.start_date = "date", None
        self.assertIn("start_date", submission_errors(revision))

    def test_inactive_or_cross_org_contact_cannot_be_submitted(self):
        a, b = self.org(), self.org()
        other = self.child(b, "contact", {"first_name": "SYNTHETIC other"})
        data = self.payload(a)
        with self.assertRaises(CustomerContact.DoesNotExist):
            self.draft(a, dict(data, primary_contact=other))
        need = self.draft(a, data)
        CustomerContact.objects.filter(pk=data["primary_contact"].pk).update(is_active=False)
        with self.assertRaises(ValidationError) as caught:
            self.transition(a, need, "submit")
        self.assertIn("primary_contact", caught.exception.message_dict)

    def test_need_creation_requires_entitlement_but_disabling_does_not_close_it(self):
        org = self.org()
        self.plan(org, workforce_need_creation=False)
        with self.assertRaises(ValidationError):
            self.draft(org, {})
        self.plan(org, workforce_need_creation=True)
        need = self.approved_need(org)
        self.plan(org, workforce_need_creation=False)
        need.refresh_from_db()
        self.assertEqual(need.status, "active")

    def test_initial_lifecycle_and_approval_actor_are_recorded(self):
        org = self.org()
        need = self.draft(org)
        self.transition(org, need, "submit")
        self.assertEqual(need.status, "submitted")
        self.transition(org, need, "review")
        self.assertEqual(need.status, "review")
        self.transition(org, need, "approve", actor=self.manager)
        self.assertEqual(need.status, "active")
        self.assertIsNone(need.working_revision_id)
        self.assertEqual(need.effective_revision.approved_by_id, self.manager.pk)
        self.assertIsNotNone(need.effective_revision.approved_at)

    def test_cannot_approve_unsubmitted_or_unreviewed_revision(self):
        org = self.org()
        need = self.draft(org)
        with self.assertRaises(ValidationError):
            self.transition(org, need, "approve")
        self.transition(org, need, "submit")
        with self.assertRaises(ValidationError):
            self.transition(org, need, "approve")

    def test_unauthorized_actor_cannot_approve(self):
        org = self.org()
        need = self.draft(org)
        self.transition(org, need, "submit")
        self.transition(org, need, "review")
        with self.assertRaises(PermissionDenied):
            self.transition(org, need, "approve", actor=self.recruiter)

    def test_pending_material_revision_preserves_effective_approved_terms(self):
        org = self.org()
        need = self.approved_need(org)
        old = need.effective_revision
        changed = self.draft(org, {"pay_minimum": Decimal("22"), "worker_quantity": 4}, need=need)
        self.assertEqual(changed.effective_revision_id, old.pk)
        self.assertEqual(changed.effective_revision.worker_quantity, 2)
        self.assertEqual(changed.working_revision.worker_quantity, 4)
        self.transition(org, changed, "submit")
        self.transition(org, changed, "review")
        self.assertEqual(changed.status, "active")
        self.transition(org, changed, "approve")
        old.refresh_from_db()
        self.assertEqual(old.worker_quantity, 2)
        self.assertEqual(changed.effective_revision.worker_quantity, 4)
        self.assertEqual(old.state, "approved")

    def test_all_hiring_terms_are_material_and_no_minor_flag_exists(self):
        org = self.org()
        need = self.approved_need(org)
        with self.assertRaises(ValidationError):
            self.draft(org, {"minor": True, "worker_quantity": 99}, need=need)
        with self.assertRaises(ValidationError):
            svc.update_need_administration(actor=self.admin, org_id=org.pk, need_id=need.pk,
                                            expected_org_revision=org.revision, expected_need_revision=need.revision,
                                            data={"pay_minimum": Decimal("1")}, why=WHY)

    def test_administrative_changes_preserve_pending_revision(self):
        org = self.org()
        need = self.approved_need(org)
        need = self.draft(org, {"worker_quantity": 3}, need=need)
        self.transition(org, need, "submit")
        pending = need.working_revision_id
        effective = need.effective_revision_id
        svc.update_need_administration(actor=self.admin, org_id=org.pk, need_id=need.pk,
                                        expected_org_revision=org.revision, expected_need_revision=need.revision,
                                        data={"internal_reference": "SYNTHETIC reference"}, why=WHY)
        need.refresh_from_db()
        self.assertEqual((need.working_revision_id, need.effective_revision_id), (pending, effective))
        self.assertEqual(need.working_revision.state, "submitted")

    def test_submitted_and_approved_payloads_cannot_be_overwritten_at_database_level(self):
        org = self.org()
        need = self.draft(org)
        self.transition(org, need, "submit")
        rev = need.working_revision
        self.db_rejects(lambda: Revision.objects.filter(pk=rev.pk).update(worker_quantity=99))
        self.db_rejects(lambda: Revision.objects.filter(pk=rev.pk).delete())
        self.transition(org, need, "review")
        self.transition(org, need, "approve")
        self.db_rejects(lambda: Revision.objects.filter(pk=rev.pk).update(decision_reason="Overwrite historical approval"))

    def test_submitted_location_and_contact_snapshots_do_not_follow_reference_edits(self):
        org = self.org()
        data = self.payload(org)
        need = self.draft(org, data)
        self.transition(org, need, "submit")
        saved = need.working_revision
        self.child_update(org, "location", data["location"], {"city": "Changed city"})
        self.child_update(org, "contact", data["primary_contact"], {"first_name": "Changed name"})
        self.transition(org, need, "review")
        self.transition(org, need, "approve")
        saved.refresh_from_db()
        self.assertEqual(saved.city, "Example City")
        self.assertTrue(saved.contact_name.startswith("SYNTHETIC"))

    def child_update(self, org, kind, obj, data):
        org.refresh_from_db()
        obj.refresh_from_db()
        result = svc.save_child(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision,
                                kind=kind, record_id=obj.pk, expected_revision=obj.revision, data=data, why=WHY)
        org.refresh_from_db()
        return result

    def test_returned_and_withdrawn_revisions_are_retained(self):
        org = self.org()
        need = self.draft(org)
        self.transition(org, need, "submit")
        old = need.working_revision
        self.transition(org, need, "return")
        self.assertEqual(need.status, "draft")
        old.refresh_from_db()
        self.assertEqual(old.state, "returned")
        new = self.draft(org, {"title": "Corrected draft"}, need=need)
        self.assertEqual(new.working_revision.number, 2)
        self.assertEqual(new.working_revision.pay_minimum, old.pay_minimum)
        self.assertEqual(old.title, "SYNTHETIC Welder")
        self.transition(org, new, "withdraw")
        self.assertEqual(Revision.objects.get(need=new, number=2).state, "withdrawn")

    def test_stale_or_wrong_revision_cannot_be_approved(self):
        org = self.org()
        need = self.draft(org)
        self.transition(org, need, "submit")
        self.transition(org, need, "review")
        version = need.revision
        with self.assertRaises(ValidationError):
            svc.revision_action(actor=self.admin, org_id=org.pk, need_id=need.pk, expected_org_revision=org.revision,
                                expected_need_revision=version, revision_id=uuid.uuid4(), action="approve", why=WHY)
        self.transition(org, need, "approve")
        with self.assertRaises(ValidationError):
            svc.revision_action(actor=self.admin, org_id=org.pk, need_id=need.pk, expected_org_revision=org.revision,
                                expected_need_revision=version, revision_id=need.effective_revision_id, action="approve", why=WHY)

    def test_approval_does_not_reactivate_held_or_closed_need(self):
        for final in ("hold", "closed"):
            org = self.org()
            need = self.approved_need(org)
            need = self.draft(org, {"worker_quantity": 5}, need=need)
            self.transition(org, need, "submit")
            self.transition(org, need, "review")
            svc.need_status(actor=self.admin, org_id=org.pk, need_id=need.pk, expected_org_revision=org.revision,
                            expected_need_revision=need.revision, action="hold" if final == "hold" else "close", why=WHY)
            self.transition(org, need, "approve")
            self.assertEqual(need.status, final)
            self.assertEqual(need.effective_revision.worker_quantity, 5)

    def test_manual_closure_requires_reason_and_records_actor_and_time(self):
        org = self.org()
        need = self.approved_need(org)
        with self.assertRaises(ValidationError):
            svc.need_status(actor=self.admin, org_id=org.pk, need_id=need.pk, expected_org_revision=org.revision,
                            expected_need_revision=need.revision, action="close", why="")
        svc.need_status(actor=self.manager, org_id=org.pk, need_id=need.pk, expected_org_revision=org.revision,
                        expected_need_revision=need.revision, action="close", why="Hiring completed after staff review")
        need.refresh_from_db()
        self.assertEqual(need.closed_by_id, self.manager.pk)
        self.assertIsNotNone(need.closed_at)
        self.assertEqual(need.closure_reason, "Hiring completed after staff review")
        org.refresh_from_db()
        with self.assertRaises(ValidationError):
            svc.need_status(actor=self.admin, org_id=org.pk, need_id=need.pk, expected_org_revision=org.revision,
                            expected_need_revision=need.revision, action="resume", why=WHY)

    def test_cancelled_need_cannot_resume_submission_or_review(self):
        org = self.org()
        need = self.draft(org)
        org.refresh_from_db()
        svc.need_status(actor=self.admin, org_id=org.pk, need_id=need.pk, expected_org_revision=org.revision,
                        expected_need_revision=need.revision, action="cancel", why=WHY)
        with self.assertRaises(ValidationError):
            self.transition(org, need, "submit")
        self.transition(org, need, "withdraw")
        self.assertEqual(need.status, "cancelled")


class CampaignAndHistoryTests(CustomerFixtures, TestCase):
    def test_campaign_can_have_zero_needs_and_requires_explicit_approval(self):
        org = self.org()
        campaign = self.campaign(org)
        self.assertEqual(campaign.workforce_needs.count(), 0)
        self.assertIsNone(campaign.approved_planning_version)
        with self.assertRaises(ValidationError):
            svc.campaign_action(actor=self.admin, org_id=org.pk, campaign_id=campaign.pk,
                                expected_org_revision=org.revision, expected_revision=campaign.revision, action="active", why=WHY)
        svc.campaign_action(actor=self.admin, org_id=org.pk, campaign_id=campaign.pk,
                            expected_org_revision=org.revision, expected_revision=campaign.revision, action="approve_activate", why=WHY)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "active")
        self.assertEqual(campaign.approved_planning_version, campaign.planning_version)

    def test_database_cannot_mark_unapproved_campaign_active(self):
        campaign = self.campaign(self.org())
        self.db_rejects(lambda: Campaign.objects.filter(pk=campaign.pk).update(status="active"))

    def test_multiple_needs_each_have_at_most_one_campaign(self):
        org = self.org()
        first, second = self.campaign(org), self.campaign(org, "SYNTHETIC second effort")
        a, b = self.draft(org, {}), self.draft(org, {})
        for need in (a, b):
            org.refresh_from_db()
            need.refresh_from_db()
            svc.assign_campaign(actor=self.admin, org_id=org.pk, need_id=need.pk, campaign_id=first.pk,
                                expected_org_revision=org.revision, expected_need_revision=need.revision, why=WHY)
        a.refresh_from_db()
        org.refresh_from_db()
        svc.assign_campaign(actor=self.admin, org_id=org.pk, need_id=a.pk, campaign_id=second.pk,
                            expected_org_revision=org.revision, expected_need_revision=a.revision, why=WHY)
        self.assertEqual(first.workforce_needs.count(), 1)
        self.assertEqual(second.workforce_needs.count(), 1)
        self.assertEqual(Need.objects.count(), 2)

    def test_campaign_scope_change_invalidates_approval_without_approving_need(self):
        org = self.org()
        campaign = self.campaign(org)
        svc.campaign_action(actor=self.admin, org_id=org.pk, campaign_id=campaign.pk,
                            expected_org_revision=org.revision, expected_revision=campaign.revision, action="approve_activate", why=WHY)
        org.refresh_from_db()
        need = self.draft(org, {})
        org.refresh_from_db()
        svc.assign_campaign(actor=self.admin, org_id=org.pk, need_id=need.pk, campaign_id=campaign.pk,
                            expected_org_revision=org.revision, expected_need_revision=need.revision, why=WHY)
        campaign.refresh_from_db()
        need.refresh_from_db()
        self.assertEqual(campaign.status, "hold")
        self.assertIsNone(campaign.approved_planning_version)
        self.assertEqual(need.status, "draft")
        self.assertIsNone(need.effective_revision_id)

    def test_campaign_planning_edit_clears_approval_and_requires_new_review(self):
        org = self.org()
        campaign = self.campaign(org)
        svc.campaign_action(actor=self.admin, org_id=org.pk, campaign_id=campaign.pk,
                            expected_org_revision=org.revision, expected_revision=campaign.revision, action="approve_activate", why=WHY)
        org.refresh_from_db()
        campaign.refresh_from_db()
        old = campaign.planning_version
        svc.save_campaign(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision, campaign_id=campaign.pk,
                          expected_revision=campaign.revision, data={"objectives": "Changed reviewed scope"}, why=WHY)
        campaign.refresh_from_db()
        self.assertEqual(campaign.planning_version, old + 1)
        self.assertEqual(campaign.status, "hold")
        self.assertIsNone(campaign.approved_at)

    def test_database_rejects_silent_approved_plan_overwrite(self):
        org = self.org()
        campaign = self.campaign(org)
        svc.campaign_action(actor=self.admin, org_id=org.pk, campaign_id=campaign.pk,
                            expected_org_revision=org.revision, expected_revision=campaign.revision, action="approve_activate", why=WHY)
        self.db_rejects(lambda: Campaign.objects.filter(pk=campaign.pk).update(objectives="Unreviewed change"))

    def test_cross_org_campaign_association_rejected_by_service_and_database(self):
        a, b = self.org(), self.org()
        need, campaign = self.draft(a, {}), self.campaign(b)
        a.refresh_from_db()
        with self.assertRaises(ValidationError):
            svc.assign_campaign(actor=self.admin, org_id=a.pk, need_id=need.pk, campaign_id=campaign.pk,
                                expected_org_revision=a.revision, expected_need_revision=need.revision, why=WHY)
        self.db_rejects(lambda: Need.objects.filter(pk=need.pk).update(campaign=campaign))

    def test_stale_campaign_approval_cannot_approve_new_planning_details(self):
        org = self.org()
        campaign = self.campaign(org)
        old = campaign.revision
        svc.save_campaign(actor=self.admin, org_id=org.pk, expected_org_revision=org.revision, campaign_id=campaign.pk,
                          expected_revision=old, data={"name": "SYNTHETIC updated campaign"}, why=WHY)
        org.refresh_from_db()
        with self.assertRaises(ValidationError):
            svc.campaign_action(actor=self.admin, org_id=org.pk, campaign_id=campaign.pk,
                                expected_org_revision=org.revision, expected_revision=old, action="approve_activate", why=WHY)

    def test_audit_scopes_use_one_log_and_do_not_duplicate_hiring_payloads(self):
        org = self.org()
        need = self.approved_need(org)
        scopes = CustomerAuditScope.objects.filter(organization=org, workforce_need=need).select_related("audit")
        self.assertTrue(scopes.exists())
        for scope in scopes:
            self.assertEqual(scope.audit.source, "customer_management")
            self.assertEqual(scope.audit.actor_id, self.admin.pk)
            for snapshot in (scope.audit.old_data, scope.audit.new_data):
                if snapshot:
                    self.assertNotIn("pay_minimum", snapshot)
                    self.assertNotIn("description", snapshot)
                    self.assertNotIn("password", snapshot)
                    self.assertNotIn("token", snapshot)

    def test_organization_history_never_inherits_connected_company_records(self):
        a, b = self.org(), self.org()
        a.refresh_from_db()
        svc.save_relationship(actor=self.admin, org_id=a.pk, target_id=b.pk, kind="connected", active=True,
                              expected_org_revision=a.revision, why=WHY)
        self.child(b, "contact", {"first_name": "SYNTHETIC private other-company contact"})
        own = CustomerAuditScope.objects.filter(organization=a, audit__action="customer.contact.created")
        self.assertFalse(own.exists())
