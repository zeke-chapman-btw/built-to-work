"""Permission-aware staff projections; never imported by participant portal views."""
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.urls import reverse
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta
from urllib.parse import urlencode

from apps.operations.task_access import tasks_for

from . import access, services
from .models import ActivityType
from .navigation import with_origin


def participant_navigation(actor, participant):
    """Affordances only: every destination still runs its existing fresh guard."""
    result = {"crm_btw_allowed": False, "crm_employer_allowed": False,
              "crm_can_add": False, "crm_new_url": reverse("crm:participant-new", args=[participant.pk])}
    try:
        access.internal_context(actor, participant=participant)
        result["crm_btw_allowed"] = True
    except PermissionDenied:
        pass
    try:
        from apps.recruiting.access import fresh_staff
        fresh_staff(actor)
        result["crm_employer_allowed"] = True
    except PermissionDenied:
        pass
    try:
        access.internal_context(actor, participant=participant, write=True)
        result["crm_can_add"] = not bool(participant.archived_at)
    except PermissionDenied:
        pass
    return result


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
        "audience": "BTW staff" if row.audience == "btw" else "Employer-owned",
        "correct_url": reverse("crm:correct", args=[row.pk]),
        "followup_url": reverse("crm:followup", args=[row.pk]),
        "link_task_url": reverse("crm:link-task", args=[row.pk]),
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


def followup_context(actor, rows, participant=None):
    """Accessible existing Tasks only; a recorded date never creates a Task."""
    from apps.operations.models import Task
    from apps.participants.identity import identity_members
    condition = Q(crm_links__interaction_id__in=[row.pk for row in rows if not row.archived_at])
    if participant:
        condition |= Q(participant_links__participant_id__in=identity_members(participant.pk))
    query = tasks_for(actor).filter(condition, archived_at__isnull=True,
        status__in=[Task.Status.TODO, Task.Status.PROGRESS]).select_related("assigned_staff").distinct()
    dated = query.filter(due_date__isnull=False).order_by("due_date", "-priority", "title", "pk")
    return {"next": dated.first(), "dated": dated,
            "undated": query.filter(due_date__isnull=True).order_by("title", "pk")}


def view_url(request, value):
    params = request.GET.copy()
    params["crm_view"] = value
    return request.path + "?" + params.urlencode()


def history_context(request, *, participant=None, organization=None):
    result = {"crm_types": ActivityType.choices, "crm_subject_is_company": bool(organization)}
    authorized = {}
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
        authorized[audience] = rows
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
        for item in items:
            item["url"] = with_origin(item["url"], request.get_full_path())
            for key, route in (("correct_url", "correct"), ("followup_url", "followup"), ("link_task_url", "link-task")):
                item[key] = with_origin(reverse("crm:" + route, args=[item["id"]]), request.get_full_path())
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
    result["crm_new_url"] = with_origin(result["crm_new_url"], request.get_full_path())
    result["crm_quick_actions"] = [{"label": label, "url": result["crm_new_url"] + "&" + urlencode({"type": kind})}
        for kind, label in (("call", "Log call"), ("email", "Record email"), ("sms", "Record text"), ("meeting", "Log meeting"), ("note", "Relationship note"))]
    choices = [("all", "All")] if organization else []
    if result.get("crm_btw_allowed"):
        choices.append(("btw", "BTW–Customer" if organization else "BTW Interactions"))
    if result.get("crm_employer_allowed"):
        choices.append(("employer", "Employer–Participant" if organization else "Employer Interactions"))
    if result.get("crm_btw_allowed"):
        choices.append(("followups", "Follow-ups"))
    selected = request.GET.get("crm_view", "all" if organization else "btw")
    selected = selected if selected in dict(choices) else (choices[0][0] if choices else "")
    result.update(crm_view=selected, crm_views=[{"label": label, "url": view_url(request, key), "selected": key == selected} for key, label in choices],
                  crm_show_btw=selected in {"all", "btw"}, crm_show_employer=selected in {"all", "employer"})
    if result.get("crm_btw_allowed"):
        # Reuse the single authorized history pass; summary is intentionally independent of timeline filters.
        active = [row for row in authorized["btw"] if not row.archived_at]
        recent_since = timezone.now() - timedelta(days=14)
        followups = followup_context(request.user, active, participant)
        from .communications import participant_preferences
        result["crm_summary"] = {"last": active[0] if active else None,
            "recent": sum(recent_since <= row.occurred_at <= timezone.now() for row in active),
            "days": 14, "next": followups["next"],
            "contact": participant_preferences(participant)[1] if participant else "Business contact preferences below; Portal users are separate."}
        for key in ("dated", "undated"):
            param = "crm_tasks_page" if key == "dated" else "crm_undated_page"
            result["crm_" + key + "_tasks"] = Paginator(followups[key], 10).get_page(request.GET.get(param))
            params = request.GET.copy(); params.pop(param, None)
            result["crm_" + key + "_query"] = params.urlencode()
    if result["crm_can_add"]:
        from .communications import relationship_communications
        result["crm_communications"] = relationship_communications(participant=participant, organization=organization)
    return result
