from urllib.parse import quote
from django import template
from django.urls import reverse
from apps.event_reports.services import can_report
from apps.internal_ui.event_presentation import event_tabs, event_tools, staff_tabs
register = template.Library()

@register.inclusion_tag("events/event_header.html", takes_context=True)
def event_header(context, event):
    from apps.events.event_management import available_actions
    from apps.operations.templatetags.task_ui import global_task_create_url
    request=context["request"]; user=request.user
    return dict(request=request,event=event,report_url=reverse("event_reports:detail",args=[event.pk]) if can_report(user) else None,
        edit_url=reverse("events:event_edit",args=[event.pk])+"?return_to="+quote(request.get_full_path(),safe="") if user.has_perm("events.change_event") and event.status!="archived" else None,
        management_actions=available_actions(event) if user.has_perm("events.change_event") else [],
        back_label="Events" if user.has_perm("events.view_event") else "My staff schedule",
        back_url=reverse("events:event_list") if user.has_perm("events.view_event") else reverse("staffing:schedule"),
        task_creation_enabled=context.get("task_creation_enabled",True),task_action_url=global_task_create_url(context) if context.get("task_action_in_event_header") else "")

@register.inclusion_tag("events/event_tabs.html", takes_context=True)
def event_navigation(context, event, active=""):
    request=context["request"]; match=request.resolver_match
    if not active:
        namespace=match.namespace if match else ""
        if namespace=="staffing": active="staff"
        elif namespace=="workdays": active="clock"
        elif namespace=="event_reports":
            kind = match.kwargs.get("kind") if match else ""
            active = "expenses" if context.get("report_section") == "expenses" or (match and match.url_name in ("expense-add", "expense-edit", "incentive-add", "incentive-edit")) or kind in ("expense", "incentive") else "report"
        else: active=context.get("section","overview")
    tabs = event_tabs(request.user,event)
    by_id = {tab["id"]: tab for tab in tabs}
    primary = []
    for key, label in (("overview", "Overview"), ("staff", "Staff"), ("clock", "Clock"),
                       ("registrations", "Registrations & Tickets"), ("expenses", "Expenses")):
        if key in by_id:
            primary.append(dict(by_id[key], label=label, icon="event-dollar" if key == "expenses" else key))
    result_tab = by_id.get("report") or by_id.get("results")
    if result_tab:
        primary.append(dict(result_tab, id="report-results", label="Reports & Results", icon="results"))
    more = [tab for tab in tabs if tab["id"] not in ("overview", "staff", "clock", "registrations", "expenses", "report", "results")]
    # Existing tool policy supplies every specialist link; do not invent routes or grants.
    tools = event_tools(request.user, event)
    seen = {tab["url"] for tab in more}
    for tool in tools:
        if tool["url"] not in seen:
            more.append(dict(id="tool", label=tool["label"], url=tool["url"], icon="operations"))
            seen.add(tool["url"])
    from apps.operations.health_presentation import health_context
    health = health_context(request.user, event)
    for key, label in (("health_execution_url", "Execution checklist"), ("health_monitor_url", "Health Monitor"),
                       ("health_closeout_url", "Closeout"), ("health_manager_url", "Primary Event Manager")):
        url = health.get(key)
        if url and url not in seen:
            more.append(dict(id=key, label=label, url=url, icon="operations"))
            seen.add(url)
    # Link existing safe read-only hubs; settings/changes remain on their guarded endpoints.
    from apps.event_reports.services import can_report
    if request.user.has_perms(("events.view_qrticket", "events.view_eventregistration")):
        more.append(dict(id="ticket-recovery", label="Ticket recovery", url=reverse("events:ticket_recovery"), icon="registrations"))
    if can_report(request.user):
        url = reverse("workdays:event", args=[event.pk])
        if url not in seen:
            more.append(dict(id="workdays", label="Workdays & mileage", url=url, icon="clock"))
    old_active = active
    if active in ("report", "results"):
        active = "report-results"
    primary_active = any(tab["id"] == active for tab in primary)
    current = None if primary_active else next((tab for tab in more if tab["id"] == old_active), None)
    exact_specialist = next((tab for tab in more if tab["id"].startswith("health_") and request.path == tab["url"].split("?", 1)[0]), None)
    if not primary_active and exact_specialist:
        current = exact_specialist
        old_active = exact_specialist["id"]
    # Execution/health/closeout share an Operations context without hiding their identity.
    for tab in more:
        if not primary_active and current is None and tab["id"] != "tool" and (request.get_full_path() == tab["url"].split("#", 1)[0] or (tab["id"].startswith("health_") and request.path == tab["url"].split("?", 1)[0])):
            current = tab
            if old_active not in ("tasks", "notes", "documents"):
                old_active = tab["id"]
    active_label = next((tab["label"] for tab in primary if tab["id"] == active), current["label"] if current else "Event sections")
    result_links = []
    if "results" in by_id:
        result_links.append(dict(by_id["results"], label="Experience results"))
    if "report" in by_id:
        result_links.append(dict(by_id["report"], label="Event report"))
    return dict(all_event_tabs=primary + more, primary_event_tabs=primary, more_event_tabs=more,
        active_event_label=active_label, active_event_tab=active, active_more_tab=old_active,
        more_active=bool(current), active_more_label=current["label"] if current else "",
        event_nav_count=len(primary) + bool(more), result_links=result_links,
        result_section=by_id.get(old_active, {}).get("id", ""))


