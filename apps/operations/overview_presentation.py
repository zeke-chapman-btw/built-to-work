"""Read-only Operations overview; existing visibility and checklist rules own facts."""
from django.db.models import Case, IntegerField, Q, Value, When
from django.urls import reverse
from django.utils import timezone
from . import execution_services as execution
from .execution_models import EventExecution
from .task_access import events_for


def operational_events(user):
    """Presentation default; all stored lifecycle states remain available in All events."""
    now = timezone.now()
    return events_for(user).filter(
        Q(status="upcoming") | Q(status="completed", execution__actual_ended_at__isnull=False,
                                  execution__closed_at__isnull=True)
    ).annotate(overview_order=Case(
        When(status="upcoming", start_at__lte=now, end_at__gte=now, then=Value(0)),
        When(status="upcoming", end_at__lt=now, then=Value(1)),
        When(status="completed", then=Value(2)), default=Value(3), output_field=IntegerField()
    )).order_by("overview_order", "start_at", "pk")


def end_attention_events(user):
    """Existing scheduled-end reminder candidates, restricted to existing end-action access."""
    visible = events_for(user).filter(pk__in=execution.end_reminder_candidates().values("pk"))
    if not execution.administrator(user):
        visible = visible.filter(execution__primary_manager__user=user,
                                 execution__primary_manager__is_active=True)
    return visible.order_by("end_at", "pk")


def overview_rows(user, events):
    """Never initialize a checklist or infer readiness from a missing/empty snapshot."""
    events = list(events)
    snapshots = {row.event_id: row for row in EventExecution.objects.filter(
        event_id__in=[event.pk for event in events]).select_related("primary_manager__user")}
    now = timezone.now()
    rows = []
    for event in events:
        snapshot = snapshots.get(event.pk)
        visible = execution.can_view(user, event)
        route = "operations:execution" if visible else "staffing:staff-event"
        rows.append(dict(event=event, execution=snapshot, url=reverse(route, args=[event.pk]),
            execution_visible=visible,
            manager=snapshot.primary_manager if snapshot and snapshot.primary_manager_id else None,
            indicators=execution.progress(snapshot) if visible and snapshot else [],
            scheduled_end_overdue=event.status == "upcoming" and event.end_at < now
                and not (snapshot and snapshot.actual_ended_at),
            health_url=reverse("operations:health", args=[event.pk]) if visible else None,
            closeout_url=reverse("operations:closeout", args=[event.pk]) if visible else None))
    return rows


def resource_overview_rows(user, rows):
    """Display existing configuration facts; no new Ready rule or checklist writes."""
    from django.core.exceptions import PermissionDenied, ValidationError
    from django.db import DatabaseError
    from .setup_presentation import setup_context
    from .template_models import KINDS
    from .template_services import can_view_setup
    rows = list(rows)
    event_ids = [row['event'].pk for row in rows]
    # Form Builder is currently admin-only. Do not expose its assignment evidence to other roles.
    assigned_forms = set()
    from apps.participants.intake_lifecycle import require_admin
    try:
        require_admin(user)
        forms_allowed = True
    except PermissionDenied:
        forms_allowed = False
    forms_available = forms_allowed
    if forms_available:
        from apps.participants.intake_models import IntakeFormAssignment
        try:
            assigned_forms = set(IntakeFormAssignment.objects.filter(
                event_id__in=event_ids, channel='event', is_active=True,
                form__is_active=True, form__is_smart_intake=True,
                active_version__status='published').values_list('event_id', flat=True))
        except DatabaseError:
            forms_available = False
    result = []
    for original in rows:
        row = dict(original)
        event = row['event']
        checks = [dict(label='Forms', icon='resource-form',
            state='Assigned' if forms_available and event.pk in assigned_forms else 'Unknown' if forms_allowed else 'Restricted',
            detail='Active published event-linked form assignment; general online forms and trailer activation are separate.' if event.pk in assigned_forms else 'Event-linked form availability is not established here. General online forms and local trailer activation are separate.',
            url=reverse('participants:intake-config-index') if forms_allowed else None)]
        setup = {}
        unavailable = False
        if can_view_setup(user):
            try:
                setup = setup_context(user, event)
            except (DatabaseError, ValidationError):
                unavailable = True
        by_kind = {kind: item for (kind, _), item in zip(KINDS, setup.get('setup_rows', []))}
        for key, label, icon in (('assessment', 'Quizzes', 'resource-quiz'), ('game', 'Games', 'resource-game'), ('simulator', 'Simulators', 'resource-simulator')):
            item = (setup.get('setup_rows') or [None])[-1] if key == 'simulator' else by_kind.get(key)
            state = item['state'] if item else 'Unknown' if unavailable else 'Restricted'
            display = {'Not Configured': 'Not set', 'Not Applicable': 'N/A'}.get(state, state)
            checks.append(dict(label=label, icon=icon, state=display,
                detail=item['detail'] if item else 'Configuration information is unavailable.' if unavailable else 'Configuration details are unavailable to your role.',
                url=reverse('events:event_detail', args=[event.pk]) + '?section=operations' if setup else None))
        for check in checks:
            check['tone'] = 'configured' if check['state'] in ('Configured', 'Assigned') else 'missing' if check['state'] == 'Not set' else 'unknown'
        row['resource_checks'] = checks
        # Use the established event/schedule destinations. Execution tools remain in the row menu.
        if user.has_perm('events.view_event'):
            row['url'] = reverse('events:event_detail', args=[event.pk])
        result.append(row)
    return result
