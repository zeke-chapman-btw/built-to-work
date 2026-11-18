"""One submitted revision produces one actionable bell reminder per recipient/day."""
from datetime import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.db import OperationalError, close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from apps.customers.test_domain import CustomerFixtures
from apps.reminders import engine
from apps.reminders.models import ReminderNotice, ReminderRole, ReminderRule, ReminderSend, ReminderSetup
from apps.reminders.seed import apply as seed_reminders
from .models import TaskNotification, TaskReminderPolicy
from .reminders import run_tick
from .test_reminders_18a import Fixtures, clock


class JobReminderCoexistenceFixture(Fixtures):
    def setUp(self):
        self.fixture_clock = patch('django.utils.timezone.now', return_value=clock(19, 6))
        self.fixture_clock.start()
        self.addCleanup(self.fixture_clock.stop)
        self.make_fixture()
        seed_reminders(ReminderRole, ReminderRule)
        customer = CustomerFixtures()
        customer.admin = self.admin
        self.org = customer.org(name='SAMPLE shared job reminder')
        self.customer = customer
        self.need = customer.draft(self.org)
        customer.transition(self.org, self.need, 'submit')
        TaskReminderPolicy.objects.create(key='company', advance_enabled=True,
            activity_enabled=True, completion_enabled=True, job_review_recipient=self.admin)
        self.rule = ReminderRule.objects.get(key='new_position')
        self.rule.recipients = {'people': [str(self.admin.pk)]}
        self.rule.backup = 'none'
        self.rule.email = self.rule.text = False
        self.rule.bell = self.rule.enabled = True
        self.rule.send_time = time(6)
        self.rule.save()
        self.setup = ReminderSetup.current()
        self.setup.is_on = True
        self.setup.turned_on_at = clock(19, 5)
        self.setup.save()

    def summary_tick(self, day=19, hour=7):
        with patch('django.utils.timezone.now', return_value=clock(day, hour)):
            run_tick(now=clock(day, hour))

    def rule_tick(self, day=19, hour=8):
        with patch('django.utils.timezone.now', return_value=clock(day, hour)):
            return engine.run_rule(self.rule, now=clock(day, hour), setup=self.setup)

    def reminder_count(self):
        return (TaskNotification.objects.filter(recipient=self.admin, kind='summary').count() +
                TaskNotification.objects.filter(recipient=self.admin, kind='reminder').count())


