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
    # Global totals require the same destination permission. Never use an admin's
    # wider task access to grant access to Events or the Participant directory.
    if user.has_perm("events.view_event"):
        from apps.events.models import Event
        metrics.append({"label": "Upcoming events", "value": Event.objects.filter(
            status=Event.Status.UPCOMING, start_at__gt=timezone.now()).count(),
            "scope": "Published events starting in the future", "url": reverse("events:event_list") + "?view=upcoming"})
    if user.has_perm("participants.view_participant"):
        from apps.participants.models import Participant
        metrics.append({"label": "Participant records", "value": Participant.objects.count(),
                        "scope": "All records in your authorized directory", "url": reverse("participants:staff-directory")})
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
                label="Accessible overdue tasks", scope="Open tasks you can access",
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
                attention.append(dict(id="intake", count=count, label="Intake review cases",
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
            attention.append(dict(id="event-end", count=end_count, label="Events past scheduled end",
                scope="Not marked ended · your existing end-action access",
                url=reverse("operations:execution-index") + "?view=attention"))
    except DatabaseError:
        unavailable.append("Scheduled-end check unavailable")
    result.update(home_attention=attention, home_attention_unavailable=unavailable)
    # Existing metrics keep their queries/meaning; only nonduplicated totals render here.
    result["home_summary_metrics"] = [m for m in result["home_metrics"]
                                      if m["label"] in ("Upcoming events", "Participant records")]

    try:
        events = list(task_access.events_for(user).filter(status=Event.Status.UPCOMING,
            end_at__gte=timezone.now()).order_by("start_at", "pk")[:3])
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
