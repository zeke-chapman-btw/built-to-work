from .home_data import home_data
from .dashboard import MAX_QUICK_ACTIONS, available_quick_actions, quick_actions_for
from .forms import QuickActionsForm
from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from .navigation import WORKSPACES, staff_destinations, legacy_workspace_context, settings_groups, visible_legacy_workspaces, visible_settings_link, visible_workspace_links, visible_workspaces


def _staff_context(request, **extra):
    return {
        "navigation": visible_workspaces(request.user, request.path),
        "primary_navigation": staff_destinations(request.user, request.path),
        "legacy_workspace_context": legacy_workspace_context(request.user, request.path),
        "settings_link": visible_settings_link(request.user, request.path),
        **extra,
    }


def _require_staff(request):
    if not request.user.is_active or not request.user.is_staff:
        raise PermissionDenied


@login_required
def home(request):
    _require_staff(request)
    return render(request, "internal/dashboard.html", _staff_context(request, page_title="Dashboard", quick_actions=quick_actions_for(request.user), **home_data(request.user), quick_actions_edit_url=reverse("internal:quick-actions-edit")))


@login_required
@require_http_methods(["GET"])
def reports(request):
    _require_staff(request)
    from .reporting import report_destinations
    return render(request, "internal/reports.html", _staff_context(request,
        page_title="Reports & Analytics", page_description="Open the reporting areas available to your account.",
        report_destinations=report_destinations(request.user)))


@login_required
def workspace(request,workspace_id):
    _require_staff(request)
    item=next((entry for entry in WORKSPACES if entry["id"]==workspace_id and entry["id"]!="home"),None)
    if item is None:
        raise Http404
    if not any(entry["id"]==workspace_id for entry in visible_legacy_workspaces(request.user)):
        raise PermissionDenied
    links=visible_workspace_links(request.user,workspace_id)
    if workspace_id=="participants" and request.user.has_perm("participants.view_participant"):
        from apps.participants.workspace_views import directory
        return directory(request)
    if workspace_id=="platform-tools":
        return render(request,"internal/platform_tools.html",_staff_context(request,
            page_title="Platform Tools",workspace_links=links))
    if workspace_id=="operations":
        if request.GET.get("resource") == "games":
            game_link = next((link for link in links if link["label"] == "Games / Duck Hunt"), None)
            if game_link is None:
                raise PermissionDenied
            if request.method != "GET":
                from django.http import HttpResponseNotAllowed
                return HttpResponseNotAllowed(["GET"])
            from apps.games.models import GameDefinition
            from apps.operations.presentation import list_page
            games, filters = list_page(request, GameDefinition.objects.all(),
                search_fields=("display_name", "key", "implementation_key"), name_field="display_name",
                newest_field="-updated_at", active_field="active", status_choices=(("active", "Active"), ("inactive", "Inactive")))
            return render(request, "internal/operations_games.html", _staff_context(request,
                page_title="Operations", page_description="Manage event resources, quizzes, games, forms and consents.",
                games=games, game_admin_url=game_link["url"], resource_filter="games",
                filter_clear_url=reverse("internal:workspace", args=["operations"]) + "?resource=games", **filters))
        from .event_presentation import operations_home
        from .reporting import report_destinations
        data=operations_home(request.user)
        from apps.events.experience_operations import can_view_event_operations
        if data["featured"] and can_view_event_operations(request.user):
            links=links+[dict(label="Simulator Scoring",url=reverse("events:event_detail",args=[data["featured"]["event"].pk])+"?section=results",description="View recorded simulator results for the next upcoming event.")]
        return render(request,"internal/operations_home.html",_staff_context(request,page_title="Operations",page_description="Manage event resources, quizzes, games, forms and consents.",workspace_links=links,
            reporting_available=bool(report_destinations(request.user)),**data))
    return render(request,"internal/workspace.html",_staff_context(request,page_title=item["label"].title(),page_description=item["description"],workspace_id=workspace_id,workspace_links=links))


@login_required
def settings(request):
    _require_staff(request)
    return render(request,"internal/settings.html",_staff_context(request,page_title="Settings & Administration",page_description="Your account and authorized administration tools.",settings_groups=settings_groups(request.user),password_change_url=reverse("admin:password_change")))

@login_required
@require_http_methods(["GET", "POST"])
def quick_actions_edit(request):
    _require_staff(request)
    available = available_quick_actions(request.user)
    if request.method == "POST" and request.POST.get("operation") == "restore":
        request.user.quick_action_ids = None
        request.user.save(update_fields=["quick_action_ids"])
        messages.success(request, "Default Quick Actions restored.")
        return redirect("internal:home")
    form = QuickActionsForm(request.POST if request.method == "POST" else None, available=available)
    if request.method == "POST" and form.is_valid():
        request.user.quick_action_ids = form.cleaned_data["actions"]
        request.user.save(update_fields=["quick_action_ids"])
        messages.success(request, "Quick Actions saved.")
        return redirect("internal:home")
    selected = request.POST.getlist("actions") if form.is_bound else [action["id"] for action in quick_actions_for(request.user)]
    slots = (selected + [""] * MAX_QUICK_ACTIONS)[:MAX_QUICK_ACTIONS]
    return render(request, "internal/quick_actions_edit.html", _staff_context(request, page_title="Edit Quick Actions", available_actions=available, form=form, slots=slots))
