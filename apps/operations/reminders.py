"""Claim, validate and deliver task reminders through the existing in-app inbox."""
import logging
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from apps.core.models import AuditLog
from apps.events.models import Event
from . import task_access
from .models import (Task, TaskNotification, TaskReminderItem, TaskReminderJob,
                     TaskReminderSchedulerState)
from .reminder_calendar import (next_working_day, previous_working_day, primary_event)
from .reminder_planning import (OPEN, calendar_allowed, coverage_key, fingerprint,
                                item_fingerprint, plan_tick, recipient_id, scope_matches)
from .reminder_preferences import (activity_allowed, enabled, policy_values,
                                   preference, snoozed)

log = logging.getLogger(__name__)
LEASE = timedelta(minutes=2)
REQUIRED = ('due', 'overdue', 'advance', 'work', 'prep')


@transaction.atomic
def claim_job(*, now=None, worker_id='manual'):
    now = now or timezone.now()
    query = TaskReminderJob.objects.filter(
        Q(state__in=('pending', 'failed')) |
        Q(state='claimed', lease_until__lte=now), available_at__lte=now,
        local_date__gte=now.date() - timedelta(days=1),
        local_date__lte=now.date() + timedelta(days=1))
    if connection.features.has_select_for_update_skip_locked:
        query = query.select_for_update(skip_locked=True)
    else:
        query = query.select_for_update()
    job = query.annotate(_order=Case(When(kind='summary', then=Value(0)), default=Value(1),
                                     output_field=IntegerField())).order_by('-local_date', '_order', 'available_at', 'pk').first()
    if job is None:
        return None
    job.state, job.claim_token = 'claimed', uuid.uuid4()
    job.attempts += 1
    job.claimed_at, job.lease_until = now, now + LEASE
    job.save(update_fields=['state', 'claim_token', 'attempts', 'claimed_at', 'lease_until', 'updated_at'])
    return job


def finish(job, state, now, worker_id, *, count=0):
    job.state, job.finished_at = state, now
    job.claim_token, job.lease_until, job.last_error = None, None, ''
    job.attempt_history = [*(job.attempt_history or [])[-19:], {
        'attempt': job.attempts, 'worker': worker_id, 'finished_at': now.isoformat(),
        'state': state, 'items': count}]
    job.save(update_fields=['state', 'finished_at', 'claim_token', 'lease_until',
                            'last_error', 'attempt_history', 'through_at', 'updated_at'])


def _party_tasks(user):
    return task_access.tasks_for(user).filter(Q(created_by=user) | Q(assigned_staff__user=user))


def _current_day(job, now):
    return now.astimezone(ZoneInfo(job.timezone_name)).date() == job.local_date


def _proposal(job, task, kind, *, source=None):
    return {'task': task, 'kind': kind, 'source_event': source,
            'dedupe_key': coverage_key(job.recipient_id, task, kind,
                                       day=job.local_date, source_event=source),
            'fingerprint': item_fingerprint(task, kind)}


def _required_proposals(job, user, tasks, now):
    preferences = preference(user)
    day = job.local_date
    result = []
    for task in tasks:
        if (task.status not in OPEN or task.archived_at or recipient_id(task) != user.pk or
                snoozed(task, user, now, preferences=preferences)):
            continue
        if not scope_matches(task, job) or not calendar_allowed(task, day):
            continue
        kind = None
        if task.due_date:
            if task.due_date < day:
                kind = 'overdue'
            elif task.due_date == day:
                kind = 'due'
            elif (previous_working_day(task.due_date) == day and
                  policy_values(primary_event(task))['advance_enabled']):
                kind = 'advance'
        if kind is None and task.weekend_work_date:
            if task.weekend_work_date == day:
                kind = 'work'
            elif (task.weekend_work_date == next_working_day(day) or
                  (day.weekday() == 4 and 0 < (task.weekend_work_date - day).days <= 2)):
                kind = 'prep'
        if kind:
            result.append(_proposal(job, task, kind))
    return result


