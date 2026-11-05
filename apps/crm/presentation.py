"""Permission-aware staff projections; never imported by participant portal views."""
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.urls import reverse

from apps.operations.task_access import tasks_for

from . import access, services
from .models import ActivityType


def home_url(row):
    if row.participant_id:
        return reverse("participants:staff-profile", args=[row.participant_id]) + "#btw-interactions"
    return reverse("customers:workspace", args=[row.organization_id, "crm"])


def staff_item(actor, row):
    item = {
        "id": row.pk, "type": row.get_activity_type_display(), "summary": row.summary,
        "notes": row.notes, "outcome": row.outcome, "occurred_at": row.occurred_at,
        "created_at": row.created_at, "author": str(row.created_by), "organization": row.organization,
        "engagement": str(row.need) if row.need_id else "", "contact": row.contact,
        "participant": row.participant, "direction": row.get_direction_display(),
        "next_step": row.next_step, "follow_up_on": row.follow_up_on,
        "responsible_staff": row.responsible_staff, "revision": row.revision,
        "archived": bool(row.archived_at), "url": reverse("crm:detail", args=[row.pk]),
        "source": "Manually recorded" if row.source == "manual" else "Provider-confirmed",
        "can_edit": False, "tasks": [],
    }
    if row.audience == "btw":
        try:
            access.internal_record(actor, row, write=True)
            item["can_edit"] = True
        except PermissionDenied:
            pass
        item["tasks"] = list(row.task_links.filter(task__in=tasks_for(actor)).select_related("task"))
    else:
        identity = row.membership.identity
        item["author"] = (identity.first_name + " " + identity.last_name).strip() or "Employer user"
    return item


def history_context(request, *, participant=None, organization=None):
    result = {"crm_types": ActivityType.choices}
    for audience in ("btw", "employer"):
        try:
            rows = services.staff_history(request.user, audience=audience,
                participant_id=participant.pk if participant else None,
                organization_id=organization.pk if organization else None,
                include_archived=request.GET.get("crm_archived") == "1")
        except PermissionDenied:
            result["crm_" + audience + "_allowed"] = False
            continue
        result["crm_" + audience + "_allowed"] = True
        # Filters operate only over records already authorized by the service.
        kind = request.GET.get("crm_type", "")
        term = request.GET.get("crm_q", "").strip()[:120].casefold()
        start, end = request.GET.get("crm_from", ""), request.GET.get("crm_to", "")
        from django.utils.dateparse import parse_date
        try:
            since, until = parse_date(start) if start else None, parse_date(end) if end else None
        except ValueError:
            since = until = None
        rows = [row for row in rows if (not kind or row.activity_type == kind)
                and (not term or term in " ".join([row.summary, row.outcome,
                    str(row.organization or ""), str(row.need or "")]).casefold())
                and (not since or row.occurred_at.date() >= since)
                and (not until or row.occurred_at.date() <= until)]
        page = Paginator(rows, 10).get_page(request.GET.get("crm_" + audience + "_page"))
        items = [staff_item(request.user, row) for row in page.object_list]
        params = request.GET.copy()
        params.pop("crm_" + audience + "_page", None)
        result["crm_" + audience + "_rows"] = items
        result["crm_" + audience + "_page"] = page
        result["crm_" + audience + "_query"] = params.urlencode()
    try:
        access.internal_context(request.user, participant=participant, organization=organization, write=True)
        result["crm_can_add"] = not bool(participant and participant.archived_at or
            organization and organization.status == "archived")
    except PermissionDenied:
        result["crm_can_add"] = False
    result["crm_new_url"] = reverse("crm:participant-new", args=[participant.pk]) if participant else reverse("crm:customer-new", args=[organization.pk])
    if result["crm_can_add"]:
        from .communications import relationship_communications
        result["crm_communications"] = relationship_communications(participant=participant, organization=organization)
    return result
