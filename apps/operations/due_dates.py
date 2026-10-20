"""Live calendar-date awareness. No durable reminders or scheduled jobs."""
from datetime import timedelta
from django.db.models import Q
from django.utils import timezone
from .task_access import tasks_for
DUE_SOON_DAYS=3

def due_state(task,today=None):
    today=today or timezone.localdate()
    if task.archived_at or task.status not in ('todo','in_progress') or not task.due_date:return ''
    if task.due_date<today:return 'overdue'
    if task.due_date<=today+timedelta(days=DUE_SOON_DAYS):return 'soon'
    return ''

def due_attention(user):
    today=timezone.localdate()
    # Daily attention is directed to creators/current assignees, not every admin-visible task.
    base=tasks_for(user).filter(Q(created_by=user)|Q(assigned_staff__user=user),archived_at__isnull=True,
        status__in=('todo','in_progress')).select_related('assigned_staff').order_by('due_date','pk')
    overdue=base.filter(due_date__lt=today)
    soon=base.filter(due_date__range=(today,today+timedelta(days=DUE_SOON_DAYS)))
    return {'overdue_count':overdue.count(),'soon_count':soon.count(),'overdue':list(overdue[:5]),'soon':list(soon[:5]),'today':today}
