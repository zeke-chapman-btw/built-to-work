"""Durable, replaceable schedules derived from authoritative current tasks."""
import hashlib
import json
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.events.models import Event
from . import task_access
from .models import Task, TaskReminderJob, TaskReminderSchedulerState
from .reminder_calendar import (EASTERN, morning, previous_working_day,
                                primary_event, scope_for, weekend_applies)
from .reminder_preferences import enabled, policy_values, preference, snoozed

log = logging.getLogger(__name__)
OPEN = ('todo', 'in_progress')
REPLACEABLE = ('pending', 'claimed', 'failed')


def digest(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def recipient_id(task):
    if hasattr(task, '_reminder_recipient_id'):
        return task._reminder_recipient_id
    # A profile can be relinked independently of a task edit.
    from apps.staffing.models import StaffProfile
    return StaffProfile.objects.filter(pk=task.assigned_staff_id).values_list('user_id', flat=True).first()


def fingerprint(task, *, include_zone=True):
    event = primary_event(task)
    return digest(task.pk, recipient_id(task), task.due_date, task.weekend_work_date,
                  task.status in OPEN and task.archived_at is None,
                  event.pk if event else None,
                  event.timezone_name if event and include_zone else None)


def coverage_key(user_id, task, kind, *, day=None, source_event=None):
    if source_event is not None:
        return digest('activity', user_id, source_event.pk)
    # Moving an unchanged deadline between event/general scopes is not a new deadline.
    if kind in ('advance', 'due', 'overdue'):
        return digest('deadline', user_id, task.pk, kind, task.due_date,
                      day if kind == 'overdue' else None)
    return digest('event-work', user_id, task.pk, kind, task.weekend_work_date,
                  day if kind == 'work' else None)


def item_fingerprint(task, kind):
    if kind in ('advance', 'due', 'overdue'):
        return digest(task.pk, recipient_id(task), task.due_date,
                      task.status in OPEN and task.archived_at is None)
    return fingerprint(task, include_zone=False)


def scope_matches(task, job):
    event, zone = scope_for(task, job.local_date)
    return (event.pk if event else None) == job.event_id and zone == job.timezone_name


def calendar_allowed(task, day):
    return day.weekday() < 5 or weekend_applies(task, day)


def ensure_summary(user, task, now):
    source_event = primary_event(task)
    zone = source_event.timezone_name if source_event else EASTERN
    day = now.astimezone(ZoneInfo(zone)).date()
    if not calendar_allowed(task, day):
        return None
    event, zone = scope_for(task, day)
    if not task_access.staff_user(user) or not task_access.tasks_for(user).filter(pk=task.pk).exists():
        return None
    when = morning(day, zone)
    key = digest('summary', user.pk, event.pk if event else None, zone, day)
    job, _ = TaskReminderJob.objects.get_or_create(key=key, defaults={
        'kind': 'summary', 'recipient': user, 'event': event, 'local_date': day,
        'timezone_name': zone, 'scheduled_for': when, 'available_at': when})
    return job


@transaction.atomic
def sync_task(task_id, *, now=None):
    """Also used by bounded recovery scans when an after-commit callback is lost."""
    if not enabled():
        return []
    now = now or timezone.now()
    initial = Task.objects.select_related('event').filter(pk=task_id).first()
    if initial is None:
        return []
    event_ids = set(initial.event_links.values_list('event_id', flat=True))
    if initial.event_id:
        event_ids.add(initial.event_id)
    list(Event.objects.select_for_update().filter(pk__in=event_ids).order_by('pk'))
    task = Task.objects.select_for_update(of=('self',)).select_related('event', 'assigned_staff').get(pk=task_id)
    from apps.staffing.models import StaffProfile
    nowait = connection.features.has_select_for_update_nowait
    StaffProfile.objects.select_for_update(nowait=nowait).get(pk=task.assigned_staff_id)
    current_event = primary_event(task)
    if current_event and current_event.pk not in event_ids:
        # A simultaneous link change will be picked up on the next scan.
        return []
    ids = {recipient_id(task), task.created_by_id} - {None}
    users = {u.pk: u for u in get_user_model().objects.select_for_update(nowait=nowait)
             .filter(pk__in=ids).order_by('pk')}
    current_recipient = recipient_id(task)
    current = fingerprint(task)
    TaskReminderJob.objects.filter(task=task, state__in=REPLACEABLE).exclude(
        fingerprint=current).update(state='superseded', claim_token=None,
                                    lease_until=None, finished_at=now)
    for user in users.values():
        ensure_summary(user, task, now)
    user = users.get(current_recipient)
    if (task.archived_at or task.status not in OPEN or not task.due_date or
            user is None or not task_access.staff_user(user) or
            not task_access.tasks_for(user).filter(pk=task.pk).exists()):
        TaskReminderJob.objects.filter(task=task, state__in=REPLACEABLE).update(
            state='superseded', claim_token=None, lease_until=None, finished_at=now)
        return []
    jobs = []
    for kind, day in [('advance', previous_working_day(task.due_date)), ('due', task.due_date)]:
        event, zone = scope_for(task, day)
        local_today = now.astimezone(ZoneInfo(zone)).date()
        # Old ticks are not useful individual notifications after downtime.
        if day < local_today or not calendar_allowed(task, day):
            continue
        if kind == 'advance' and not policy_values(current_event)['advance_enabled']:
            continue
        when = morning(day, zone)
        key = digest(kind, task.pk, user.pk, current, day, zone)
        job, created = TaskReminderJob.objects.get_or_create(key=key, defaults={
            'kind': kind, 'recipient': user, 'task': task, 'event': event,
            'local_date': day, 'timezone_name': zone, 'scheduled_for': when,
            'available_at': when, 'fingerprint': current})
        if not created and job.state == 'superseded':
            job.state, job.available_at = 'pending', when
            job.claim_token, job.lease_until, job.finished_at = None, None, None
            job.save(update_fields=['state', 'available_at', 'claim_token', 'lease_until', 'finished_at', 'updated_at'])
        jobs.append(job)
    # Suppress a disabled phase even if its old schedule fingerprint still matches.
    TaskReminderJob.objects.filter(task=task, state__in=REPLACEABLE).exclude(
        pk__in=[job.pk for job in jobs]).update(state='superseded', claim_token=None,
                                              lease_until=None, finished_at=now)
    return jobs


def reconcile_after_commit(task_id):
    if not enabled():
        return
    def reconcile():
        try:
            sync_task(task_id)
        except Exception:
            # The source edit already committed; the durable scan repairs scheduling.
            log.exception('Task reminder reconciliation will retry: %s', task_id)
    transaction.on_commit(reconcile)


def plan_tick(now, *, limit=200):
    """Plan current changes plus a bounded rotating scan; never replay past days."""
    if not enabled():
        return 0
    with transaction.atomic():
        state, _ = TaskReminderSchedulerState.objects.get_or_create(key='task-reminders')
        state = TaskReminderSchedulerState.objects.select_for_update().get(pk=state.pk)
        previous = state.last_planned_at
        scan = Task.objects.order_by('pk')
        if state.scan_after:
            scan = scan.filter(pk__gt=state.scan_after)
        ids = list(scan.values_list('pk', flat=True)[:limit])
        if not ids and state.scan_after:
            ids = list(Task.objects.order_by('pk').values_list('pk', flat=True)[:limit])
        state.scan_after = str(ids[-1]) if ids else ''
        if previous:
            changed = list(Task.objects.filter(updated_at__gte=previous)
                           .order_by('updated_at', 'pk').values_list('pk', flat=True)[:limit])
        else:
            changed = []
        state.last_planned_at = now
        state.save(update_fields=['scan_after', 'last_planned_at'])
    failures = []
    for task_id in dict.fromkeys([*changed, *ids]):
        try:
            sync_task(task_id, now=now)
        except Exception:
            failures.append(str(task_id))
            log.exception('Task reminder planning will retry: %s', task_id)
    cutoff = now.date() - timedelta(days=2)
    old = list(TaskReminderJob.objects.filter(state__in=REPLACEABLE, local_date__lt=cutoff)
               .order_by('scheduled_for').values_list('pk', flat=True)[:limit])
    TaskReminderJob.objects.filter(pk__in=old).update(
        state='superseded', claim_token=None, lease_until=None, finished_at=now)
    return {'tasks': len(set([*changed, *ids])), 'failed': len(failures)}
