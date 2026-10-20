"""In-app task notifications sourced from new audit events; no background delivery."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from apps.core.models import AuditLog
from .models import Task, TaskNotification
from .task_access import tasks_for, staff_user


def visible_notifications(user):
    return TaskNotification.objects.filter(recipient=user, task__in=tasks_for(user)).select_related('task').order_by('-created_at','-pk')


def notification_event(entry):
    """Only meaningful events; task audit remains the source of what changed."""
    action=entry.action.removeprefix('operations.task.')
    old,new=entry.old_data or {},entry.new_data or {}
    if action=='created':return ('assigned','A task was assigned to you.',False)
    if action=='reassigned':return ('reassigned','The task assignment changed.',True)
    if action in ('comment.created','comment.edited','comment.moderated'):
        return ('comment','A comment was added.' if action=='comment.created' else 'A comment was updated.',True)
    if action in ('completed','reopened','status'):
        label=dict(Task.Status.choices).get(new.get('status'),'updated')
        return ('status','Task status changed to '+label+'.',True)
    if action=='updated' and any(old.get(k)!=new.get(k) for k in ('due_date','priority')):
        return ('changed','The task due date or priority changed.',True)
    if action in ('archived','restored'):
        return (action,'The task was '+action+'.',True)
    return None


@transaction.atomic
def notify_from_audit(entry, task):
    event=notification_event(entry)
    if not event:return
    kind,message,include_creator=event
    recipients={task.assigned_staff.user_id}
    if include_creator:recipients.add(task.created_by_id)
    recipients.discard(None);recipients.discard(entry.actor_id)
    for user in get_user_model().objects.filter(pk__in=recipients,is_active=True,is_staff=True):
        # Role membership is current; explicit administrators aren't broadcast recipients.
        if tasks_for(user).filter(pk=task.pk).exists():
            TaskNotification.objects.get_or_create(source_event=entry,recipient=user,defaults={'task':task,'kind':kind,'message':message})


@receiver(post_save,sender=AuditLog,dispatch_uid='operations.task-notifications.9g2d')
def task_audit_notification(sender,instance,created,raw=False,**kwargs):
    # Actorless historical imports/migrations are history, not new staff actions.
    if not created or raw or instance.actor_id is None or not instance.action.startswith('operations.task.'):
        return
    if instance.content_type.app_label!='operations' or instance.content_type.model!='task':return
    try:task=Task.objects.select_related('assigned_staff','created_by').get(pk=instance.object_id)
    except (Task.DoesNotExist,ValueError,ValidationError):return
    # Existing task/comment services write their audit within the same atomic transaction.
    notify_from_audit(instance,task)


def mark_read(user,notification_id):
    return visible_notifications(user).filter(pk=notification_id,read_at__isnull=True).update(read_at=timezone.now())


def mark_all_read(user):
    return visible_notifications(user).filter(read_at__isnull=True).update(read_at=timezone.now())


def shell_context(user):
    if not staff_user(user):return {}
    from .due_dates import due_attention
    visible=visible_notifications(user)
    due=due_attention(user)
    return {'task_updates':{'unread':visible.filter(read_at__isnull=True).count(),'recent':list(visible[:5]),
                           'due_count':due['overdue_count']+due['soon_count']}}
