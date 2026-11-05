import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.customers.models import CustomerContact
from apps.operations.models import Task
from apps.recruiting import employer
from apps.recruiting.models import RecruitingStage
from apps.recruiting.test_employer import EmployerFixtures
from apps.staffing.models import StaffProfile

from . import services
from .models import ContactRelationship, Interaction, InteractionTaskLink


@skipUnless(connection.vendor == "postgresql", "PostgreSQL CRM locking and revocation concurrency")
class CrmConcurrencyTests(EmployerFixtures, TransactionTestCase):
    def setUp(self):
        RecruitingStage.objects.get_or_create(pk="identified", defaults={"label": "Identified", "semantic": "active"})
        self.employer_setup()
        self.grant = self.release()
        self.stamp = timezone.now()

    def user(self, pk):
        return get_user_model().objects.get(pk=pk)

    def together(self, actions):
        barrier = Barrier(len(actions))

        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=20)
                try:
                    return {"ok": action()}
                except (PermissionDenied, ValidationError) as error:
                    return {"rejected": str(error)}
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(run, actions))

    def record(self, operation=None):
        return services.create_employer(actor=self.user(self.customer.pk), organization_id=self.organization.pk,
            disclosure_id=self.grant.pk, data={"activity_type": "call", "summary": "SYNTHETIC concurrent call", "occurred_at": self.stamp},
            operation_id=operation or uuid.uuid4())

    def test_duplicate_employer_submissions_are_one_attributed_activity(self):
        operation = uuid.uuid4()
        result = self.together([lambda: str(self.record(operation).pk), lambda: str(self.record(operation).pk)])
        self.assertTrue(all("ok" in item for item in result), result)
        self.assertEqual(len({item["ok"] for item in result}), 1)
        self.assertEqual(Interaction.objects.count(), 1)
        self.assertEqual(Interaction.objects.get().history.count(), 1)

    def test_concurrent_corrections_preserve_one_winner_and_original_revision(self):
        row = self.record()

        def correct(label):
            return services.correct(actor=self.user(self.customer.pk), organization_id=self.organization.pk,
                interaction_id=row.pk, expected_revision=1, data={"summary": label}, why="SYNTHETIC concurrent correction").summary

        result = self.together([lambda: correct("SYNTHETIC correction A"), lambda: correct("SYNTHETIC correction B")])
        self.assertEqual(sum("ok" in item for item in result), 1)
        self.assertEqual(sum("rejected" in item for item in result), 1)
        row.refresh_from_db()
        self.assertEqual(row.revision, 2)
        self.assertEqual(row.history.count(), 2)
        self.assertEqual(row.history.first().snapshot["summary"], "SYNTHETIC concurrent call")

    def test_disclosure_revocation_serializes_with_activity_creation_and_read(self):
        def revoke():
            return str(employer.revoke(actor=self.user(self.admin.pk), disclosure_id=self.grant.pk,
                expected_revision=self.grant.revision, why="SYNTHETIC concurrent disclosure withdrawal").pk)

        self.together([revoke, lambda: str(self.record().pk)])
        self.assertLessEqual(Interaction.objects.count(), 1)
        with self.assertRaises(PermissionDenied):
            self.record()
        with self.assertRaises(PermissionDenied):
            services.employer_history(self.customer, self.organization.pk, self.grant.pk)

    def test_disclosure_revocation_serializes_with_history_read(self):
        self.record()

        def read():
            return len(services.employer_history(self.user(self.customer.pk), self.organization.pk, self.grant.pk))

        def revoke():
            return str(employer.revoke(actor=self.user(self.admin.pk), disclosure_id=self.grant.pk,
                expected_revision=self.grant.revision, why="SYNTHETIC revoke during read").pk)

        result = self.together([read, revoke])
        self.assertEqual(len(result), 2)
        with self.assertRaises(PermissionDenied):
            read()
        self.assertEqual(Interaction.objects.count(), 1)

    def test_concurrent_primary_contact_changes_keep_one_primary_and_audit(self):
        contacts = [CustomerContact.objects.create(organization=self.organization, first_name="SYNTHETIC", last_name=label)
                    for label in ("Primary A", "Primary B")]

        def primary(contact):
            return str(services.save_contact_relationship(actor=self.user(self.admin.pk), contact_id=contact.pk,
                data={"is_primary": True}, expected_revision=0, why="SYNTHETIC concurrent explicit designation").pk)

        result = self.together([lambda: primary(contacts[0]), lambda: primary(contacts[1])])
        self.assertTrue(all("ok" in item for item in result), result)
        self.assertEqual(ContactRelationship.objects.count(), 2)
        self.organization.refresh_from_db()
        self.assertIn(self.organization.primary_contact_id, [item.pk for item in contacts])
        self.assertEqual(sum(item.is_primary for item in ContactRelationship.objects.select_related("organization")), 1)

    def test_duplicate_explicit_followups_create_one_existing_task(self):
        row = services.create_internal(actor=self.admin, participant_id=self.person.pk,
            data={"activity_type": "call", "summary": "SYNTHETIC follow-up source", "occurred_at": self.stamp}, operation_id=uuid.uuid4())
        staff = StaffProfile.objects.create(user=self.admin, first_name="SYNTHETIC", last_name="Task owner")
        operation = uuid.uuid4()
        data = {"title": "SYNTHETIC concurrent follow-up", "assigned_staff": str(staff.pk), "priority": "normal", "revision": "new"}

        def create():
            task, form = services.create_followup(actor=self.user(self.admin.pk), interaction_id=row.pk,
                data=data, operation_id=operation)
            return str(task.pk) if task else str(form.errors)

        result = self.together([create, create])
        self.assertTrue(all("ok" in item for item in result), result)
        self.assertEqual(len({item["ok"] for item in result}), 1)
        self.assertEqual(Task.objects.count(), 1)
        self.assertEqual(InteractionTaskLink.objects.count(), 1)
