"""Bounded Home read models; reuse task visibility, ordering and presentation."""
from django.urls import reverse
from django.utils import timezone
from apps.operations import workspace, task_access


def _work_data(user):
    work = workspace.workspace(user, {}, mine=True)
    # The existing workspace owns visibility and ordering. Home previews open work.
    mine = work["queryset"].filter(status__in=("todo", "in_progress"))
    rows = task_access.present_tasks(user, mine[:4])
    metrics = [
        {"label": "My open tasks", "value": mine.count(),
         "scope": "Assigned to you · not archived", "url": reverse("operations:tasks") + "?view=mine"},
        {"label": "Accessible overdue tasks", "value": work["summary"]["overdue"],
         "scope": "Open tasks you can access · not archived", "url": reverse("operations:tasks") + "?view=all&due=overdue"},
    ]
    return {"home_tasks": rows, "home_task_count": metrics[0]["value"], "home_metrics": metrics}



def _intake_review_count(user):
    """The existing default intake queue, including held registration cases."""
    from django.db.models import Q
    from apps.events.models import ParticipantMatchReview
    return ParticipantMatchReview.objects.filter(
        Q(status="open") | Q(status__in=("resolved", "rejected"), evidence__registration_status="held"),
        source_submission__isnull=False,
    ).count()


def _event_destinations(user, events):
    """Use the existing Operations destinations, with one bulk manager read."""
    if user.has_perm("events.view_event"):
        for event in events:
            event.home_url = reverse("events:event_detail", args=[event.pk])
        return
    from apps.accounts.operational_access import safe_event_worker
    if safe_event_worker(user):
        for event in events:event.home_url=reverse('staffing:general-event',args=[event.pk])
        return
    from apps.operations.execution_models import EventExecution
    managed = set(EventExecution.objects.filter(event_id__in=[e.pk for e in events],
        primary_manager__user=user, primary_manager__is_active=True).values_list("event_id", flat=True))
    for event in events:
        route = "operations:execution" if event.pk in managed else "staffing:staff-event"
        event.home_url = reverse(route, args=[event.pk])


def greeting(user, now=None):
    now = timezone.localtime(now)
    part = "morning" if now.hour < 12 else "afternoon" if now.hour < 17 else "evening"
    name = (getattr(user, "first_name", "") or getattr(user, "username", "")).split(" ")[0]
    return {"home_greeting": f"Good {part}, {name}" if name else f"Good {part}", "home_today": now}


def _staff_counts(user, events):
    """Confirmed people vs. the event-level staffing plan; only for staffing managers."""
    if not events or not user.has_perms(("staffing.manage_event_staffing", "events.view_event")):
        return
    from django.db.models import Count, Q
    from apps.staffing.models import StaffingPlan, StaffAssignment
    plans = {p.event_id: p.required_staff + p.required_drivers for p in StaffingPlan.objects.filter(event_id__in=[e.pk for e in events])}
    assigned = dict(StaffAssignment.objects.filter(event_id__in=[e.pk for e in events], status="confirmed")
                    .values("event_id").annotate(n=Count("staff_id", distinct=True)).values_list("event_id", "n"))
    for event in events:
        event.staff_needed = plans.get(event.pk)
        event.staff_assigned = assigned.get(event.pk, 0)
        event.staff_short = bool(event.staff_needed) and event.staff_assigned < event.staff_needed