def _activity_proposals(job, user, tasks, now):
    previous = (TaskReminderJob.objects.filter(recipient=user, kind='summary',
                event_id=job.event_id, timezone_name=job.timezone_name,
                through_at__isnull=False, scheduled_for__lt=job.scheduled_for)
                .order_by('-through_at').first())
    start = previous.through_at if previous else job.scheduled_for - timedelta(days=3)
    by_id = {str(t.pk): t for t in tasks
             if not t.archived_at and t.status != 'cancelled' and
             scope_matches(t, job) and calendar_allowed(t, job.local_date)}
    if not by_id:
        return []
    sources = AuditLog.objects.filter(content_type__app_label='operations', content_type__model='task',
              object_id__in=by_id, action__startswith='operations.task.',
              timestamp__gt=start, timestamp__lte=now).order_by('timestamp', 'pk')
    result = []
    preferences = preference(user)
    for source in sources:
        task = by_id.get(str(source.object_id))
        completed = source.action == 'operations.task.completed'
        if (not activity_allowed(user, event=primary_event(task), completed=completed) or
                snoozed(task, user, now, preferences=preferences)):
            continue
        # Assignment delivery is owned by the existing unique audit notification.
        if source.action in ('operations.task.created', 'operations.task.reassigned'):
            if TaskNotification.objects.filter(source_event=source, recipient=user,
                                                kind__in=('assigned', 'reassigned')).exists():
                continue
        result.append(_proposal(job, task, 'completed' if completed else 'activity', source=source))
    return result


@transaction.atomic
def deliver_job(claimed, *, now=None, worker_id='manual'):
    """Events -> tasks -> recipient -> job, matching source task lock order."""
    now = now or timezone.now()
    candidate_user = get_user_model().objects.filter(pk=claimed.recipient_id).first()
    if candidate_user and task_access.staff_user(candidate_user):
        candidates = _party_tasks(candidate_user) if claimed.kind == 'summary' else Task.objects.filter(pk=claimed.task_id)
        ids = list(candidates.values_list('pk', flat=True))
    else:
        ids = []
    event_ids = set(Task.objects.filter(pk__in=ids).exclude(event_id=None).values_list('event_id', flat=True))
    from .models import TaskEventLink
    event_ids.update(TaskEventLink.objects.filter(task_id__in=ids).values_list('event_id', flat=True))
    if claimed.event_id:
        event_ids.add(claimed.event_id)
    list(Event.objects.select_for_update().filter(pk__in=event_ids).order_by('pk'))
    locked = list(Task.objects.select_for_update(of=('self',)).filter(pk__in=ids)
                  .select_related('event', 'assigned_staff').order_by('pk'))
    from apps.staffing.models import StaffProfile
    nowait = connection.features.has_select_for_update_nowait
    list(StaffProfile.objects.select_for_update(nowait=nowait).filter(
        pk__in={task.assigned_staff_id for task in locked}).order_by('pk'))
    if any(primary_event(task) and primary_event(task).pk not in event_ids for task in locked):
        raise RuntimeError('Task event links changed during delivery; retry required.')
    # Staff-account edits already lock users before profiles. Retry instead of
    # waiting here while holding a profile that such an edit may need.
    user = get_user_model().objects.select_for_update(nowait=nowait).filter(pk=claimed.recipient_id).first()
    job = TaskReminderJob.objects.select_for_update().get(pk=claimed.pk)
    if job.state != 'claimed' or job.claim_token != claimed.claim_token:
        return 'lost_claim'
    if not user or not task_access.staff_user(user) or not _current_day(job, now):
        finish(job, 'superseded', now, worker_id)
        return 'superseded'
    allowed = set(task_access.tasks_for(user).filter(pk__in=ids).values_list('pk', flat=True))
    tasks = [t for t in locked if t.pk in allowed]
    # A previous atomic delivery can be recovered without issuing another notice.
    if TaskNotification.objects.filter(reminder_job=job).exists():
        finish(job, 'delivered', now, worker_id)
        return 'delivered'
    if job.kind != 'summary':
        task = next((t for t in tasks if t.pk == job.task_id), None)
        if (task is None or fingerprint(task) != job.fingerprint or
                recipient_id(task) != user.pk or task.status not in OPEN or task.archived_at or
                not scope_matches(task, job) or not calendar_allowed(task, job.local_date)):
            finish(job, 'superseded', now, worker_id)
            return 'superseded'
        if snoozed(task, user, now):
            raw = preference(user).snoozes[str(task.pk)]
            from django.utils.dateparse import parse_datetime
            job.state, job.available_at = 'pending', parse_datetime(raw)
            job.claim_token, job.lease_until = None, None
            job.save(update_fields=['state', 'available_at', 'claim_token', 'lease_until', 'updated_at'])
            return 'snoozed'
        if job.kind == 'advance' and not policy_values(primary_event(task))['advance_enabled']:
            finish(job, 'superseded', now, worker_id)
            return 'superseded'
        proposals = [_proposal(job, task, job.kind)]
    else:
        proposals = [*_required_proposals(job, user, tasks, now),
                     *_activity_proposals(job, user, tasks, now)]
        job.through_at = now
    keys = [p['dedupe_key'] for p in proposals]
    covered = set(TaskReminderItem.objects.filter(dedupe_key__in=keys).values_list('dedupe_key', flat=True))
    proposals = [p for p in proposals if p['dedupe_key'] not in covered]
    if not proposals:
        finish(job, 'empty' if job.kind == 'summary' else 'covered', now, worker_id)
        return job.state
    TaskReminderItem.objects.bulk_create([TaskReminderItem(job=job, **p) for p in proposals])
    TaskNotification.objects.create(recipient=user, task_id=job.task_id, reminder_job=job,
        kind=job.kind, message='Morning summary' if job.kind == 'summary' else
        ('Task due' if job.kind == 'due' else 'Upcoming task'))
    finish(job, 'delivered', now, worker_id, count=len(proposals))
    return 'delivered'


