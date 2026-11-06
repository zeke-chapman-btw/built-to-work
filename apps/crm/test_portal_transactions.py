"""Real autocommit requests must establish their own PostgreSQL policy transactions."""
import uuid
from datetime import timedelta
from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.customers.test_portal import BACKEND
from apps.recruiting import employer
from apps.recruiting.test_employer import EmployerFixtures
from apps.recruiting.models import RecruitingStage

from .models import Interaction


@skipUnless(connection.vendor == "postgresql", "PostgreSQL autocommit row locks; immutable invitation fixtures require PostgreSQL TRUNCATE cleanup")
class EmployerRequestTransactionTests(EmployerFixtures, TransactionTestCase):
    def setUp(self):
        RecruitingStage.objects.get_or_create(pk="identified", defaults={"label":"Identified", "semantic":"active"})
        self.employer_setup()
        self.grant = self.release()
        self.client.force_login(self.customer, backend=BACKEND)

    def activity_payload(self):
        return {"operation_id":str(uuid.uuid4()), "activity_type":"call", "summary":"SYNTHETIC autocommit request",
            "occurred_at":(timezone.now()-timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"), "outcome":"SYNTHETIC conversation"}

    def test_entry_detail_correction_and_archive_work_without_an_enclosing_test_transaction(self):
        self.assertFalse(connection.in_atomic_block)
        new = reverse("crm:employer-new", args=[self.grant.pk])
        self.assertContains(self.client.get(new), "Log interaction")
        data = self.activity_payload()
        self.assertEqual(self.client.post(new, data).status_code, 302)
        self.assertEqual(self.client.post(new, data).status_code, 302)
        row = Interaction.objects.get()
        detail = reverse("crm:employer-detail", args=[self.grant.pk,row.pk])
        self.assertContains(self.client.get(detail), row.summary)
        self.assertFalse(connection.in_atomic_block)
        data.update(revision=1, reason="SYNTHETIC actual request correction", summary="SYNTHETIC corrected request")
        self.assertEqual(self.client.post(reverse("crm:employer-correct", args=[self.grant.pk,row.pk]), data).status_code,302)
        self.assertContains(self.client.get(detail), "SYNTHETIC corrected request")
        self.assertEqual(self.client.post(reverse("crm:employer-archive",args=[self.grant.pk,row.pk,"archive"]),
            {"revision":2,"reason":"SYNTHETIC retained archive"}).status_code,302)
        row.refresh_from_db();self.assertIsNotNone(row.archived_at)
        self.assertEqual(row.history.count(),3)
        self.assertFalse(connection.in_atomic_block)

    def test_withdrawal_denies_direct_requests_in_autocommit(self):
        self.assertFalse(connection.in_atomic_block)
        new = reverse("crm:employer-new", args=[self.grant.pk])
        self.assertEqual(self.client.post(new,self.activity_payload()).status_code,302)
        row=Interaction.objects.get()
        employer.revoke(actor=self.admin,disclosure_id=self.grant.pk,expected_revision=self.grant.revision,
            why="SYNTHETIC autocommit disclosure revocation")
        self.assertEqual(self.client.get(new).status_code,403)
        self.assertEqual(self.client.get(reverse("crm:employer-detail",args=[self.grant.pk,row.pk])).status_code,403)
        self.assertEqual(self.client.post(new,self.activity_payload()).status_code,403)
        self.assertEqual(Interaction.objects.count(),1)
