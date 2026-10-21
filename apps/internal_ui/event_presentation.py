"""Read-only presentation policies; domain views remain authoritative."""
from django.urls import reverse
from django.utils import timezone
from apps.events.experience_operations import can_view_event_operations
from apps.operations.services import assigned_event, manager
from apps.operations.task_access import events_for, staff_user
from apps.workdays.services import manager as workday_manager
from apps.event_reports.services import can_report


def event_tools(user, event):
    if not staff_user(user) or not user.has_perm("events.view_event"):
        return []
    tools = []
    def add(label, route, args=(), description="", suffix=""):
        tools.append(dict(label=label, url=reverse(route, args=args)+suffix, description=description))
    if user.is_superuser or user.has_perm("assessments.change_eventassessmentcategory"):
        add("Quizzes & Tests", "assessments:event_configuration", [event.pk], "Choose assessment categories and question sets for this event.")
    if user.has_perm("events.change_event"):
        if can_view_event_operations(user):
            add("Station assignments", "events:event_detail", [event.pk], "Manage station assignments using the existing event form.", "?section=results#event-stations")
        add("Public registration settings", "events:public-settings", [event.pk], "Manage registration availability and the event’s public link.")
    if user.has_perm("games.view_eventgameconfiguration") or user.has_perm("games.change_eventgameconfiguration"):
        add("Game configuration", "admin:games_eventgameconfiguration_changelist", description="Review this event’s selected game and settings.", suffix="?event__id__exact="+str(event.pk))
    if can_view_event_operations(user):
        add("Simulator results & configuration", "events:event_detail", [event.pk], "View recorded simulator scores and configuration checks.", "?section=results")
    if user.has_perm("events.view_offlineeventpreparation"):
        for prep in event.offline_preparations.select_related("trailer").order_by("created_at", "pk")[:10]:
            add("Offline preparation · "+prep.trailer.display_name, "events:offline-preparation-detail", [prep.pk], "Open registration preparation for this event and trailer.")
    return tools


def event_tabs(user, event):
    if not staff_user(user):
        return []
    tabs = []
    def add(key, label, route, args=(), suffix=""):
        tabs.append(dict(id=key, label=label, url=reverse(route, args=args)+suffix))
    view_event = user.has_perm("events.view_event")
    assigned = assigned_event(user, event)
    if view_event:
        add("overview", "Info", "events:event_detail", [event.pk])
    if view_event and user.has_perm("staffing.manage_event_staffing"):
        add("staff", "Staff", "staffing:event", [event.pk])
    from apps.operations.template_services import can_view_setup
    if event_tools(user, event) or can_view_setup(user):
        add("operations", "Operations", "events:event_detail", [event.pk], "?section=operations")
    if view_event and can_view_event_operations(user):
        add("results", "Results", "events:event_detail", [event.pk], "?section=results")
    if view_event and user.has_perms(("events.view_eventregistration", "participants.view_participant")):
        add("registrations", "Registrations", "events:event_detail", [event.pk], "?section=registrations")
    if workday_manager(user):
        add("workdays", "Workdays & Mileage", "workdays:event", [event.pk])
    if can_report(user):
        add("expenses", "Expenses & Report", "event_reports:detail", [event.pk], "#expenses")
    for section, kind in (("tasks", "tasks"), ("notes", "event_notes"), ("documents", "event_documents")):
        if manager(user, kind) or assigned:
            add(section, section.title(), "operations:event", [event.pk, section])
    return tabs


def operations_home(user):
    """Chronological future events, after the existing event access policy."""
    now = timezone.now()
    visible = events_for(user)
    upcoming = list(visible.filter(status="upcoming", start_at__gt=now).order_by("start_at", "pk")[:5])
    def present(event):
        route = "events:event_detail" if user.has_perm("events.view_event") else "staffing:staff-event"
        return dict(event=event, url=reverse(route, args=[event.pk]))
    featured = present(upcoming[0]) if upcoming else None
    return dict(featured=featured, other_events=[present(e) for e in upcoming[1:]],
        event_index_url=reverse("events:event_list" if user.has_perm("events.view_event") else "staffing:schedule"))
