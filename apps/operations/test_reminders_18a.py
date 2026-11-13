"""Isolated clock, permission, delivery and recovery regressions for Build 18A."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone as utc
from threading import Barrier
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.accounts.roles import ROLE_GROUPS
from apps.core.audit import record_audit
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.staffing.models import StaffProfile, StaffAssignment
from . import notifications, services
from .models import (Task, TaskNotification, TaskReminderJob, TaskReminderItem,
                     TaskReminderPreference, TaskReminderSchedulerState)
from .reminder_planning import sync_task
from .reminder_preferences import (save_personal, save_policy, save_weekend_work, snooze_tasks)
from .reminder_rendering import decorate
from .reminders import claim_job, deliver_job, retry_job, run_tick

ET = ZoneInfo('America/New_York')


def clock(day=13, hour=7, month=11):
    return datetime(2026, month, day, hour, tzinfo=ET)


class Fixtures:
    def make_fixture(self):
        U = get_user_model()
        self.admin = U.objects.create_user('SAMPLE_18a_admin', is_staff=True, is_superuser=True)
        self.manager = U.objects.create_user('SAMPLE_18a_manager', is_staff=True)
        self.recruiter = U.objects.create_user('SAMPLE_18a_recruiter', is_staff=True)
        self.staff = U.objects.create_user('SAMPLE_18a_staff', is_staff=True)
        self.other = U.objects.create_user('SAMPLE_18a_other', is_staff=True)
        for user, role in [(self.manager, 'manager'), (self.recruiter, 'recruiter'), (self.staff, 'event_staff')]:
            user.groups.add(Group.objects.get_or_create(name=ROLE_GROUPS[role])[0])
        self.profiles = {u.pk: StaffProfile.objects.create(first_name='SAMPLE', last_name=u.username, user=u)
                         for u in (self.admin, self.manager, self.recruiter, self.staff, self.other)}
        self.event = Event.objects.create(name='SAMPLE Monday event', start_at=clock(16, 9),
            end_at=clock(16, 17), timezone_name='America/New_York', status='upcoming', created_by=self.admin)

    def task(self, *, due=date(2026, 11, 16), event=None, assignee=None, actor=None, at=None):
        user = assignee or self.staff
        with patch('django.utils.timezone.now', return_value=at or clock(13, 6)):
            task, form = services.save_task(actor=actor or self.admin, data={
                'title': 'SAMPLE reminder task', 'description': '', 'priority': 'normal',
                'due_date': due.isoformat() if due else '', 'event': str(event.pk) if event else '',
                'assigned_staff': str(self.profiles[user.pk].pk), 'revision': 'new'})
        self.assertIsNotNone(task, form.errors)
        task.refresh_from_db()
        return task

    def summaries(self, user=None):
        return TaskNotification.objects.filter(recipient=user or self.staff, kind='summary')

    def edit(self, task, **values):
        data = dict(title=task.title, description=task.description, priority=task.priority,
                    due_date=task.due_date.isoformat() if task.due_date else '',
                    event=str(task.event_id) if task.event_id else '', assigned_staff=str(task.assigned_staff_id),
                    revision=task.updated_at.isoformat())
        data.update(values)
        task, form = services.save_task(actor=self.admin, task_id=task.pk, data=data)
        self.assertIsNotNone(task, form.errors)
        return task

    def visible(self, user=None, now=None):
        with patch('django.utils.timezone.now', return_value=now or clock()):
            return list(notifications.visible_notifications(user or self.staff))


@override_settings(BTW_TASK_REMINDERS_ENABLED=True)
class ReminderDelivery18ATests(Fixtures, TestCase):
    def setUp(self):
        self.make_fixture()

    def test_assignment_reuses_audit_dedupe_including_self_assignment(self):
        task = self.task(assignee=self.admin)
        notice = TaskNotification.objects.get(task=task, kind='assigned')
        notifications.notify_from_audit(notice.source_event, task)
        self.assertEqual(TaskNotification.objects.filter(task=task, kind='assigned').count(), 1)
        self.assertFalse(TaskReminderJob.objects.filter(kind='assigned').exists())

    def test_friday_advance_and_monday_due_are_covered_once_by_summary(self):
        task = self.task()
        for now in (clock(), clock(), clock(16), clock(16)):
            run_tick(now=now)
        rows = TaskReminderItem.objects.filter(task=task, job__recipient=self.staff, kind__in=('advance','due'))
        self.assertEqual(list(rows.order_by('kind').values_list('kind', flat=True)), ['advance', 'due'])
        self.assertEqual(self.summaries().count(), 2)
        self.assertFalse(TaskNotification.objects.filter(recipient=self.staff,kind__in=('advance','due')).exists())

    def test_overdue_is_one_summary_per_morning_never_a_direct_job(self):
        task = self.task(due=date(2026, 11, 12))
        run_tick(now=clock()); run_tick(now=clock(13, 12)); run_tick(now=clock(16))
        self.assertEqual(TaskReminderItem.objects.filter(task=task, kind='overdue', job__recipient=self.staff).count(), 2)
        self.assertEqual(self.summaries().count(), 2)

    def test_no_notification_before_seven(self):
        self.task()
        run_tick(now=clock(13, 6))
        self.assertFalse(self.summaries().exists())

    def test_empty_summary_is_not_a_notification(self):
        self.task(due=None)
        run_tick(now=clock())
        self.assertFalse(self.summaries().exists())
        self.assertTrue(TaskReminderJob.objects.filter(kind='summary', state='empty').exists())

    def test_saturday_setup_for_monday_event_and_unrelated_work_excluded(self):
        setup = self.task(due=date(2026,11,16), event=self.event)
        save_weekend_work(actor=self.admin, task_id=setup.pk, work_date=date(2026,11,14),
                          expected_revision=setup.updated_at.isoformat())
        unrelated = self.task(due=date(2026,11,13))
        event_link_only = self.task(due=date(2026,11,14), event=self.event)
        run_tick(now=clock(14))
        n = self.summaries().get()
        self.assertEqual(n.reminder_job.event, self.event)
        self.assertEqual(set(n.reminder_job.items.values_list('task_id', flat=True)), {setup.pk})
        self.assertFalse(TaskReminderItem.objects.filter(task__in=[unrelated,event_link_only]).exists())

    def test_event_timezone_seven_and_no_general_duplicate(self):
        self.event.timezone_name='America/Los_Angeles'; self.event.save(update_fields=['timezone_name'])
        self.task(due=date(2026,11,13),event=self.event)
        run_tick(now=clock())
        self.assertFalse(self.summaries().exists())
        run_tick(now=clock(13,10))
        n=self.summaries().get()
        self.assertEqual(n.reminder_job.timezone_name,'America/Los_Angeles')
        self.assertEqual(n.reminder_job.scheduled_for.astimezone(ZoneInfo('America/Los_Angeles')).hour,7)
        self.assertEqual(n.reminder_job.items.filter(kind='due').count(),1)
        self.assertIn('America/Los_Angeles',decorate(self.staff,[n])[0].scope_label)

    def test_monday_includes_weekend_activity_without_repeating_it(self):
        task=self.task(due=None)
        run_tick(now=clock())
        with patch('django.utils.timezone.now',return_value=clock(15,12)):
            source=record_audit(actor=self.admin, action='operations.task.comment.created', instance=task,
                                source='staff',new_data={'content':'SAMPLE weekend update'})
        run_tick(now=clock(16));run_tick(now=clock(17))
        self.assertEqual(TaskReminderItem.objects.filter(source_event=source,job__recipient=self.staff).count(),1)

    def test_completed_work_appears_but_future_deadline_is_cancelled(self):
        task=self.task()
        sync_task(task.pk,now=clock(13,6))
        with patch('django.utils.timezone.now',return_value=clock(13,6)):
            task,form=services.task_status(actor=self.admin,task_id=task.pk,
                data={'status':'completed','revision':task.updated_at.isoformat()})
        run_tick(now=clock())
        self.assertEqual(self.summaries().get().reminder_job.items.get().kind,'completed')
        self.assertFalse(TaskReminderJob.objects.filter(task=task,state='pending').exists())

    def test_cancelled_and_archived_tasks_do_not_generate_reminders(self):
        for status in ('cancelled','archived'):
            task=self.task()
            sync_task(task.pk,now=clock(13,6))
            if status=='cancelled':
                services.task_status(actor=self.admin,task_id=task.pk,data={'status':status,'revision':task.updated_at.isoformat()})
            else:
                services.task_archive(actor=self.admin,task_id=task.pk,data={'revision':task.updated_at.isoformat()})
        run_tick(now=clock())
        self.assertFalse(self.summaries().exists())

    def test_reassignment_invalidates_claim_and_old_recipient_display(self):
        task=self.task(due=date(2026,11,13))
        run_tick(now=clock())
        self.assertTrue(any(n.reminder_job_id for n in self.visible()))
        task=self.edit(task,assigned_staff=str(self.profiles[self.recruiter.pk].pk))
        run_tick(now=clock())
        self.assertFalse(self.visible())
        self.assertEqual(self.summaries(self.recruiter).count(),1)

    def test_changed_deadline_hides_old_summary_and_reschedules(self):
        task=self.task(due=date(2026,11,13));run_tick(now=clock())
        task=self.edit(task,due_date='2026-11-20')
        self.assertFalse(any(n.reminder_job_id for n in self.visible()))
        sync_task(task.pk,now=clock())
        self.assertEqual(set(TaskReminderJob.objects.filter(task=task,state='pending').values_list('local_date',flat=True)),
                         {date(2026,11,19),date(2026,11,20)})

    def test_moving_same_deadline_between_general_and_event_scopes_does_not_repeat_it(self):
        task=self.task(due=date(2026,11,13));run_tick(now=clock())
        self.event.timezone_name='America/Los_Angeles';self.event.save(update_fields=['timezone_name'])
        task=self.edit(task,event=str(self.event.pk));run_tick(now=clock(13,10))
        self.assertEqual(TaskReminderItem.objects.filter(task=task,kind='due',job__recipient=self.staff).count(),1)
        self.assertEqual(len([n for n in self.visible(now=clock(13,10)) if n.reminder_job_id]),1)

    def test_changing_weekend_work_date_does_not_repeat_unchanged_deadline(self):
        task=self.task(event=self.event)
        save_weekend_work(actor=self.admin,task_id=task.pk,work_date=date(2026,11,14),
                          expected_revision=task.updated_at.isoformat())
        run_tick(now=clock());task.refresh_from_db()
        save_weekend_work(actor=self.admin,task_id=task.pk,work_date=date(2026,11,15),
                          expected_revision=task.updated_at.isoformat())
        run_tick(now=clock(13,8))
        self.assertEqual(TaskReminderItem.objects.filter(task=task,kind='advance',job__recipient=self.staff).count(),1)

    def test_completion_hides_existing_required_notice_at_display(self):
        task=self.task(due=date(2026,11,13));run_tick(now=clock())
        services.task_status(actor=self.admin,task_id=task.pk,data={'status':'completed','revision':task.updated_at.isoformat()})
        self.assertFalse(any(n.reminder_job_id for n in self.visible()))

    def test_snooze_hides_all_copies_without_changing_deadline(self):
        task=self.task(due=date(2026,11,13));run_tick(now=clock())
        until=snooze_tasks(actor=self.staff,tasks=[task],choice='hour',now=clock())
        self.assertEqual(until,clock(13,8))
        self.assertFalse(self.visible(now=clock()))
        run_tick(now=clock(13,7)+timedelta(minutes=30))
        self.assertEqual(self.summaries().count(),1)
        self.assertTrue(self.visible(now=clock(13,8)))
        task.refresh_from_db();self.assertEqual(task.due_date,date(2026,11,13))

    def test_hour_snooze_is_one_elapsed_hour_through_eastern_fall_back(self):
        task=self.task()
        start=datetime(2026,11,1,1,30,tzinfo=ET,fold=0)
        until=snooze_tasks(actor=self.staff,tasks=[task],choice='hour',now=start)
        self.assertEqual(until.astimezone(utc.utc)-start.astimezone(utc.utc),timedelta(hours=1))
        self.assertEqual(until.astimezone(ET).fold,1)

    def test_next_morning_snooze_and_new_summary_supersedes_old_copy(self):
        task=self.task(due=date(2026,11,13));run_tick(now=clock())
        until=snooze_tasks(actor=self.staff,tasks=[task],choice='morning',now=clock())
        self.assertEqual(until,clock(16))
        run_tick(now=clock(16))
        visible=[n for n in self.visible(now=clock(16)) if n.reminder_job_id]
        self.assertEqual(len(visible),1)
        self.assertEqual(visible[0].reminder_job.local_date,date(2026,11,16))

    def test_optional_mute_does_not_disable_assignment_or_deadline(self):
        save_personal(actor=self.staff,activity_updates=False,expected_revision='new')
        task=self.task(due=date(2026,11,13));run_tick(now=clock())
        kinds={n.kind for n in self.visible()}
        self.assertEqual(kinds,{'assigned','summary'})
        self.assertEqual(self.summaries().get().reminder_job.items.get().kind,'due')

    def test_disabled_advance_is_rechecked_after_a_job_was_claimed(self):
        task=self.task();sync_task(task.pk,now=clock())
        TaskReminderJob.objects.exclude(recipient=self.staff,kind='advance').update(state='superseded')
        job=claim_job(now=clock())
        save_policy(actor=self.admin,values=dict(advance_enabled=False,activity_enabled=True,
                    completion_enabled=True),expected_revision='new')
        self.assertEqual(deliver_job(job,now=clock()),'superseded')
        self.assertFalse(TaskNotification.objects.filter(reminder_job=job).exists())

    def test_one_planning_failure_does_not_block_other_tasks(self):
        task=self.task()
        with patch('apps.operations.reminder_planning.sync_task',side_effect=RuntimeError('SAMPLE planning failure')):
            outcome=run_tick(now=clock())
        self.assertEqual(outcome['planning_failures'],1)
        self.assertIn('planning',TaskReminderSchedulerState.objects.get().last_error)
        run_tick(now=clock())
        self.assertEqual(self.summaries().count(),1)

    def test_no_broadcast_to_unrelated_manager(self):
        self.task();run_tick(now=clock())
        self.assertFalse(TaskNotification.objects.filter(recipient=self.manager).exists())

    def test_inactive_and_unlinked_assignees_are_rechecked(self):
        task=self.task();sync_task(task.pk,now=clock(13,6))
        get_user_model().objects.filter(pk=self.staff.pk).update(is_active=False)
        run_tick(now=clock());self.assertFalse(self.visible())
        self.assertFalse(self.summaries().exists())

    def test_staff_profile_relink_is_recovered_without_task_edit(self):
        task=self.task();sync_task(task.pk,now=clock(13,6))
        # A distinct unlinked account becomes the profile's current login.
        user=get_user_model().objects.create_user('SAMPLE_relinked',is_staff=True)
        StaffProfile.objects.filter(pk=task.assigned_staff_id).update(user=user)
        run_tick(now=clock())
        self.assertFalse(self.summaries().exists());self.assertEqual(self.summaries(user).count(),1)

    def test_late_restart_creates_only_current_catchup(self):
        task=self.task(due=date(2026,11,12));sync_task(task.pk,now=clock(12,6))
        run_tick(now=clock(18,12));run_tick(now=clock(18,12))
        self.assertEqual(self.summaries().count(),1)
        self.assertEqual(self.summaries().get().reminder_job.local_date,date(2026,11,18))
        self.assertFalse(TaskNotification.objects.filter(kind__in=('advance','due')).exists())

    def test_failure_rolls_back_items_and_notice_then_retries_once(self):
        self.task();sync_task(Task.objects.get().pk,now=clock())
        job=claim_job(now=clock())
        with patch.object(TaskNotification.objects,'create',side_effect=RuntimeError('SAMPLE injected failure')):
            with self.assertRaises(RuntimeError):deliver_job(job,now=clock())
        self.assertFalse(TaskReminderItem.objects.filter(job=job).exists())
        retry_job(job,RuntimeError('SAMPLE injected failure'),clock(),'test-worker')
        job.refresh_from_db();self.assertEqual(job.state,'failed');self.assertEqual(job.attempts,1)
        later=clock()+timedelta(minutes=1)
        recovered=claim_job(now=later)
        # Other ready summary jobs may precede the retry; the tick recovers all.
        if recovered: deliver_job(recovered,now=later)
        run_tick(now=later)
        self.assertEqual(TaskNotification.objects.filter(reminder_job=job).count(),1)

    def test_expired_claim_recovery_invalidates_previous_token(self):
        task=self.task();sync_task(task.pk,now=clock())
        job=claim_job(now=clock())
        TaskReminderJob.objects.filter(pk=job.pk).update(lease_until=clock()-timedelta(seconds=1))
        replacement=claim_job(now=clock())
        self.assertEqual(replacement.pk,job.pk)
        self.assertEqual(deliver_job(job,now=clock()),'lost_claim')
        deliver_job(replacement,now=clock())
        self.assertEqual(TaskNotification.objects.filter(reminder_job=job).count(),1)

    def test_manual_tick_is_not_automatic_operating_proof(self):
        run_tick(now=clock())
        state=TaskReminderSchedulerState.objects.get();self.assertIsNone(state.last_automatic_at)
        run_tick(now=clock(13,8),automatic=True,worker_id='SAMPLE automatic')
        state.refresh_from_db();self.assertEqual(state.last_automatic_at,clock(13,8))

    def test_system_delivery_does_not_fabricate_task_history(self):
        self.task();before=AuditLog.objects.filter(action__startswith='operations.task.').count()
        run_tick(now=clock())
        self.assertEqual(AuditLog.objects.filter(action__startswith='operations.task.').count(),before)

    def test_failed_event_permission_lookup_hides_event_name_without_breaking_task_updates(self):
        from django.db import DatabaseError
        self.event.timezone_name='America/Los_Angeles';self.event.save(update_fields=['timezone_name'])
        self.task(due=date(2026,11,13),event=self.event,assignee=self.admin)
        run_tick(now=clock(13,10));notice=self.summaries(self.admin).get()
        with patch('apps.operations.task_access.events_for',side_effect=DatabaseError('SAMPLE unavailable')):
            rendered=decorate(self.admin,[notice])[0]
        self.assertNotIn(self.event.name,rendered.scope_label)
        self.assertIn('America/Los_Angeles',rendered.scope_label)

    def test_notification_queries_do_not_grow_once_per_task_and_history_receipt(self):
        from django.test.utils import CaptureQueriesContext
        self.task(due=date(2026,11,12));run_tick(now=clock())
        with patch('django.utils.timezone.now',return_value=clock()), CaptureQueriesContext(connection) as small:
            list(notifications.visible_notifications(self.staff))
        for _ in range(9):self.task(due=date(2026,11,12))
        run_tick(now=clock(16))
        with patch('django.utils.timezone.now',return_value=clock(16)), CaptureQueriesContext(connection) as larger:
            list(notifications.visible_notifications(self.staff))
        self.assertLessEqual(len(larger),len(small)+2)

    def test_read_state_bell_home_and_summary_link(self):
        self.task();run_tick(now=clock())
        self.client.force_login(self.staff, backend='django.contrib.auth.backends.ModelBackend')
        with patch('django.utils.timezone.now',return_value=clock()):
            page=self.client.get(reverse('operations:updates'))
            self.assertEqual(page.status_code,200);self.assertContains(page,'Morning summary')
            self.assertContains(page,'SAMPLE reminder task')
            n=self.summaries().get();notifications.mark_read(self.staff,n.pk)
            n.refresh_from_db();self.assertIsNotNone(n.read_at)
            context=notifications.shell_context(self.staff)
            self.assertTrue(all(hasattr(row,'destination') for row in context['task_updates']['recent']))

    def test_date_only_deadline_rejects_timed_input(self):
        task,form=services.save_task(actor=self.admin,data={'title':'SAMPLE timed input',
            'assigned_staff':str(self.profiles[self.staff.pk].pk),'due_date':'2026-11-13T12:00:00-05:00',
            'priority':'normal','revision':'new','event':''})
        self.assertIsNone(task);self.assertIn('due_date',form.errors)

    def test_no_external_delivery_code_is_called(self):
        self.task()
        with (patch('django.core.mail.send_mail',side_effect=AssertionError('External mail')),
              patch('urllib.request.urlopen',side_effect=AssertionError('External request'))):
            run_tick(now=clock())


@override_settings(BTW_TASK_REMINDERS_ENABLED=True)
class ReminderPermissions18ATests(Fixtures, TestCase):
    def setUp(self):self.make_fixture()

    def test_only_admin_can_change_company_defaults(self):
        values=dict(advance_enabled=False,activity_enabled=True,completion_enabled=True)
        for user in (self.manager,self.recruiter,self.staff):
            with self.assertRaises(PermissionDenied):
                save_policy(actor=user,values=values,expected_revision='new')
        obj=save_policy(actor=self.admin,values=values,expected_revision='new')
        self.assertFalse(obj.advance_enabled)

    def test_manager_event_settings_require_existing_event_authority(self):
        values=dict(advance_enabled=None,activity_enabled=False,completion_enabled=None)
        with self.assertRaises(PermissionDenied):
            save_policy(actor=self.manager,event=self.event,values=values,expected_revision='new')
        StaffAssignment.objects.create(event=self.event,staff=self.profiles[self.manager.pk],status='confirmed')
        obj=save_policy(actor=self.manager,event=self.event,values=values,expected_revision='new')
        self.assertFalse(obj.activity_enabled)
        StaffAssignment.objects.create(event=self.event,staff=self.profiles[self.staff.pk],status='confirmed')
        with self.assertRaises(PermissionDenied):
            save_policy(actor=self.staff,event=self.event,values=values,expected_revision=obj.updated_at.isoformat())

    def test_other_persons_snooze_and_work_date_are_denied(self):
        task=self.task()
        with self.assertRaises(PermissionDenied):snooze_tasks(actor=self.other,tasks=[task],choice='hour')
        with self.assertRaises(PermissionDenied):
            save_weekend_work(actor=self.other,task_id=task.pk,work_date=date(2026,11,14),expected_revision=task.updated_at.isoformat())

    def test_weekend_date_requires_event_and_weekend_and_revision(self):
        task=self.task()
        with self.assertRaises(ValidationError):
            save_weekend_work(actor=self.staff,task_id=task.pk,work_date=date(2026,11,14),expected_revision=task.updated_at.isoformat())
        task=self.edit(task,event=str(self.event.pk))
        with self.assertRaises(ValidationError):
            save_weekend_work(actor=self.staff,task_id=task.pk,work_date=date(2026,11,16),expected_revision=task.updated_at.isoformat())
        with self.assertRaises(ValidationError):
            save_weekend_work(actor=self.staff,task_id=task.pk,work_date=date(2026,11,14),expected_revision='stale')

    def test_personal_post_cannot_target_other_user_or_disable_deadlines(self):
        self.client.force_login(self.staff, backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.post(reverse('operations:reminder-preferences'),
            {'revision':'new','user':str(self.other.pk),'deadline_enabled':'false'})
        self.assertEqual(response.status_code,302)
        self.assertFalse(TaskReminderPreference.objects.get(user=self.staff).activity_updates)
        self.assertFalse(TaskReminderPreference.objects.filter(user=self.other).exists())

    def test_company_and_event_urls_deny_recruiter_and_staff(self):
        for user in (self.recruiter,self.staff):
            self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
            self.assertEqual(self.client.get(reverse('operations:reminder-company')).status_code,403)
            self.assertIn(self.client.get(reverse('operations:reminder-event',args=[self.event.pk])).status_code,(403,404))

    def test_snooze_post_requires_csrf_and_never_accepts_foreign_notice(self):
        from django.test import Client
        task=self.task();notice=TaskNotification.objects.get(task=task,recipient=self.staff)
        strict=Client(enforce_csrf_checks=True)
        strict.force_login(self.staff,backend='django.contrib.auth.backends.ModelBackend')
        url=reverse('operations:reminder-snooze',args=[notice.pk])
        self.assertEqual(strict.post(url,{'choice':'hour'}).status_code,403)
        self.client.force_login(self.other,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.post(url,{'choice':'hour'}).status_code,404)


@override_settings(BTW_TASK_REMINDERS_ENABLED=True)
class ReminderPostgresConcurrency18ATests(Fixtures, TransactionTestCase):
    def setUp(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL locking test')
        self.make_fixture()

    def test_two_workers_claim_and_deliver_once(self):
        task=self.task();sync_task(task.pk,now=clock())
        # Keep just this recipient's summary to force contention on one row.
        TaskReminderJob.objects.exclude(recipient=self.staff,kind='summary').update(state='superseded')
        barrier=Barrier(2)
        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                job=claim_job(now=clock(),worker_id=str(index))
                return deliver_job(job,now=clock(),worker_id=str(index)) if job else 'none'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(worker,range(2)))
        self.assertEqual(outcomes.count('delivered'),1)
        self.assertEqual(self.summaries().count(),1)
        self.assertEqual(TaskReminderItem.objects.filter(job__recipient=self.staff).count(),1)

    def test_claimed_job_rechecks_committed_reassignment(self):
        task=self.task();sync_task(task.pk,now=clock())
        TaskReminderJob.objects.exclude(recipient=self.staff,kind='due').update(state='superseded')
        job=claim_job(now=clock(16))
        self.assertIsNotNone(job)
        self.edit(task,assigned_staff=str(self.profiles[self.recruiter.pk].pk))
        self.assertIn(deliver_job(job,now=clock(16)),('superseded','lost_claim'))
        self.assertFalse(TaskNotification.objects.filter(reminder_job=job).exists())

    def test_direct_and_summary_workers_serialize_same_deadline_coverage(self):
        task=self.task();sync_task(task.pk,now=clock())
        TaskReminderJob.objects.exclude(recipient=self.staff,local_date=clock().date(),
            kind__in=('summary','advance')).update(state='superseded')
        barrier=Barrier(2)
        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                job=claim_job(now=clock(),worker_id=str(index))
                return deliver_job(job,now=clock(),worker_id=str(index)) if job else 'none'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(worker,range(2)))
        self.assertEqual(outcomes.count('delivered'),1)
        self.assertEqual(TaskReminderItem.objects.filter(task=task,job__recipient=self.staff,kind='advance').count(),1)
        self.assertEqual(TaskNotification.objects.filter(reminder_job__recipient=self.staff).count(),1)
