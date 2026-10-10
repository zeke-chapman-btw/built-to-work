from datetime import timedelta
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.events.models import Event, OfflineEventPreparation, OfflineTicketRange, SyncOutboxItem, TrailerInstance
from apps.events.offline import check_offline_readiness, mark_sync_failed, queue_sync, reserve_ticket_number


class OfflineFoundationTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(
            name="Offline Foundation Event",
            status=Event.Status.UPCOMING,
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(hours=5),
        )
        self.trailer = TrailerInstance.objects.create(identity="TRAILER-TEST", display_name="Test trailer")
        self.preparation = OfflineEventPreparation.objects.create(
            event=self.event,
            trailer=self.trailer,
            status=OfflineEventPreparation.Status.ACTIVE,
            sync_identity="sync-test",
        )

    def test_readiness_requires_approved_consent_and_ticket_ranges(self):
        readiness = check_offline_readiness(self.preparation)
        self.assertFalse(readiness.ready)
        self.assertIn("approved consent", " ".join(readiness.failures).lower())

    def test_ticket_ranges_use_primary_then_backup(self):
        OfflineTicketRange.objects.create(
            preparation=self.preparation, kind="primary", start_number=1000000000,
            end_number=1000000001, next_number=1000000000,
        )
        OfflineTicketRange.objects.create(
            preparation=self.preparation, kind="backup", start_number=2000000000,
            end_number=2000000000, next_number=2000000000,
        )
        self.assertEqual(reserve_ticket_number(self.preparation), "1000000000")
        self.assertEqual(reserve_ticket_number(self.preparation), "1000000001")
        self.assertEqual(reserve_ticket_number(self.preparation), "2000000000")
        with self.assertRaises(ValidationError):
            reserve_ticket_number(self.preparation)

    def test_sync_outbox_is_idempotent_and_retries(self):
        first, created = queue_sync(preparation=self.preparation, operation_key="registration-1", operation_type="registration", payload={"ticket": "1000000000"})
        again, created_again = queue_sync(preparation=self.preparation, operation_key="registration-1", operation_type="registration", payload={"ticket": "1000000000"})
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(SyncOutboxItem.objects.filter(operation_key="registration-1").count(), 1)
        mark_sync_failed(first, "temporary network error")
        first.refresh_from_db()
        self.assertEqual(first.status, SyncOutboxItem.Status.PENDING)