@register.inclusion_tag("events/operational_tools.html", takes_context=True)
def event_operational_tools(context,event):
    from apps.operations.setup_presentation import setup_context
    from apps.operations.health_presentation import health_context
    return dict(event=event,tools=event_tools(context["request"].user,event), **setup_context(context["request"].user,event), **health_context(context["request"].user,event))


@register.inclusion_tag("events/staff_navigation.html", takes_context=True)
def staff_navigation(context, event, active="workdays"):
    return dict(staff_tabs=staff_tabs(context["request"].user, event), selected_staff_tab=active)


@register.filter
def event_timezone(event, instant=None):
    from apps.events.timezone_choices import event_timezone_label
    return event_timezone_label(event, instant)


@register.simple_tag
def event_editor_sections(form):
    """Progressive disclosure uses the original bound fields and error state."""
    result = []
    for key, title, names, optional in (
        ("basics", "Basic information", ("name", "event_type", "event_type_other", "description"), False),
        ("schedule", "Event dates", ("start_at", "end_at", "timezone_name"), False),
        ("location", "Location", ("location_name", "address_line_1", "address_line_2", "city", "state", "postal_code", "country"), False),
        ("travel-dates", "Setup and travel dates", ("setup_date", "staff_arrival_time", "return_date"), True),
        ("staff-defaults", "Staff assignment defaults", ("daily_start_time", "daily_end_time"), True),
        ("contact-logistics", "Event Contact", ("contact_name", "contact_email", "contact_phone"), True),
        ("housing", "Event Housing", ("housing_information",), True),
        ("logistics", "Travel & Logistics", ("logistics_instructions",), True),
        ("registration", "Participant registration settings", ("registration_opens_at", "registration_closes_at", "registration_deadline_at", "registration_capacity", "group_mode", "group_label"), True),
    ):
        fields = [form[name] for name in names if not form[name].is_hidden]
        if not fields:
            continue
        result.append(dict(id="event-" + key, title=title, fields=fields,
            optional=optional and not any(field.field.required for field in fields),
            open=any(field.errors for field in fields) or any(field.value() not in (None, "", False, 0, "disabled", "Group") for field in fields)))
    return result


@register.simple_tag(takes_context=True)
def event_staffing_summary(context, event=None):
    """Read existing staffing settings only for the existing manager boundary.

    Requirements and coverage remain in Staff. Never initialize a plan on GET,
    infer publication/eligibility, or compute a second set of coverage totals.
    """
    from django.core.exceptions import PermissionDenied, ValidationError
    from urllib.parse import urlencode
    from apps.staffing import services as staffing
    from apps.staffing.models import StaffingPlan
    request = context["request"]
    try:
        staffing.require_manager(request.user)
    except PermissionDenied:
        return None
    if not event or not getattr(event, "pk", None):
        return {"saved": False}
    plan = StaffingPlan.objects.filter(event=event).first()
    edit_url = None
    try:
        staffing.mutable(event)
    except ValidationError:
        pass
    else:
        edit_url = reverse("staffing:plan-edit", args=[event.pk]) + "?" + urlencode({"return_to": context.get("back", request.get_full_path())})
    return {"saved": True, "configured": plan is not None,
            "deadline": plan.signup_deadline if plan else None,
            "edit_url": edit_url, "coverage_url": reverse("staffing:event", args=[event.pk])}


@register.filter
def event_status_label(event):
    """A temporal display label; never changes the stored lifecycle."""
    from django.utils import timezone
    if event.status == "upcoming":
        now = timezone.now()
        if event.start_at <= now <= event.end_at:
            return "Current"
        if event.end_at < now:
            return "Scheduled window passed"
        return "Upcoming"
    return "Ended" if event.status == "completed" else event.get_status_display()
