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


def home_data(user):
    """Present existing task and event scopes; failed reads are never zero."""
    from django.db import DatabaseError
    from apps.events.models import Event
    try:
        result = _work_data(user)
        result.update(home_work_state="available", home_attention_count=result["home_metrics"][1]["value"])
    except DatabaseError:
        result = dict(home_tasks=[], home_task_count=None, home_metrics=[],
                      home_work_state="unavailable", home_attention_count=None)
    try:
        result["home_events"] = list(task_access.events_for(user).filter(
            status=Event.Status.UPCOMING, end_at__gte=timezone.now()).order_by("start_at", "pk")[:3])
        result["home_events_state"] = "available"
    except DatabaseError:
        result.update(home_events=[], home_events_state="unavailable")
    return result
