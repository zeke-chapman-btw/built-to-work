"""Explicit, permission-checked reminder settings; no changes to task deadlines."""
from datetime import timedelta, timezone as datetime_timezone

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.roles import manager
from apps.core.audit import record_audit
from . import task_access
from .models import Task, TaskReminderPolicy, TaskReminderPreference
from .reminder_calendar import next_applicable_morning, primary_event

POLICY_FIELDS = ('advance_enabled', 'activity_enabled', 'completion_enabled')
_UNSET = object()


def enabled():
    return bool(getattr(settings, 'BTW_TASK_REMINDERS_ENABLED', False))


def current_user(actor, *, lock=False):
    if not getattr(actor, 'pk', None):
        raise PermissionDenied
    query = get_user_model().objects.select_for_update() if lock else get_user_model().objects
    user = query.filter(pk=actor.pk).first()
    if user is None:
        raise PermissionDenied
    task_access.require_task_staff(user)
    return user


def policy_values(event=None):
    keys = ['company'] + (['event:' + str(event.pk)] if event else [])
    policies = {p.key: p for p in TaskReminderPolicy.objects.filter(key__in=keys)}
    company = policies.get('company')
    result = {field: getattr(company, field) if company and getattr(company, field) is not None
              else True for field in POLICY_FIELDS}
    override = policies.get(keys[-1]) if event else None
    if override:
        for field in POLICY_FIELDS:
            if getattr(override, field) is not None:
                result[field] = getattr(override, field)
    return result


def preference(user):
    return TaskReminderPreference.objects.filter(user=user).first()


def activity_allowed(user, *, event=None, completed=False):
    values = policy_values(event)
    if not values['completion_enabled' if completed else 'activity_enabled']:
        return False
    item = preference(user)
    return item is None or item.activity_updates is not False


def snoozed(task, user, now=None, *, preferences=_UNSET):
    item = preference(user) if preferences is _UNSET else preferences
    value = (item.snoozes or {}).get(str(task.pk)) if item else None
    try:
        until = parse_datetime(value) if isinstance(value, str) else None
        return bool(until and timezone.is_aware(until) and until > (now or timezone.now()))
    except (TypeError, ValueError):
        return False


def check_revision(obj, value):
    expected = obj.updated_at.isoformat() if obj and obj.updated_at else 'new'
    if value != expected:
        raise ValidationError('These settings changed. Reload before saving.')


def can_manage_event(actor, event):
    from .services import event_access, primary_event_manager
    if not (manager(actor) or primary_event_manager(actor, event)):
        return False
    try:
        event_access(actor, event, 'tasks')
    except PermissionDenied:
        return False
    return True


@transaction.atomic
def save_policy(*, actor, values, expected_revision, event=None):
    actor = current_user(actor)
    if event:
        from apps.events.models import Event
        event = Event.objects.select_for_update().get(pk=event.pk)
        if not can_manage_event(actor, event):
            raise PermissionDenied
    elif not actor.is_superuser:
        raise PermissionDenied
    # Serialize creation of an absent singleton as well as subsequent edits.
    actor = current_user(actor, lock=True)
    key = 'event:' + str(event.pk) if event else 'company'
    if (event and not can_manage_event(actor, event)) or (not event and not actor.is_superuser):
        raise PermissionDenied
    obj, created = TaskReminderPolicy.objects.get_or_create(key=key, defaults={'event': event})
    obj = TaskReminderPolicy.objects.select_for_update().get(pk=obj.pk)
    check_revision(None if created else obj, expected_revision)
    before = {field: getattr(obj, field) for field in POLICY_FIELDS} if not created else None
    for field in POLICY_FIELDS:
        value = values.get(field)
        if value not in (True, False, None) or (not event and value is None):
            raise ValidationError('Choose a reminder setting.')
        setattr(obj, field, value)
    obj.full_clean()
    obj.save()
    record_audit(actor=actor, action='operations.reminders.policy_updated', instance=obj,
                 old_data=before, new_data={field: getattr(obj, field) for field in POLICY_FIELDS},
                 source='staff', reason='Reminder settings updated.')
    return obj


@transaction.atomic
def save_personal(*, actor, activity_updates, expected_revision):
    actor = current_user(actor, lock=True)
    obj = TaskReminderPreference.objects.select_for_update().filter(user=actor).first()
    check_revision(obj, expected_revision)
    if type(activity_updates) is not bool:
        raise ValidationError('Choose whether to receive activity updates.')
    before = {'activity_updates': obj.activity_updates} if obj else None
    obj = obj or TaskReminderPreference(user=actor)
    obj.activity_updates = activity_updates
    obj.save()
    record_audit(actor=actor, action='operations.reminders.preferences_updated', instance=obj,
                 old_data=before, new_data={'activity_updates': activity_updates},
                 source='staff', reason='Personal reminder preferences updated.')
    return obj


@transaction.atomic
def snooze_tasks(*, actor, tasks, choice, now=None):
    now = now or timezone.now()
    actor = current_user(actor, lock=True)
    tasks = list(tasks)
    current = list(task_access.tasks_for(actor).filter(pk__in=[t.pk for t in tasks]))
    allowed = {t.pk for t in current}
    if not tasks or any(t.pk not in allowed for t in tasks):
        raise PermissionDenied
    tasks = current
    if choice not in ('hour', 'morning'):
        raise ValidationError('Choose one hour or the next morning.')
    obj, _ = TaskReminderPreference.objects.get_or_create(user=actor)
    retained = {}
    for key, raw in (obj.snoozes or {}).items():
        try:
            value = parse_datetime(raw)
            if value and timezone.is_aware(value) and value > now:
                retained[key] = raw
        except (TypeError, ValueError):
            continue
    untils = []
    for task in tasks:
        until = (now.astimezone(datetime_timezone.utc) + timedelta(hours=1)
                 if choice == 'hour' else next_applicable_morning(task, now))
        retained[str(task.pk)] = until.isoformat()
        untils.append(until)
    obj.snoozes = retained
    obj.save(update_fields=['snoozes', 'updated_at'])
    record_audit(actor=actor, action='operations.reminders.snoozed', instance=obj,
                 new_data={'tasks': [str(t.pk) for t in tasks], 'until': max(untils).isoformat()},
                 source='staff', reason='Reminder snoozed.')
    return max(untils)


@transaction.atomic
def save_weekend_work(*, actor, task_id, work_date, expected_revision):
    from apps.events.models import Event
    from . import services
    from .execution_services import guard_managed_task
    initial = task_access.tasks_for(actor).filter(pk=task_id).first()
    if initial is None:
        raise PermissionDenied
    ids = set(initial.event_links.values_list('event_id', flat=True))
    if initial.event_id:
        ids.add(initial.event_id)
    list(Event.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    task = Task.objects.select_for_update().get(pk=task_id)
    task_access.require_task_access(actor, task)
    guard_managed_task(task)
    check_revision(task, expected_revision)
    if task.archived_at or task.status in ('completed', 'cancelled'):
        raise ValidationError('Reopen the task before changing its work date.')
    event = primary_event(task)
    services.mutable(event)
    if work_date and (work_date.weekday() < 5 or event is None):
        raise ValidationError('Choose a Saturday or Sunday and link one event to the task.')
    before = services.snap(task)
    if task.weekend_work_date == work_date:
        return task
    task.weekend_work_date = work_date
    task.save(update_fields=['weekend_work_date', 'updated_at'])
    services.audit(actor, 'task.updated', task, before, 'Weekend work date updated.')
    return task