@override_settings(BTW_TASK_REMINDERS_ENABLED=True, BTW_EVENT_APPROVAL_REMINDERS_ENABLED=True)
class JobReminderCoexistence18BTests(JobReminderCoexistenceFixture, TestCase):
    def test_existing_summary_prevents_second_job_bell(self):
        self.summary_tick()
        self.rule_tick()
        self.assertEqual(self.reminder_count(), 1,
            'The same submitted revision appeared in both the morning summary and a new-position bell.')

    def test_earlier_job_bell_prevents_same_day_summary_duplicate(self):
        self.rule_tick(hour=6)
        self.summary_tick()
        self.assertEqual(self.reminder_count(), 1,
            'An earlier job bell was repeated in the same morning summary window.')

    def test_still_pending_approval_returns_in_next_days_summary(self):
        self.rule_tick(hour=6)
        self.summary_tick()
        self.summary_tick(day=20)
        self.assertEqual(TaskNotification.objects.filter(recipient=self.admin, kind='reminder').count(), 1)
        self.assertEqual(TaskNotification.objects.filter(recipient=self.admin, kind='summary').count(), 1)
        notice = TaskNotification.objects.get(recipient=self.admin, kind='summary')
        self.assertEqual(notice.reminder_job.local_date, clock(20).date())
        self.need.working_revision.refresh_from_db()
        self.assertEqual(self.need.working_revision.state, 'submitted')

    def test_read_summary_still_prevents_a_second_bell_in_the_window(self):
        self.summary_tick()
        from .notifications import mark_read
        notice = TaskNotification.objects.get(recipient=self.admin, kind='summary')
        mark_read(self.admin, notice.pk)
        self.rule_tick()
        self.assertEqual(self.reminder_count(), 1)

    def test_email_delivery_does_not_suppress_the_required_bell_summary(self):
        self.admin.email = 'sample-reviewer@example.test'
        self.admin.save(update_fields=['email'])
        self.rule.bell, self.rule.email = False, True
        self.rule.save()
        delivery = Mock()
        with patch.dict(engine.DELIVER, {'email': delivery}):
            self.rule_tick(hour=6)
        self.summary_tick()
        self.assertEqual(delivery.call_count, 1)
        self.assertEqual(TaskNotification.objects.filter(recipient=self.admin, kind='summary').count(), 1)
        self.assertFalse(TaskNotification.objects.filter(kind='reminder').exists())

    def test_another_recipients_notice_does_not_cover_the_designated_reviewer(self):
        self.rule.recipients = {'people': [str(self.manager.pk)]}
        self.rule.save()
        self.rule_tick(hour=6)
        self.summary_tick()
        self.assertEqual(TaskNotification.objects.filter(recipient=self.manager, kind='reminder').count(), 1)
        self.assertEqual(TaskNotification.objects.filter(recipient=self.admin, kind='summary').count(), 1)

    def test_identical_titles_do_not_hide_another_submitted_revision(self):
        self.summary_tick()
        with patch('django.utils.timezone.now', return_value=clock(19, 8)):
            second = self.customer.draft(self.org)
            self.customer.transition(self.org, second, 'submit')
        self.rule_tick(hour=9)
        first_ref = 'need-revision:' + str(self.need.working_revision_id)
        second_ref = 'need-revision:' + str(second.working_revision_id)
        self.assertFalse(ReminderSend.objects.filter(ref=first_ref, channel='bell').exists())
        self.assertTrue(ReminderSend.objects.filter(ref=second_ref, channel='bell', status='sent').exists())
        self.assertEqual(self.reminder_count(), 2)

    def test_failed_bell_delivery_does_not_suppress_the_summary(self):
        with patch.dict(engine.DELIVER, {'bell': Mock(side_effect=RuntimeError('SAMPLE delivery failure'))}):
            self.rule_tick(hour=6)
        self.summary_tick()
        self.assertEqual(TaskNotification.objects.filter(recipient=self.admin, kind='summary').count(), 1)
        self.assertFalse(TaskNotification.objects.filter(kind='reminder').exists())

    def test_settings_sender_keeps_working_when_18b_is_disabled(self):
        with override_settings(BTW_EVENT_APPROVAL_REMINDERS_ENABLED=False):
            self.rule_tick()
        self.assertEqual(TaskNotification.objects.filter(recipient=self.admin, kind='reminder').count(), 1)


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL source-lock verification')
@override_settings(BTW_TASK_REMINDERS_ENABLED=True, BTW_EVENT_APPROVAL_REMINDERS_ENABLED=True)
class JobReminderCoexistencePostgres18BTests(JobReminderCoexistenceFixture, TransactionTestCase):
    def worker(self, action, barrier=None):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '2s'")
                cursor.execute("SET statement_timeout = '8s'")
            if barrier:
                barrier.wait(timeout=10)
            try:
                return action()
            except OperationalError as error:
                code = getattr(error.__cause__, 'sqlstate', None)
                if code != '55P03':
                    raise
                return 'lock-retry'
        finally:
            connections.close_all()

    def test_concurrent_senders_share_one_revision_bell(self):
        barrier = Barrier(2)
        with patch('django.utils.timezone.now', return_value=clock(19, 7)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.worker, lambda: run_tick(now=clock(19, 7)), barrier)
                second = pool.submit(self.worker,
                    lambda: engine.run_rule(self.rule, now=clock(19, 7), setup=self.setup), barrier)
                first.result(timeout=20)
                second.result(timeout=20)
        self.summary_tick(hour=8)
        self.rule_tick(hour=8)
        self.assertEqual(self.reminder_count(), 1)

    def test_busy_revision_defers_without_claiming_a_notice(self):
        from apps.customers.models import WorkforceNeedRevision
        with patch('django.utils.timezone.now', return_value=clock(19, 7)):
            with transaction.atomic():
                WorkforceNeedRevision.objects.select_for_update().get(pk=self.need.working_revision_id)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.worker,
                        lambda: engine.run_rule(self.rule, now=clock(19, 7), setup=self.setup))
                    self.assertEqual(future.result(timeout=15), 'lock-retry')
            self.assertFalse(ReminderSend.objects.filter(rule=self.rule).exists())
        self.rule_tick(hour=7)
        self.summary_tick(hour=8)
        self.assertEqual(self.reminder_count(), 1)
