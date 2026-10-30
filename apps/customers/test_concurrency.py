"""Real PostgreSQL races; each worker owns a separate connection/transaction."""
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from django.core.exceptions import ValidationError
from django.db import connection, connections, close_old_connections
from django.test import TransactionTestCase
from apps.customers import services as svc
from apps.customers.models import CustomerOrganization as Org, CustomerMembership as Member, CustomerIdentity, Campaign, WorkforceNeed as Need
from apps.customers.test_domain import CustomerFixtures, WHY


@skipUnless(connection.vendor == "postgresql", "Requires real PostgreSQL independent transactions")
class CustomerConcurrencyTests(CustomerFixtures, TransactionTestCase):
    def setUp(self):
        self.setUpTestData()

    def race(self, first, second):
        barrier = threading.Barrier(2)
        def worker(fn):
            close_old_connections()
            try:
                connection.ensure_connection()
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout='8s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                try:
                    fn()
                    outcome = "saved"
                except ValidationError:
                    outcome = "rejected"
                return pid, outcome
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(worker, first), pool.submit(worker, second)
            results = [a.result(timeout=25), b.result(timeout=25)]
        self.assertNotEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(r[1] for r in results), ["rejected", "saved"])

    def test_last_available_seat_is_consumed_once(self):
        org = self.org()
        self.membership(org)
        self.plan(org, member_limit=1)
        revision = org.revision
        def add(actor, email):
            return lambda: svc.save_membership(actor=actor, org_id=org.pk, expected_org_revision=revision, role="member", identity_data={"first_name": "SYNTHETIC", "email": email}, why=WHY)
        self.race(add(self.admin, "race-a@example.test"), add(self.manager, "race-b@example.test"))
        self.assertEqual(svc.seat_usage(org)["member"], 1)

    def test_competing_primary_transfers_preserve_one_enabled_lead(self):
        org = self.org()
        self.membership(org)
        a, b = self.membership(org, role="member"), self.membership(org, role="member")
        revision = org.revision
        def transfer(actor, target):
            return lambda: svc.transfer_primary(actor=actor, org_id=org.pk, expected_org_revision=revision, target_id=target.pk, previous_role="member", why=WHY)
        self.race(transfer(self.admin, a), transfer(self.manager, b))
        org.refresh_from_db()
        self.assertIn(org.primary_lead_id, [a.pk, b.pk])
        self.assertEqual(org.primary_lead.role, "lead")
        self.assertEqual(org.primary_lead.state, "enabled")
        self.assertEqual(Member.objects.filter(organization=org, role="lead", state="enabled").count(), 1)

    def test_cross_actor_cross_org_email_provisioning_is_not_merged(self):
        a, b = self.org(), self.org()
        revisions = {a.pk: a.revision, b.pk: b.revision}
        def provision(actor, org):
            return lambda: svc.save_membership(actor=actor, org_id=org.pk, expected_org_revision=revisions[org.pk], role="lead", designate_primary=True, identity_data={"first_name": "SYNTHETIC", "email": "same-race@example.test"}, why=WHY)
        self.race(provision(self.admin, a), provision(self.manager, b))
        self.assertEqual(CustomerIdentity.objects.filter(email="same-race@example.test").count(), 1)
        self.assertEqual(Org.objects.filter(pk__in=[a.pk, b.pk], provisioned=True).count(), 1)
        self.assertEqual(Member.objects.count(), 1)

    def test_stale_policy_updates_fail_instead_of_overwriting(self):
        org = self.org()
        revision, policy_revision = org.revision, org.access_plan.revision
        def policy(actor, value):
            return lambda: svc.save_access_plan(actor=actor, org_id=org.pk, expected_org_revision=revision, expected_revision=policy_revision, data={"member_limit": value}, why=WHY)
        self.race(policy(self.admin, 7), policy(self.manager, 9))
        org.refresh_from_db()
        self.assertIn(org.access_plan.member_limit, [7, 9])
        self.assertEqual(org.access_plan.revision, policy_revision+1)

    def test_competing_approvals_freeze_actual_revision_once(self):
        org = self.org()
        need = self.draft(org)
        self.transition(org, need, "submit")
        self.transition(org, need, "review")
        org_revision, need_revision, working_id = org.revision, need.revision, need.working_revision_id
        def approve(actor):
            return lambda: svc.revision_action(actor=actor, org_id=org.pk, need_id=need.pk, expected_org_revision=org_revision, expected_need_revision=need_revision, revision_id=working_id, action="approve", why=WHY)
        self.race(approve(self.admin), approve(self.manager))
        need.refresh_from_db()
        self.assertEqual(need.effective_revision_id, working_id)
        self.assertIsNone(need.working_revision_id)
        self.assertEqual(need.audit_scopes.filter(audit__action="customer.need.revision_approve").count(), 1)

    def test_competing_campaign_reassignment_has_one_winner(self):
        org = self.org()
        a, b, c = self.campaign(org, "SYNTHETIC A"), self.campaign(org, "SYNTHETIC B"), self.campaign(org, "SYNTHETIC C")
        need = self.draft(org, {})
        org.refresh_from_db()
        svc.assign_campaign(actor=self.admin, org_id=org.pk, need_id=need.pk, campaign_id=a.pk, expected_org_revision=org.revision, expected_need_revision=need.revision, why=WHY)
        org.refresh_from_db()
        need.refresh_from_db()
        org_revision, need_revision = org.revision, need.revision
        def assign(actor, target):
            return lambda: svc.assign_campaign(actor=actor, org_id=org.pk, need_id=need.pk, campaign_id=target.pk, expected_org_revision=org_revision, expected_need_revision=need_revision, why=WHY)
        self.race(assign(self.admin, b), assign(self.manager, c))
        need.refresh_from_db()
        self.assertIn(need.campaign_id, [b.pk, c.pk])
        self.assertEqual(Need.objects.filter(campaign_id__in=[b.pk, c.pk]).count(), 1)
        self.assertEqual(a.workforce_needs.count(), 0)
