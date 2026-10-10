import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import RequestFactory, TestCase, override_settings
from .sync import record_sync_failure
from .sync_views import sync_ingest


class SyncIngestTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(BTW_SYNC_API_TOKEN="secret")
    def test_missing_credentials_are_rejected(self):
        request = self.factory.post("/events/sync/ingest/", data=json.dumps({}), content_type="application/json")
        response = sync_ingest(request)
        self.assertEqual(response.status_code, 401)

    @override_settings(BTW_SYNC_API_TOKEN="secret")
    def test_invalid_envelope_is_rejected(self):
        request = self.factory.post("/events/sync/ingest/", data=json.dumps({}), content_type="application/json", HTTP_AUTHORIZATION="Bearer secret")
        response = sync_ingest(request)
        self.assertEqual(response.status_code, 400)

    @override_settings(BTW_SYNC_API_TOKEN="")
    def test_unconfigured_endpoint_fails_closed(self):
        request = self.factory.post("/events/sync/ingest/", data=json.dumps({}), content_type="application/json")
        response = sync_ingest(request)
        self.assertEqual(response.status_code, 503)

    def test_retry_backoff_is_bounded(self):
        item = Mock(attempts=12)
        manager = Mock()
        manager.select_for_update.return_value.get.return_value = item
        with patch("apps.events.sync.SyncOutboxItem.objects", manager), patch("apps.events.sync.timezone.now", return_value=__import__("django.utils.timezone", fromlist=["now"]).now()):
            result = record_sync_failure("id", "temporary", max_delay_seconds=60)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.last_error, "temporary")
        self.assertEqual((item.next_attempt_at - result.next_attempt_at).total_seconds(), 0)

    @override_settings(BTW_SYNC_API_TOKEN="secret")
    def test_duplicate_operation_is_acknowledged_without_reimport(self):
        existing = SimpleNamespace(status="accepted")
        receipt_manager = Mock()
        receipt_manager.select_for_update.return_value.filter.return_value.first.return_value = existing
        trailer_manager = Mock()
        trailer_manager.filter.return_value.first.return_value = SimpleNamespace(identity="TRAILER-1")
        event_manager = Mock()
        event_manager.filter.return_value.first.return_value = SimpleNamespace(id="event")
        body = {"operation_key": "registration:1", "trailer_identity": "TRAILER-1", "event_id": "00000000-0000-0000-0000-000000000001", "payload": {}}
        request = self.factory.post("/events/sync/ingest/", data=json.dumps(body), content_type="application/json", HTTP_AUTHORIZATION="Bearer secret")
        with patch("apps.events.sync_views.CentralSyncReceipt.objects", receipt_manager), patch("apps.events.sync_views.TrailerInstance.objects", trailer_manager), patch("apps.events.sync_views.Event.objects", event_manager):
            response = sync_ingest(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)["duplicate"])
