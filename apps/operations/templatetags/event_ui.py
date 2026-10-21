from django import template
from django.urls import reverse
from apps.event_reports.services import can_report
from apps.internal_ui.event_presentation import event_tabs, event_tools
register = template.Library()

@register.inclusion_tag("events/event_header.html", takes_context=True)
def event_header(context, event):
    from apps.events.event_management import available_actions
    from apps.operations.templatetags.task_ui import global_task_create_url
    request=context["request"]; user=request.user
    return dict(request=request,event=event,report_url=reverse("event_reports:detail",args=[event.pk]) if can_report(user) else None,
        edit_url=reverse("events:event_edit",args=[event.pk]) if user.has_perm("events.change_event") and event.status!="archived" else None,
        management_actions=available_actions(event) if user.has_perm("events.change_event") else [],
        back_url=reverse("events:event_list") if user.has_perm("events.view_event") else reverse("staffing:schedule"),
        task_creation_enabled=context.get("task_creation_enabled",True),task_action_url=global_task_create_url(context) if context.get("task_action_in_event_header") else "")

@register.inclusion_tag("events/event_tabs.html", takes_context=True)
def event_navigation(context, event, active=""):
    request=context["request"]; match=request.resolver_match
    if not active:
        namespace=match.namespace if match else ""
        if namespace=="staffing": active="staff"
        elif namespace=="workdays": active="workdays"
        elif namespace=="event_reports": active="expenses"
        else: active=context.get("section","overview")
    return dict(event_tabs=event_tabs(request.user,event),active_event_tab=active)

@register.inclusion_tag("events/operational_tools.html", takes_context=True)
def event_operational_tools(context,event):
    return dict(tools=event_tools(context["request"].user,event))