def retry_job(claimed, error, now, worker_id):
    with transaction.atomic():
        job = TaskReminderJob.objects.select_for_update().get(pk=claimed.pk)
        if job.state != 'claimed' or job.claim_token != claimed.claim_token:
            return
        job.state = 'failed'
        job.available_at = now + timedelta(seconds=min(900, 30 * 2 ** min(job.attempts - 1, 5)))
        job.last_error = (type(error).__name__ + ': ' + str(error))[:500]
        job.claim_token, job.lease_until = None, None
        job.attempt_history = [*(job.attempt_history or [])[-19:], {
            'attempt': job.attempts, 'worker': worker_id, 'finished_at': now.isoformat(),
            'state': 'failed', 'error': job.last_error}]
        job.save(update_fields=['state', 'available_at', 'last_error', 'claim_token',
                                'lease_until', 'attempt_history', 'updated_at'])


def run_tick(*, now=None, worker_id='manual', max_jobs=100, plan_limit=200, automatic=False):
    if not enabled():
        return {'enabled': False, 'processed': 0}
    now = now or timezone.now()
    state, _ = TaskReminderSchedulerState.objects.get_or_create(key='task-reminders')
    heartbeat = dict(last_tick_at=now, worker_id=worker_id)
    TaskReminderSchedulerState.objects.filter(pk=state.pk).update(**heartbeat)
    try:
        planned = plan_tick(now, limit=plan_limit)
    except Exception as error:
        TaskReminderSchedulerState.objects.filter(pk=state.pk).update(
            last_error=(type(error).__name__ + ': ' + str(error))[:500])
        raise
    counts = {}
    for _ in range(max_jobs):
        job = claim_job(now=now, worker_id=worker_id)
        if job is None:
            break
        try:
            outcome = deliver_job(job, now=now, worker_id=worker_id)
        except Exception as error:
            retry_job(job, error, now, worker_id)
            log.exception('Task reminder job will retry: %s', job.pk)
            outcome = 'failed'
        counts[outcome] = counts.get(outcome, 0) + 1
    retry_pending = TaskReminderJob.objects.filter(state='failed',
        local_date__gte=now.date()-timedelta(days=1)).exists()
    failed = counts.get('failed') or planned['failed'] or retry_pending
    updates = dict(last_jobs_processed=sum(counts.values()),
                   last_error='A reminder job needs retry.' if counts.get('failed') or retry_pending else
                   ('Task reminder planning needs retry.' if planned['failed'] else ''))
    if not failed:
        updates['last_success_at'] = now
        if automatic:
            updates.update(last_automatic_at=now, automatic_worker_id=worker_id)
    TaskReminderSchedulerState.objects.filter(pk=state.pk).update(**updates)
    return {'enabled': True, 'planned': planned['tasks'], 'planning_failures': planned['failed'],
            'processed': sum(counts.values()), 'outcomes': counts}