def _more_attention(user, unavailable):
    """Extra Needs-attention rows from existing queues; each read uses that queue's own access rule."""
    from django.db import DatabaseError
    from apps.internal_ui.approvals import access_flags
    rows = []
    flags = access_flags(user)
    if flags.get("events"):
        try:
            from apps.events.models import Event
            from apps.staffing.models import AvailabilityRequest, StaffingPlan, StaffAssignment
            from django.db.models import Count
            pending = AvailabilityRequest.objects.filter(status="pending").count()
            if pending:
                rows.append(dict(id="staff-requests", count=pending, label="Staff requests to approve",
                                 action="Review", url=reverse("internal:workspace", args=["approvals"]) + "?tab=events"))
            soon = Event.objects.filter(status=Event.Status.UPCOMING, start_at__gte=timezone.now(),
                                        start_at__lte=timezone.now() + __import__('datetime').timedelta(days=30))
            plans = {p.event_id: p.required_staff + p.required_drivers for p in StaffingPlan.objects.filter(event__in=soon)}
            got = dict(StaffAssignment.objects.filter(event_id__in=plans, status="confirmed").values("event_id")
                       .annotate(n=Count("staff_id", distinct=True)).values_list("event_id", "n"))
            short = sum(1 for event_id, need in plans.items() if need and got.get(event_id, 0) < need)
            if short:
                rows.append(dict(id="short-staffed", count=short, label="Upcoming events short on staff",
                                 action="Assign staff", url=reverse("events:event_list") + "?view=upcoming"))
        except DatabaseError:
            unavailable.append("Staffing check unavailable")
    try:
        from apps.customers.access import is_manager
        if flags.get("jobs") and is_manager(user):
            from apps.customers.models import WorkforceNeedRevision
            waiting = WorkforceNeedRevision.objects.filter(state__in=("submitted", "review")).count()
            if waiting:
                rows.append(dict(id="positions", count=waiting, label="Customer job requests to approve",
                                 action="Review", url=reverse("internal:workspace", args=["approvals"]) + "?tab=jobs"))
    except DatabaseError:
        unavailable.append("Job request check unavailable")
    return rows


def home_data(user):
    """Bounded existing sources; independent failures never become zero."""
    from django.core.exceptions import PermissionDenied
    from django.db import DatabaseError
    from apps.events.models import Event
    from apps.internal_ui.event_presentation import directory_counts
    from apps.event_reports.services import can_report
    from apps.participants.intake_review import require_review

    attention, unavailable = [], []
    try:
        result = _work_data(user)
        result.update(home_work_state="available", home_attention_count=result["home_metrics"][1]["value"])
        if result["home_attention_count"]:
            attention.append(dict(id="tasks", count=result["home_attention_count"],
                label="Overdue tasks", action="View tasks", scope="Open tasks you can access",
                url=reverse("operations:tasks") + "?view=all&due=overdue"))
    except DatabaseError:
        result = dict(home_tasks=[], home_task_count=None, home_metrics=[],
                      home_work_state="unavailable", home_attention_count=None)
        unavailable.append("Overdue task check unavailable")

    # Authorization is the queue's existing guard, not a new Home permission.
    try:
        require_review(user)
    except PermissionDenied:
        result.update(home_intake_state="restricted", home_intake_count=None)
    else:
        try:
            count = _intake_review_count(user)
            result.update(home_intake_state="available", home_intake_count=count)
            if count:
                attention.append(dict(id="intake", count=count, label="Sign-ups to review", action="Review",
                    scope="Current review queue · includes held registrations",
                    url=reverse("participants:intake-review-queue") + "?status=open"))
        except DatabaseError:
            result.update(home_intake_state="unavailable", home_intake_count=None)
            unavailable.append("Intake review check unavailable")
    # Reuse the existing scheduled-end candidate rule and end-action authorization.
    from apps.operations.overview_presentation import end_attention_events
    try:
        end_count = end_attention_events(user).count()
        if end_count:
            attention.append(dict(id="event-end", count=end_count, label="Events ended but not closed out", action="Close out",
                scope="Not marked ended · your existing end-action access",
                url=reverse("operations:execution-index") + "?view=attention"))
    except DatabaseError:
        unavailable.append("Scheduled-end check unavailable")
    # Use exactly the same responsibility, feedback-review and Task boundaries
    # as Recruiting Overview. No new queue or notification state is created.
    try:
        from apps.recruiting.read_models import staff_queues
        # Fail closed before extra identity/role reads for a user who cannot
        # access people at all. Eligible readers still receive the service's
        # fresh role, identity and opportunity checks.
        if not user.has_perm('participants.view_participant'):
            raise PermissionDenied
        recruiting = staff_queues(user, include_followup_tasks=False)
        incoming, responses = recruiting['incoming'].count(), recruiting['pending_responses'].count()
        result.update(home_recruiting_state='available')
        if incoming:
            attention.append(dict(id='recruiting-interest', count=incoming, label='Incoming job interests',
                scope='Active, Interest Received · your accessible recruiting positions',
                url=reverse('recruiting:interests')+'?status=received'))
        if responses:
            attention.append(dict(id='recruiting-response', count=responses, label='Employer responses to review',
                scope='No recorded BTW review · your accessible recruiting positions',
                url=reverse('recruiting:overview')+'#employer-responses'))
    except PermissionDenied:
        result.update(home_recruiting_state='restricted')
    except DatabaseError:
        result.update(home_recruiting_state='unavailable')
        unavailable.append('Recruiting review check unavailable')
    attention.extend(_more_attention(user, unavailable))
    result.update(home_attention=attention, home_attention_unavailable=unavailable)
    # Unique source signals remain links, not invented Tasks or replayed notices.
    result['home_unique_attention'] = [item for item in attention if item['id'] != 'tasks']
    result['home_overdue_attention'] = next((item for item in attention if item['id'] == 'tasks'), None)
    result['home_summary_metrics'] = operational_metrics(user)

    try:
        events = list(task_access.events_for(user).filter(status=Event.Status.UPCOMING,
            end_at__gte=timezone.now()).order_by("start_at", "pk")[:5])
        _staff_counts(user, events)
        _event_destinations(user, events)
        result.update(home_events=events, home_events_state="available",
            home_event_index_url=reverse("events:event_list") if user.has_perm("events.view_event")
                else reverse("staffing:schedule") if events else None)
    except DatabaseError:
        result.update(home_events=[], home_events_state="unavailable", home_event_index_url=None)
        events = []
    result["home_registration_state"] = "available" if can_report(user) else "restricted"
    try:
        counts = directory_counts(user, events)
    except DatabaseError:
        counts = {}
        result["home_registration_state"] = "unavailable"
    for event in events:
        event.directory_registered = counts.get(event.pk, {}).get("registered")
    return result


