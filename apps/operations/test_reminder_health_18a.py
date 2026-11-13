import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import AuditLog
from .models import TaskReminderJob, TaskReminderSchedulerState
from .reminders import run_tick
from .test_reminders_18a import Fixtures, clock


@override_settings(BTW_TASK_REMINDERS_ENABLED=True)
class ReminderHealth18ATests(Fixtures, TestCase):
    def setUp(self):
        self.make_fixture()
        self.url = reverse('operations:reminder-status')

    def login(self, user):
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')

    def test_only_current_active_admin_can_inspect_company_scheduler(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        for user in (self.manager, self.recruiter, self.staff, self.other):
            self.login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        type(self.admin).objects.filter(pk=self.admin.pk).update(is_active=False)
        self.assertIn(self.client.get(self.url).status_code, (302, 403))

    def test_read_does_not_plan_deliver_or_write_audit_and_exposes_no_internal_state(self):
        self.login(self.admin)
        before = (TaskReminderJob.objects.count(), TaskReminderSchedulerState.objects.count(), AuditLog.objects.count())
        with patch.dict('os.environ', {'RENDER_GIT_COMMIT': 'a' * 40}):
            response = self.client.get(self.url)
        value = response.json()
        self.assertEqual(value['commit'], 'a' * 40)
        self.assertFalse(value['automatic_healthy'])
        self.assertNotIn('state', value)
        self.assertNotIn('last_error', value)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(before, (TaskReminderJob.objects.count(), TaskReminderSchedulerState.objects.count(), AuditLog.objects.count()))

    def test_manual_tick_is_not_operational_proof_and_expired_heartbeat_is_unhealthy(self):
        from datetime import timedelta
        self.login(self.admin)
        run_tick(now=clock())
        with patch('django.utils.timezone.now', return_value=clock()):
            self.assertFalse(self.client.get(self.url).json()['automatic_healthy'])
        run_tick(now=clock(), automatic=True, worker_id='SAMPLE automatic worker')
        with patch('django.utils.timezone.now', return_value=clock()):
            self.assertTrue(self.client.get(self.url).json()['automatic_healthy'])
        with patch('django.utils.timezone.now', return_value=clock()+timedelta(seconds=151)):
            self.assertFalse(self.client.get(self.url).json()['automatic_healthy'])

    def test_cli_and_http_share_health_logic_but_only_cli_includes_execution_details(self):
        self.login(self.admin)
        run_tick(now=clock(), automatic=True)
        output = StringIO()
        with patch('django.utils.timezone.now', return_value=clock()):
            call_command('run_task_reminders', status=True, stdout=output)
            http = self.client.get(self.url).json()
        cli = json.loads(output.getvalue())
        self.assertEqual(cli['automatic_healthy'], http['automatic_healthy'])
        self.assertEqual(cli['jobs_by_state'], http['jobs_by_state'])
        self.assertIn('state', cli)
        self.assertNotIn('state', http)

    @override_settings(BTW_TASK_REMINDERS_ENABLED=False)
    def test_admin_can_diagnose_disabled_activation_without_starting_it(self):
        self.login(self.admin)
        value = self.client.get(self.url).json()
        self.assertFalse(value['enabled'])
        self.assertFalse(value['automatic_healthy'])
        self.assertFalse(TaskReminderSchedulerState.objects.exists())
