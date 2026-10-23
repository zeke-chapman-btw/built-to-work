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
        elif namespace=="workdays": active="staff"
        elif namespace=="event_reports":
            kind = match.kwargs.get("kind") if match else ""
            active = "expenses" if context.get("report_section") == "expenses" or (match and match.url_name in ("expense-add", "expense-edit", "incentive-add", "incentive-edit")) or kind in ("expense", "incentive") else "report"
        else: active=context.get("section","overview")
    tabs = event_tabs(request.user,event)
    more = [tab for tab in tabs if tab["id"] in ("tasks", "notes", "documents")]
    current = next((tab for tab in more if tab["id"] == active), None)
    active_label = next((tab["label"] for tab in tabs if tab["id"] == active), "Event sections")
    return dict(all_event_tabs=tabs,active_event_label=active_label,primary_event_tabs=[tab for tab in tabs if tab not in more], more_event_tabs=more, active_event_tab=active, more_active=bool(current), active_more_label=current["label"] if current else "")

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
        ("contact-logistics", "Contact and logistics", ("contact_name", "contact_email", "contact_phone", "housing_information", "logistics_instructions"), True),
        ("registration", "Participant registration settings", ("registration_opens_at", "registration_closes_at", "registration_deadline_at", "registration_capacity", "group_mode", "group_label"), True),
    ):
        fields = [form[name] for name in names if not form[name].is_hidden]
        if not fields:
            continue
        result.append(dict(id="event-" + key, title=title, fields=fields,
            optional=optional and not any(field.field.required for field in fields),
            open=any(field.errors for field in fields) or any(field.value() not in (None, "", False, 0, "disabled", "Group") for field in fields)))
    return result
