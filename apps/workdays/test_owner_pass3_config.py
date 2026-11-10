from datetime import UTC, datetime
from unittest.mock import patch
from django.test import TestCase
from apps.core.models import AuditLog
from . import services
from .forms import RateForm, RoleForm, RevisionForm


class CompensationConfigurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Reuse the authoritative fixture without importing its TestCase into
        # module discovery or rerunning its complete suite here.
        from .tests import Workday9ETests
        Workday9ETests.setUpTestData.__func__(cls)

    def test_only_configuration_forms_remove_free_text_reason(self):
        self.assertNotIn('reason', RateForm().fields)
        self.assertNotIn('reason', RoleForm().fields)
        self.assertIn('reason', RevisionForm().fields)
        self.assertFalse(RevisionForm({'revision': 'new'}).is_valid())

    def test_dated_rate_without_reason_preserves_approved_pay(self):
        with patch('apps.workdays.services.timezone.now', return_value=datetime(2030, 7, 1, 14, tzinfo=UTC)):
            workday, form = services.checkin(actor=self.employee, event_id=self.event.pk,
                data={'work_date': '2030-07-01', 'role': self.role.pk})
        self.assertIsNotNone(workday, form.errors)
        approved, form = services.workday_action(actor=self.manager, record_id=workday.pk,
            data={'revision': workday.updated_at.isoformat(), 'reason': 'Synthetic approval after review.'}, action='approve')
        self.assertIsNotNone(approved, form.errors)
        before = services.snap(approved)
        rate, form = services.save_rate(actor=self.admin, data={
            'kind': 'role', 'role': self.role.pk, 'amount': '225.00',
            'effective_from': '2030-07-02', 'revision': self.rate.updated_at.isoformat()})
        self.assertIsNotNone(rate, form.errors)
        self.assertIn('recorded automatically', rate.reason)
        approved.refresh_from_db()
        self.assertEqual(services.snap(approved), before)
        self.assertEqual(approved.pay_amount, 100)
        self.assertEqual(approved.pay_rate_id, self.rate.pk)
        self.assertEqual(services.rate_for('role', approved.work_date, self.role).pk, self.rate.pk)
        self.assertTrue(AuditLog.objects.filter(action='workdays.rate.version', object_id=str(rate.pk), actor=self.admin).exists())

    def test_role_without_reason_is_audited_and_does_not_rewrite_names(self):
        old_name = self.role.name
        saved, form = services.save_role(actor=self.admin, role_id=self.role.pk,
            data={'name': 'Updated compensation role', 'is_active': True, 'per_diem_eligible': 'no', 'revision': self.role.updated_at.isoformat()})
        self.assertIsNotNone(saved, form.errors)
        log = AuditLog.objects.get(action='workdays.role.changed', object_id=str(saved.pk))
        self.assertEqual(log.old_data['name'], old_name)
        self.assertEqual(log.new_data['name'], saved.name)
        self.assertIn('recorded automatically', log.reason)
