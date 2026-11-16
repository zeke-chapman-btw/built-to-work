from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseNotAllowed, JsonResponse
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
def status(request):
    actor = prefs.current_user(request.user)
    if not actor.is_superuser:
        raise PermissionDenied
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])
    from .reminder_health import inspect_scheduler
    response = JsonResponse(inspect_scheduler())
    response['Cache-Control'] = 'no-store, private'
    return response


@staff_view
def personal(request):
    available()
    actor = prefs.current_user(request.user)
    obj = prefs.preference(actor)
    form = PersonalReminderForm(request.POST if request.method == 'POST' else None,
        initial={'revision': revision(obj), 'activity_updates': obj is None or obj.activity_updates is not False})
    if request.method == 'POST' and form.is_valid():
        try:
            prefs.save_personal(actor=actor, activity_updates=form.cleaned_data['activity_updates'],
                                expected_revision=form.cleaned_data['revision'])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Reminder preferences saved.')
            from apps.internal_ui.section_editors import operations_saved
            return operations_saved(request, reverse('operations:reminder-preferences'))
    if request.GET.get('edit') == '1' or request.method == 'POST':
        from apps.internal_ui.section_editors import operations_editor
        return operations_editor(request, form, title='Your reminders',
            back=reverse('operations:reminder-preferences'))
    events = [event for event in task_access.events_for(actor).exclude(status='archived')
              if prefs.can_manage_event(actor, event)]
    return render(request, 'operations/reminder_settings.html', dict(page_title='Reminders', form=form,
        company_settings=actor.is_superuser, reminder_events=events, personal_settings=True,
        reminder_setup=responsibility_setup(actor),
        back=reverse('internal:settings')))


def responsibility_setup(actor):
    from .event_reminder_sources import enabled
    if not actor.is_superuser or not enabled():
        return []
    from .models import TaskReminderSchedulerState
    from .execution_models import EventChecklistItem
    state = TaskReminderSchedulerState.objects.filter(pk='task-reminders').first()
    issues = (state.responsibility_health or {}).get('issues', []) if state else []
    result, seen = [], set()
    for issue in issues[:50]:
        event_id = issue.get('id') if issue.get('category') == 'event-owner' or issue.get('code') == 'owner-access' else None
        if issue.get('category') == 'preparation':
            event_id = EventChecklistItem.objects.filter(pk=issue.get('id')).values_list('category__execution__event_id', flat=True).first()
        if event_id:
            event = task_access.events_for(actor).filter(pk=event_id).first()
            if event is None:
                continue
            label = event.name + ' · Review responsibility'
            route = 'operations:health' if issue.get('category') == 'preparation' else 'operations:execution'
            url = reverse(route, args=[event.pk])
        else:
            category = {'staffing': 'Staffing', 'verification': 'Verification',
                        'jobs': 'Job-posting', 'customers': 'Customer-approval'}.get(issue.get('category'))
            if not category:
                continue
            label = category + ' recipient · Choose an eligible account'
            url = reverse('operations:reminder-company')
        if (label, url) not in seen:
            result.append({'label': label, 'url': url, 'popup': not event_id})
            seen.add((label, url))
    return result


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
    from . import event_reminder_policy as source_policy
    for field in (*source_policy.EVENT_BOOLEANS,
                  *(() if event else source_policy.COMPANY_BOOLEANS)):
        value = getattr(obj, field) if obj else None
        if value is None and not event:
            value = source_policy.DEFAULTS[field]
        initial[field] = 'inherit' if value is None else 'true' if value else 'false'
    for field in source_policy.TIMINGS:
        value = getattr(obj, field) if obj else None
        if value is None and not event:
            value = source_policy.DEFAULTS[field]
        initial[field] = ('inherit' if value is None else ','.join(map(str, value))
                          if isinstance(value, (list, tuple)) else str(value))
    if not event:
        for field in source_policy.RECIPIENT_FIELDS.values():
            initial[field] = getattr(obj, field + '_id') if obj else None
    form = ReminderPolicyForm(request.POST if request.method == 'POST' else None, initial=initial, event=event)
    if request.method == 'POST' and form.is_valid():
        try:
            prefs.save_policy(actor=actor, values=form.cleaned_data,
                              expected_revision=form.cleaned_data['revision'], event=event)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            from apps.internal_ui.section_editors import operations_saved
            messages.success(request, 'Reminder settings saved.')
            return operations_saved(request, reverse('operations:reminder-preferences'))
    from apps.internal_ui.section_editors import operations_editor
    return operations_editor(request, form,
        title='Event reminders' if event else 'Company reminders',
        back=reverse('operations:reminder-preferences'),
        fields_template='operations/reminder_policy_fields.html')


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
    source_items = []
    if notice and notice.reminder_job_id:
        from .reminder_rendering import visible_items
        items = visible_items(actor, [notice]).get(notice.reminder_job_id, [])
        tasks = [item.task for item in items if item.task_id]
        source_items = [item for item in items if not item.task_id]
    elif notice:
        tasks = [notice.task]
    else:
        tasks = [get_object_or_404(task_access.tasks_for(actor), pk=task_id)]
    with transaction.atomic():
        untils = []
        if tasks:
            untils.append(prefs.snooze_tasks(actor=actor, tasks=tasks, choice=request.POST.get('choice')))
        if source_items:
            from .event_reminder_snooze import snooze_sources
            untils.append(snooze_sources(actor=actor, items=source_items,
                choice=request.POST.get('choice'), now=timezone.now()))
        if not untils:
            raise PermissionDenied
        until = max(untils)
        if notice:
            TaskNotification.objects.filter(pk=notice.pk, recipient=actor).update(snoozed_until=until)
    messages.success(request, 'Reminder snoozed.')
    return redirect('operations:task-reminders', task_id=task_id) if task_id else redirect('operations:updates')
