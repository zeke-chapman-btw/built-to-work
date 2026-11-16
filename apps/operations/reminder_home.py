"""Exact current-summary coverage for the accepted Home Needs attention list."""

from django.utils import timezone

from .models import TaskNotification
from .reminder_rendering import visible_items


def coverage(user, *, now=None):
    from .event_reminder_sources import enabled
    result = {'tasks': set(), 'staffing': set(), 'staffing-review': set(),
              'job-review': set(), 'closeout': set()}
    if not enabled():
        return result
    now = now or timezone.now()
    notices = list(TaskNotification.objects.filter(recipient=user,
        reminder_job__kind='summary').select_related('reminder_job'))
    # Read and snooze do not cause the same work to become a second Home alert.
    for items in visible_items(user, notices, now=now, include_snoozed=True).values():
        for item in items:
            if item.task_id:
                if item.kind in ('due', 'overdue', 'advance', 'work', 'prep'):
                    result['tasks'].add(item.task_id)
                continue
            ref = item.source_ref
            if ref['type'] == 'staffing':
                result['staffing'].add((ref['event'], ref['date'], ref['role']))
            elif ref['type'] == 'closeout':
                result['closeout'].add(ref['event'])
                from .execution_models import EventExecution
                task_id = EventExecution.objects.filter(event_id=ref['event']).values_list('closeout_task_id', flat=True).first()
                if task_id:
                    result['tasks'].add(task_id)
            elif ref['type'] in ('staffing-review', 'job-review'):
                result[ref['type']].add(ref['id'])
    return result


def covered_staffing_events(events, covered):
    from apps.staffing.services import coverage as staffing_coverage
    result = []
    for event in events:
        gaps = {(str(event.pk), row['date'].isoformat(), role['key'])
                for row in staffing_coverage(event)['days'] for role in row['roles'] if role['remaining']}
        if gaps and gaps.issubset(covered):
            result.append(event.pk)
    return result
