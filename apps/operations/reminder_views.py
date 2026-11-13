from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from . import notifications, task_access
from .models import TaskNotification, TaskReminderPolicy
from .reminder_forms import PersonalReminderForm, ReminderPolicyForm, WeekendWorkForm
from . import reminder_preferences as prefs
from .views import staff_view


def available():
    if not prefs.enabled():
        raise PermissionDenied


def revision(obj):
    return obj.updated_at.isoformat() if obj else 'new'


@staff_view
def personal(request):
    available()
    actor = prefs.current_user(request.user)
    obj = prefs.preference(actor)
    form = PersonalReminderForm(request.POST if request.method == 'POST' else None,
        initial={'revision': revision(obj), 'activity_updates': obj is None or obj.activity_updates is not False})
    if request.method == 'POST' and form.is_valid():
        prefs.save_personal(actor=actor, activity_updates=form.cleaned_data['activity_updates'],
                            expected_revision=form.cleaned_data['revision'])
        messages.success(request, 'Reminder preferences saved.')
        return redirect('operations:reminder-preferences')
    events = [event for event in task_access.events_for(actor).exclude(status='archived')
              if prefs.can_manage_event(actor, event)]
    return render(request, 'operations/reminder_settings.html', dict(page_title='Reminders', form=form,
        company_settings=actor.is_superuser, reminder_events=events, personal_settings=True,
        back=reverse('internal:settings')))


@staff_view
def policy(request, event_id=None):
    available()
    actor = prefs.current_user(request.user)
    event = get_object_or_404(task_access.events_for(actor), pk=event_id) if event_id else None
    if (event and not prefs.can_manage_event(actor, event)) or (event is None and not actor.is_superuser):
        raise PermissionDenied
    key = 'event:' + str(event.pk) if event else 'company'
    obj = TaskReminderPolicy.objects.filter(pk=key).first()
    initial = {'revision': revision(obj)}
    for field in prefs.POLICY_FIELDS:
        value = getattr(obj, field) if obj else (None if event else True)
        initial[field] = 'inherit' if value is None else 'true' if value else 'false'
    form = ReminderPolicyForm(request.POST if request.method == 'POST' else None, initial=initial, event=event)
    if request.method == 'POST' and form.is_valid():
        prefs.save_policy(actor=actor, values=form.cleaned_data,
                          expected_revision=form.cleaned_data['revision'], event=event)
        messages.success(request, 'Reminder settings saved.')
        return redirect(request.path)
    return render(request, 'operations/reminder_settings.html', dict(page_title='Event reminders' if event else 'Company reminders',
        form=form, reminder_event=event, back=reverse('operations:reminder-preferences')))


@staff_view
def task_controls(request, task_id):
    available()
    actor = prefs.current_user(request.user)
    task = get_object_or_404(task_access.tasks_for(actor), pk=task_id)
    form = WeekendWorkForm(request.POST if request.method == 'POST' else None,
                          initial={'revision': revision(task), 'work_date': task.weekend_work_date})
    if request.method == 'POST' and form.is_valid():
        prefs.save_weekend_work(actor=actor, task_id=task.pk, work_date=form.cleaned_data['work_date'],
                                expected_revision=form.cleaned_data['revision'])
        messages.success(request, 'Weekend work date saved.')
        return redirect('operations:task-reminders', task_id=task.pk)
    from .execution_models import EventExecution
    from .reminder_calendar import primary_event
    editable = (not task.archived_at and task.status in ('todo','in_progress') and
                primary_event(task) is not None and
                not EventExecution.objects.filter(closeout_task=task).exists())
    return render(request, 'operations/task_reminders.html', dict(page_title='Task reminders', task=task,
        form=form, can_schedule=editable, back=reverse('operations:task', args=[task.pk])))


@staff_view
def snooze(request, notification_id=None, task_id=None):
    available()
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    actor = prefs.current_user(request.user)
    notice = get_object_or_404(notifications.visible_notifications(actor), pk=notification_id) if notification_id else None
    if notice and notice.reminder_job_id:
        from .reminder_rendering import visible_items
        tasks = [item.task for item in visible_items(actor, [notice]).get(notice.reminder_job_id, [])]
    elif notice:
        tasks = [notice.task]
    else:
        tasks = [get_object_or_404(task_access.tasks_for(actor), pk=task_id)]
    with transaction.atomic():
        until = prefs.snooze_tasks(actor=actor, tasks=tasks, choice=request.POST.get('choice'))
        if notice:
            TaskNotification.objects.filter(pk=notice.pk, recipient=actor).update(snoozed_until=until)
    messages.success(request, 'Reminder snoozed.')
    return redirect('operations:task-reminders', task_id=task_id) if task_id else redirect('operations:updates')
