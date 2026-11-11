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
from .navigation import with_origin, with_context, previous


from .actor_names import compact_actor


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
        "icon": {"call":"phone", "voicemail":"phone", "attempt":"phone", "email":"mail", "sms":"message", "meeting":"people", "customer_meeting":"customers", "video":"people", "interview":"people", "event":"calendar", "follow_up":"tasks"}.get(row.activity_type, "notes"),
        "notes": row.notes, "outcome": row.outcome, "occurred_at": row.occurred_at,
        "created_at": row.created_at, "author": compact_actor(row.created_by), "organization": row.organization,
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
        "record_type": "Participant interaction" if row.participant_id else "Customer interaction",
        "subject_label": str(row.participant or row.organization),
        "subject_url": (reverse("participants:staff-profile", args=[row.participant_id]) + "?section=relationship") if row.participant_id else reverse("customers:workspace", args=[row.organization_id, "crm"]),
        "related": [],
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
        item["author"] = compact_actor(identity)
    can_customer = False
    try:
        from apps.customers.access import require_manager
        require_manager(actor)
        can_customer = True
    except PermissionDenied:
        pass
    if row.organization_id:
        item["related"].append({"label": str(row.organization), "url": reverse("customers:workspace", args=[row.organization_id, "crm"]) if can_customer else "", "relationship": True})
    if row.need_id:
        item["related"].append({"label": str(row.need), "url": reverse("customers:need", args=[row.organization_id, row.need_id]) if can_customer and row.organization_id else ""})
    if row.campaign_id:
        item["related"].append({"label": str(row.campaign), "url": reverse("customers:campaign", args=[row.organization_id, row.campaign_id]) if can_customer and row.organization_id else ""})
    if row.contact_id:
        item["related"].append({"label": "Contact: " + str(row.contact)})
    if row.workforce_request_id:
        item["related"].append({"label": "Request: " + row.workforce_request.name})
    if row.engagement_id:
        try:
            from apps.recruiting.selection import opportunity_for
            opportunity_for(actor, row.engagement_id)
            item["related"].append({"label": "Recruiting engagement", "url": reverse("recruiting:matching", args=[row.engagement_id])})
        except PermissionDenied:
            pass
    return item


def contextual_item(request, row):
    item = staff_item(request.user, row); back = request.get_full_path()
    item["url"] = with_origin(item["url"], back)
    item["subject_url"] = with_context(item["subject_url"], back, "crm_return_to")
    for key in ("correct_url", "followup_url", "link_task_url"):
        item[key] = with_origin(item[key], back)
    for related in item["related"]:
        if related.get("url"):
            related["url"] = with_context(related["url"], back, "crm_return_to" if related.get("relationship") else "return_to")
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
    dated = query.filter(due_date__isnull=False).order_by("due_date", "-created_at", "pk")
    return {"next": dated.first(), "dated": dated,
            "undated": query.filter(due_date__isnull=True).order_by("title", "pk")}


def view_url(request, value):
    params = request.GET.copy()
    params["crm_view"] = value
    return request.path + "?" + params.urlencode()


def history_context(request, *, participant=None, organization=None):
    result = {"crm_types": ActivityType.choices, "crm_subject_is_company": bool(organization), "crm_previous_url": previous(request)}
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
        items = [contextual_item(request, row) for row in page.object_list]
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
    result["crm_record_email_url"] = result["crm_new_url"] + "&type=email"
    result["crm_record_text_url"] = result["crm_new_url"] + "&type=sms"
    choices = [("all", "All")] if organization else []
    if result.get("crm_btw_allowed"):
        choices.append(("btw", "BTW–Customer" if organization else "BTW touchpoints"))
    if result.get("crm_employer_allowed"):
        choices.append(("employer", "Employer–Participant" if organization else "From employers"))
    if result.get("crm_btw_allowed"):
        choices.append(("followups", "Follow-ups"))
    selected = request.GET.get("crm_view", "all" if organization else "btw")
    selected = selected if selected in dict(choices) else (choices[0][0] if choices else "")
    result.update(crm_view=selected, crm_views=[{"label": label, "url": view_url(request, key), "selected": key == selected} for key, label in choices],
                  crm_show_btw=selected in {"all", "btw"}, crm_show_employer=selected in {"all", "employer"})
    if result.get("crm_btw_allowed"):
        # Reuse the single authorized history pass; summary is intentionally independent of timeline filters.
        active = [row for row in authorized["btw"] if not row.archived_at]
        now = timezone.now(); recent_since = now - timedelta(days=14)
        followups = followup_context(request.user, active, participant)
        from .communications import participant_preferences
        result["crm_summary"] = {"last": active[0] if active else None,
            "recent": sum(recent_since <= row.occurred_at <= now for row in active),
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
    summary = result.setdefault('crm_summary', {})
    summary['btw_allowed'] = bool(result.get('crm_btw_allowed'))
    summary['btw_customer_count'] = sum(not row.archived_at and bool(row.organization_id) for row in authorized.get('btw', [])) if result.get('crm_btw_allowed') else None
    summary['employer_count'] = sum(not row.archived_at for row in authorized.get('employer', [])) if result.get('crm_employer_allowed') else None
    if summary.get('last'):
        summary['last_author'] = compact_actor(summary['last'].created_by)
    params = request.GET.copy()
    for key in ('crm_type', 'crm_q', 'crm_from', 'crm_to', 'crm_archived', 'crm_btw_page', 'crm_employer_page'):
        params.pop(key, None)
    result['crm_reset_url'] = request.path + ('?' + params.urlencode() if params else '')
    return result
