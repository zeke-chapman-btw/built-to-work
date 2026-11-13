"""Read-only scheduler inspection; HTTP callers receive no task or user details."""
import os
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from .models import TaskReminderJob, TaskReminderSchedulerState
from .reminder_preferences import enabled


def inspect_scheduler(*, now=None, include_internal=False):
    from .reminder_runtime import permitted
    now = now or timezone.now()
    state = TaskReminderSchedulerState.objects.filter(pk='task-reminders').values().first()
    automatic = state['last_automatic_at'] if state else None
    age = (now - automatic).total_seconds() if automatic else None
    grouped = dict(TaskReminderJob.objects.values('state').annotate(total=Count('pk'))
                   .values_list('state', 'total'))
    retry_pending = TaskReminderJob.objects.filter(state='failed',
        local_date__gte=now.date()-timedelta(days=1)).count()
    result = {
        'commit': os.environ.get('RENDER_GIT_COMMIT', ''),
        'enabled': enabled(),
        'automatic_worker_permitted': permitted(),
        'automatic_healthy': bool(enabled() and age is not None and 0 <= age < 150
                                  and not state['last_error'] and not retry_pending),
        'automatic_age_seconds': round(age, 1) if age is not None else None,
        'last_automatic_at': automatic,
        'last_success_at': state['last_success_at'] if state else None,
        'automatic_worker': state['automatic_worker_id'] if state else '',
        'retry_pending': retry_pending,
        'jobs_by_state': {key: grouped.get(key, 0) for key in TaskReminderJob.State.values},
    }
    if include_internal:
        result['state'] = state
    return result
