"""Populated 18A receipts, assignments and audit survive the additive 18B schema."""
from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class PopulatedReminderMigration18BTests(TransactionTestCase):
    def test_populated_18a_history_and_policy_survive(self):
        old_target = [('operations', '0011_event_note_category')]
        new_target = [('operations', '0013_customer_review_reminders_18b')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(old_target)
            old = executor.loader.project_state(old_target).apps
            user = old.get_model('accounts', 'User').objects.create(username='SAMPLE migration', is_staff=True)
            profile = old.get_model('staffing', 'StaffProfile').objects.create(first_name='SAMPLE', last_name='History', user=user)
            task = old.get_model('operations', 'Task').objects.create(title='SAMPLE unchanged deadline',
                assigned_staff=profile, due_date=date(2026, 11, 16), created_by=user)
            policy = old.get_model('operations', 'TaskReminderPolicy').objects.create(key='company',
                advance_enabled=False, activity_enabled=True, completion_enabled=False)
            when = timezone.now()
            job = old.get_model('operations', 'TaskReminderJob').objects.create(key='SAMPLE-old-summary',
                kind='summary', recipient=user, state='delivered', local_date=when.date(),
                scheduled_for=when, available_at=when, finished_at=when, attempts=1,
                attempt_history=[{'state': 'delivered', 'worker': 'SAMPLE-before-18b'}])
            item = old.get_model('operations', 'TaskReminderItem').objects.create(job=job, task=task,
                kind='due', dedupe_key='SAMPLE-existing-coverage', fingerprint='SAMPLE-existing-source')
            notice = old.get_model('operations', 'TaskNotification').objects.create(task=None, recipient=user,
                reminder_job=job, kind='summary', message='Morning summary', read_at=when)
            content_type = old.get_model('contenttypes', 'ContentType').objects.get(app_label='operations', model='task')
            audit = old.get_model('core', 'AuditLog').objects.create(actor=user, action='operations.task.created',
                content_type=content_type, object_id=str(task.pk), source='staff', new_data={'title': task.title})
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            new = executor.loader.project_state(new_target).apps
            preserved_task = new.get_model('operations', 'Task').objects.get(pk=task.pk)
            self.assertEqual(preserved_task.due_date, task.due_date)
            self.assertEqual(preserved_task.assigned_staff_id, profile.pk)
            preserved_job = new.get_model('operations', 'TaskReminderJob').objects.get(pk=job.pk)
            self.assertEqual(preserved_job.state, 'delivered')
            self.assertEqual(preserved_job.attempt_history, job.attempt_history)
            self.assertEqual(preserved_job.source_ref, {})
            self.assertIsNone(preserved_job.summary_id)
            preserved_item = new.get_model('operations', 'TaskReminderItem').objects.get(pk=item.pk)
            self.assertEqual(preserved_item.task_id, task.pk)
            self.assertEqual(preserved_item.dedupe_key, item.dedupe_key)
            self.assertEqual(preserved_item.source_ref, {})
            self.assertEqual(new.get_model('operations', 'TaskNotification').objects.get(pk=notice.pk).read_at, when)
            self.assertFalse(new.get_model('operations', 'TaskReminderPolicy').objects.get(pk=policy.pk).advance_enabled)
            self.assertEqual(new.get_model('core', 'AuditLog').objects.get(pk=audit.pk).new_data, audit.new_data)
        finally:
            MigrationExecutor(connection).migrate(new_target)
