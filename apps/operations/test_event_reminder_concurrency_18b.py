"""Meaningful PostgreSQL serialization and recovery of source-backed delivery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event as ThreadEvent

from django.contrib.auth import get_user_model
from django.db import connection, transaction, close_old_connections
from django.test import TransactionTestCase, override_settings

from apps.staffing.models import StaffingPlan
from .execution_models import EventExecution
from .models import TaskReminderJob, TaskReminderItem, TaskNotification
from .event_reminder_planning import plan_sources
from .reminders import run_tick, claim_job, deliver_job
from .test_reminders_18a import Fixtures, clock


@override_settings(BTW_TASK_REMINDERS_ENABLED=True, BTW_EVENT_APPROVAL_REMINDERS_ENABLED=True)
class SourceConcurrency18BTests(Fixtures, TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL source locking test')
        self.make_fixture()
        self.event.start_at, self.event.end_at = clock(20, 9), clock(20, 17)
        self.event.save()
        self.execution = EventExecution.objects.create(event=self.event, primary_manager=self.profiles[self.admin.pk])
        StaffingPlan.objects.create(event=self.event, required_staff=1)

    def test_concurrent_claims_and_repeated_ticks_deliver_one_source_receipt(self):
        plan_sources(clock())
        barrier = Barrier(2)
        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                job = claim_job(now=clock(), worker_id=str(index))
                return deliver_job(job, now=clock(), worker_id=str(index)) if job else 'none'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(worker, range(2)))
        self.assertEqual(outcomes.count('delivered'), 1)
        run_tick(now=clock())
        self.assertEqual(TaskNotification.objects.filter(kind='summary').count(), 1)
        self.assertEqual(TaskReminderItem.objects.filter(kind='source').count(), 1)

    def test_planner_workers_share_one_parent_and_child(self):
        barrier = Barrier(2)
        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return plan_sources(clock())
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(worker, range(2)))
        self.assertTrue(all(outcome['failed'] == 0 for outcome in outcomes))
        self.assertEqual(TaskReminderJob.objects.filter(kind='summary').count(), 1)
        self.assertEqual(TaskReminderJob.objects.filter(kind='source').count(), 1)

    def test_account_edit_has_priority_then_retry_recovers_without_duplicate(self):
        plan_sources(clock())
        locked, released = ThreadEvent(), ThreadEvent()
        def account_edit():
            close_old_connections()
            try:
                with transaction.atomic():
                    get_user_model().objects.select_for_update().get(pk=self.admin.pk)
                    locked.set()
                    if not released.wait(timeout=10):
                        raise AssertionError('Source worker blocked the account edit.')
                    type(self.profiles[self.admin.pk]).objects.select_for_update().get(pk=self.profiles[self.admin.pk].pk)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            edit = pool.submit(account_edit)
            self.assertTrue(locked.wait(timeout=10))
            try:
                outcome = run_tick(now=clock())
            finally:
                released.set()
            edit.result(timeout=10)
        self.assertEqual(outcome['outcomes'].get('failed'), 1)
        self.assertFalse(TaskNotification.objects.filter(kind='summary').exists())
        run_tick(now=clock()+timedelta(minutes=1))
        run_tick(now=clock()+timedelta(minutes=1))
        self.assertEqual(TaskNotification.objects.filter(kind='summary').count(), 1)

    def test_committed_owner_change_after_claim_revokes_old_delivery(self):
        plan_sources(clock())
        job = claim_job(now=clock())
        self.execution.primary_manager = self.profiles[self.manager.pk]
        self.execution.save()
        self.assertEqual(deliver_job(job, now=clock()), 'empty')
        self.assertFalse(TaskNotification.objects.filter(kind='summary').exists())
