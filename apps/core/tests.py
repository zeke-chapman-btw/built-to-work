from django.test import TestCase

from apps.accounts.models import User
from apps.core.audit import record_audit
from apps.core.models import AuditLog


class EndpointTests(TestCase):
    def test_home_returns_json(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "built-to-work"})

    def test_health_returns_json(self):
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class AuditLogTests(TestCase):
    def test_record_audit_persists_explicit_event(self):
        actor = User.objects.create_user(username="auditor", password="test-password")
        subject = User.objects.create_user(username="subject", password="test-password")

        event = record_audit(
            actor=actor,
            action="updated",
            instance=subject,
            source="test",
            old_data={"is_active": True},
            new_data={"is_active": False},
            reason="Account disabled",
        )

        stored_event = AuditLog.objects.get(pk=event.pk)
        self.assertEqual(stored_event.actor, actor)
        self.assertEqual(stored_event.content_object, subject)
        self.assertEqual(stored_event.object_id, str(subject.pk))
        self.assertEqual(stored_event.old_data, {"is_active": True})
        self.assertEqual(stored_event.new_data, {"is_active": False})
        self.assertEqual(stored_event.reason, "Account disabled")
        self.assertEqual(stored_event.source, "test")
        self.assertIsNotNone(stored_event.timestamp)