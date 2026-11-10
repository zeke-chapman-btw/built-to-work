"""Read-only data for the one-page participant profile overview.

Reuses the existing authorized services; nothing here writes, widens access, or
recalculates domain values. Each block is only populated when the viewer
already has access to the full destination page it summarizes.
"""
from django.core.exceptions import PermissionDenied

TOUCHPOINT_PREVIEW = 4
CONNECTION_PREVIEW = 3


def overview_context(request, person, ctx):
    from apps.reporting.definitions import REPORT_TIMEZONE
    totals = {group["key"]: group["total"] for group in ctx.get("profile_qualification_groups") or []}
    result = {
        "profile_timezone": REPORT_TIMEZONE,
        "profile_skill_total": totals.get("skills", 0),
        "profile_education_total": totals.get("education", 0),
        "profile_connection_more": 0,
        "profile_touchpoints": [],
        "profile_touchpoint_total": 0,
        "profile_last_contact": None,
        "profile_last_contact_author": "",
        "profile_next_followup": None,
        "profile_show_touchpoints": False,
        "profile_connections": [],
        "profile_connection_active": 0,
        "profile_connection_closed": 0,
        "profile_show_connections": False,
    }
    if ctx.get("crm_btw_allowed") or ctx.get("crm_employer_allowed"):
        from apps.crm.presentation import history_context
        history = history_context(request, participant=person)
        rows, total = [], 0
        for audience in ("btw", "employer"):
            if history.get("crm_" + audience + "_allowed"):
                rows.extend(history.get("crm_" + audience + "_rows") or [])
                page = history.get("crm_" + audience + "_page")
                total += page.paginator.count if page is not None else 0
        rows.sort(key=lambda item: item["occurred_at"], reverse=True)
        result["profile_touchpoints"] = rows[:TOUCHPOINT_PREVIEW]
        result["profile_touchpoint_total"] = total
        result["profile_show_touchpoints"] = True
        summary = history.get("crm_summary") or {}
        if summary.get("btw_allowed"):
            result["profile_last_contact"] = summary.get("last")
            result["profile_last_contact_author"] = summary.get("last_author", "")
            result["profile_next_followup"] = summary.get("next")
    if ctx.get("can_recruiting"):
        try:
            from apps.recruiting.read_models import participant_context
            recruiting = participant_context(request.user, person)
        except PermissionDenied:
            recruiting = None
        if recruiting is not None:
            records = recruiting["profile_recruiting_records"].paginator.object_list
            active = records.filter(active=True)
            result["profile_connections"] = list(active[:CONNECTION_PREVIEW])
            result["profile_connection_active"] = active.count()
            result["profile_connection_closed"] = records.count() - result["profile_connection_active"]
            result["profile_connection_more"] = max(result["profile_connection_active"] - CONNECTION_PREVIEW, 0)
            result["profile_show_connections"] = True
    return result
