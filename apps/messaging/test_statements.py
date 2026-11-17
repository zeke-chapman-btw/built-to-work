from unittest.mock import patch

from django.core import mail

from apps.reporting import payroll
from apps.reporting.test_metrics import ReportingFixtures

from . import statements, store
from .models import StatementNotice


class StatementNoticeTests(ReportingFixtures):
    def approve(self):
        self.day()
        payroll.compile_payroll(actor=self.admin, starts_on=self.monday, why="SYNTHETIC")
        with self.captureOnCommitCallbacks(execute=True):
            payroll.approve_payroll(actor=self.admin, starts_on=self.monday, expected_revision=1, why="SYNTHETIC")

    def test_off_by_default_sends_nothing(self):
        self.approve()
        self.assertFalse(StatementNotice.objects.exists())

    def test_link_only_email_when_turned_on(self):
        self.worker.email = "worker@example.test"
        self.worker.save()
        store.save(key="sending", actor=self.admin, values={"enabled": True, "since": "2026-01-01T00:00:00+00:00"})
        statements.set_enabled(actor=self.admin, on=True)
        config = {"from_address": "noreply@example.test", "reply_to": [], "source": "app"}
        with patch("apps.messaging.store.email_settings", return_value=config), \
             patch("apps.messaging.store.smtp_connection", return_value=mail.get_connection()):
            self.approve()
        notice = StatementNotice.objects.get()
        self.assertEqual((notice.status, notice.address), ("sent", "worker@example.test"))
        sent = mail.outbox[-1]
        self.assertEqual(sent.subject, "Your earnings statement is ready")
        self.assertNotIn("$", sent.body)
        self.assertIn("doesn't include any amounts", sent.body)
