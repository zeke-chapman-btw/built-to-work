"""Render reminders from current authorized records, never frozen task details."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from . import task_access
from .models import TaskNotification, TaskReminderItem
from .reminder_calendar import next_working_day, primary_event
from .reminder_planning import OPEN, item_fingerprint, recipient_id
from .reminder_preferences import policy_values, preference, snoozed

REQUIRED = ('advance', 'due', 'overdue', 'work', 'prep')
LABELS = {'advance': 'Due next working day', 'due': 'Due today', 'overdue': 'Overdue',
          'work': 'Event work today', 'prep': 'Event preparation',
          'completed': 'Completed', 'activity': 'Updated'}
SECTIONS = {'advance': 'Coming up', 'prep': 'Coming up', 'due': 'Today',
            'overdue': 'Today', 'work': 'Today', 'completed': 'Since your last summary',
            'activity': 'Since your last summary'}


def fresh_staff(user):
    fresh = get_user_model().objects.filter(pk=getattr(user, 'pk', None)).first()
    return fresh if fresh and task_access.staff_user(fresh) else None


def hydrate(tasks):
    """Resolve current relationships in batches, not once per history receipt."""
    from apps.staffing.models import StaffProfile
    from .models import TaskEventLink
    tasks = list(tasks)
    if not tasks:
        return
    owners = dict(StaffProfile.objects.filter(pk__in={t.assigned_staff_id for t in tasks})
                  .values_list('pk', 'user_id'))
    links = {}
    for link in TaskEventLink.objects.filter(task_id__in={t.pk for t in tasks if not t.event_id}).select_related('event'):
        links.setdefault(link.task_id, []).append(link.event)
    for task in tasks:
        events = links.get(task.pk, [])
        task._reminder_primary_event = task.event if task.event_id else (events[0] if len(events) == 1 else None)
        task._reminder_recipient_id = owners.get(task.assigned_staff_id)


def permissions(preferences):
    policies = {}
    def values(task):
        event = primary_event(task)
        key = event.pk if event else None
        if key not in policies:
            policies[key] = policy_values(event)
        return policies[key]
    def optional(task, completed=False):
        return ((preferences is None or preferences.activity_updates is not False) and
                values(task)['completion_enabled' if completed else 'activity_enabled'])
    return values, optional


def visible_items(user, notices, *, now=None):
    now = now or timezone.now()
    user = fresh_staff(user)
    if user is None:
        return {}
    jobs = [n.reminder_job_id for n in notices if n.reminder_job_id]
    items = list(TaskReminderItem.objects.filter(job_id__in=jobs, task__in=task_access.tasks_for(user))
                 .select_related('job', 'task', 'task__event', 'task__assigned_staff'))
    preferences = preference(user)
    hydrate(item.task for item in items)
    tasks = {item.task_id: item.task for item in items}
    fingerprints = {}
    def expected(task_id, kind):
        key = (task_id, kind)
        if key not in fingerprints:
            fingerprints[key] = item_fingerprint(tasks[task_id], kind)
        return fingerprints[key]
    values, optional = permissions(preferences)
    latest = {}
    task_ids = {i.task_id for i in items if i.kind in REQUIRED}
    # Newer required coverage replaces older reminders without changing read state.
    receipts = (TaskReminderItem.objects.filter(task_id__in=task_ids, kind__in=REQUIRED,
                job__recipient=user, job__notification__isnull=False)
                .order_by('-job__scheduled_for', '-created_at'))
    for receipt in receipts:
        if receipt.fingerprint == expected(receipt.task_id, receipt.kind):
            latest.setdefault(receipt.task_id, receipt.job_id)
    result = {}
    for item in items:
        task = item.task
        if expected(task.pk, item.kind) != item.fingerprint or snoozed(task, user, now, preferences=preferences):
            continue
        if item.kind in REQUIRED:
            if item.kind == 'advance' and not values(task)['advance_enabled']:
                continue
            if (task.archived_at or task.status not in OPEN or recipient_id(task) != user.pk or
                    latest.get(task.pk) != item.job_id):
                continue
        elif task.archived_at or task.status == 'cancelled' or not optional(task, item.kind == 'completed'):
            continue
        result.setdefault(item.job_id, []).append(item)
    return result


def visible_queryset(user):
    user = fresh_staff(user)
    if user is None:
        return TaskNotification.objects.none()
    now = timezone.now()
    base = (TaskNotification.objects.filter(recipient=user)
            .filter(Q(snoozed_until__isnull=True) | Q(snoozed_until__lte=now))
            .select_related('task', 'task__assigned_staff', 'task__event', 'reminder_job', 'reminder_job__event'))
    system = list(base.filter(reminder_job__isnull=False))
    items = visible_items(user, system, now=now)
    ids = [n.pk for n in system if items.get(n.reminder_job_id)]
    preferences = preference(user)
    legacy = list(base.filter(reminder_job__isnull=True, task__in=task_access.tasks_for(user)))
    hydrate(notice.task for notice in legacy)
    values, optional = permissions(preferences)
    for notice in legacy:
        task = notice.task
        if snoozed(task, user, now, preferences=preferences):
            continue
        required = notice.kind in ('assigned', 'reassigned') and recipient_id(task) == user.pk
        if notice.kind == 'assigned' and not required:
            continue
        if required and (task.archived_at or task.status not in OPEN):
            continue
        if notice.kind in ('assigned', 'reassigned') and not required and task.created_by_id != user.pk:
            continue
        if required or optional(task, task.status == 'completed'):
            ids.append(notice.pk)
    return base.filter(pk__in=ids).order_by('-created_at', '-pk')


def decorate(user, notices):
    notices = list(notices)
    system_items = visible_items(user, notices) if any(n.reminder_job_id for n in notices) else {}
    events = set()
    if any(notice.reminder_job_id and notice.reminder_job.event_id for notice in notices):
        try:
            events = set(task_access.events_for(user).values_list('pk', flat=True))
        except (DatabaseError, PermissionDenied):
            # Task access was checked independently. Fail closed on event names.
            pass
    for notice in notices:
        notice.summary_sections = []
        if not notice.reminder_job_id:
            notice.display_title = notice.task.title
            notice.destination = reverse('operations:task', args=[notice.task_id])
            continue
        job = notice.reminder_job
        items = system_items.get(job.pk, [])
        rows = {}
        for item in sorted(items, key=lambda i: (i.kind not in REQUIRED, LABELS.get(i.kind, ''), i.task.title)):
            if item.task_id in rows:
                continue
            label = LABELS[item.kind]
            if item.kind in ('advance', 'due') and item.task.due_date:
                fmt = 'M j' if item.task.due_date.year == timezone.localdate().year else 'M j, Y'
                label = 'Due ' + date_format(item.task.due_date, fmt)
            work = item.task.weekend_work_date
            if work and (item.kind in ('work', 'prep') or
                         (item.kind == 'advance' and job.local_date <= work <= next_working_day(job.local_date))):
                fmt = 'M j' if work.year == timezone.localdate().year else 'M j, Y'
                label = (label + ' · ' if item.kind == 'advance' else '') + 'Work ' + date_format(work, fmt)
            rows[item.task_id] = dict(title=item.task.title, label=label,
                destination=reverse('operations:task', args=[item.task_id]), section=SECTIONS[item.kind])
        for section in ('Today', 'Coming up', 'Since your last summary'):
            values = [row for row in rows.values() if row['section'] == section]
            if values:
                notice.summary_sections.append({'title': section, 'rows': values})
        event = job.event
        scope_current = event and all(primary_event(item.task) and primary_event(item.task).pk == event.pk for item in items)
        notice.scope_label = ((event.name if event.pk in events else 'Event work') + ' · ' + job.timezone_name) if scope_current else ''
        notice.display_title = ('Morning summary' if job.kind == 'summary' else
                                (rows[next(iter(rows))]['title'] if rows else 'Task reminder'))
        notice.destination = (reverse('operations:updates') + '#notice-' + str(notice.pk)
                              if job.kind == 'summary' else reverse('operations:task', args=[job.task_id]))
        notice.item_count = len(rows)
    return notices