def operational_metrics(actor):
    """One request-local tab resolution; unchanged canonical source predicates.

    Public report APIs retain their fresh guards. Home authorizes all four reads
    once, then uses their shared private builders and existing record selectors.
    No permission decision or metric value is cached across requests.
    """
    from django.core.exceptions import PermissionDenied
    from django.db import DatabaseError
    from django.db.models import Count
    from datetime import datetime, time
    from apps.reporting.access import allowed_tabs
    from apps.reporting.metrics import _event_cohort, _customer_projection, _confirmed_hires, _participant_population
    from apps.participants.identity import canonical_records
    try:
        tabs=set(allowed_tabs(actor));access_state='available'
    except PermissionDenied:
        tabs=set();access_state='restricted'
    except DatabaseError:
        tabs=set();access_state='unavailable'
    metrics=[]
    def metric(label,scope,loader,url,required,period_loader=None):
        value=None;period=''
        if not set(required).issubset(tabs):
            state='restricted' if access_state=='available' else access_state
        else:
            try:
                value=loader();state='available'
            except PermissionDenied:state='restricted'
            except DatabaseError:state='unavailable'
            if state=='available' and period_loader is not None:
                # A supplementary period read must not erase an available stock.
                try:period=period_loader()
                except (PermissionDenied,DatabaseError):period=''
        metrics.append(dict(label=label,scope=scope,value=value,state=state,url=url if state=='available' else '',period=period))
    now=timezone.now()
    month=timezone.localdate(now).replace(day=1)
    month_start=timezone.make_aware(datetime.combine(month,time.min))
    metric('Participants','Canonical directory people · preview fixtures excluded',
        lambda:_participant_population(actor,{})[0].count(),reverse('participants:staff-directory'),['participants'],
        lambda:f"+{_participant_population(actor,{})[0].filter(created_at__gte=month_start,created_at__lte=now).count()} added this month")
    metric('Open positions','Approved active position records · not requests or worker quantity',
        lambda:_customer_projection(actor,{})[2]['active_positions'],reverse('internal:reports')+'?tab=customers',['customers'])
    metric('Upcoming events','Published, future start · preview fixtures excluded',
        lambda:_event_cohort(actor,{}).filter(status='upcoming',start_at__gt=now).count(),
        reverse('events:event_list')+'?view=upcoming',['events'],
        lambda:f"{_event_cohort(actor,{}).filter(status='upcoming',start_at__gt=now,start_at__year=month.year,start_at__month=month.month).count()} scheduled this month")
    def placed():
        return canonical_records(_confirmed_hires(actor,{})).aggregate(n=Count('_canonical_person',distinct=True))['n']
    metric('Hires','Unique canonical people · current explicitly confirmed hires · all time',
        placed,reverse('internal:reports')+'?tab=hires',['hires','opportunities'],
        lambda:f"{canonical_records(_confirmed_hires(actor,{}).filter(confirmed_on__gte=month_start,confirmed_on__lte=now)).aggregate(n=Count('_canonical_person',distinct=True))['n']} hired this month")
    return metrics
