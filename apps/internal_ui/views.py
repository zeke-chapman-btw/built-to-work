from .dashboard import ANALYTICS_LABELS, quick_actions_for
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from .navigation import WORKSPACES, visible_settings_link, visible_workspace_links, visible_workspaces


def _staff_context(request, **extra):
    return {
        "navigation": visible_workspaces(request.user, request.path),
        "settings_link": visible_settings_link(request.user, request.path),
        **extra,
    }


def _require_staff(request):
    if not request.user.is_active or not request.user.is_staff:
        raise PermissionDenied


@login_required
def home(request):
    _require_staff(request)
    return render(request, "internal/dashboard.html", _staff_context(request, page_title="Dashboard", quick_actions=quick_actions_for(request.user), analytics_labels=ANALYTICS_LABELS))


@login_required
def workspace(request,workspace_id):
    _require_staff(request)
    item=next((entry for entry in WORKSPACES if entry["id"]==workspace_id and entry["id"]!="home"),None)
    if item is None:
        raise Http404
    if not any(entry["id"]==workspace_id for entry in visible_workspaces(request.user)):
        raise PermissionDenied
    links=visible_workspace_links(request.user,workspace_id)
    return render(request,"internal/workspace.html",_staff_context(request,page_title=item["label"].title(),page_description=item["description"],workspace_id=workspace_id,workspace_links=links))


@login_required
def settings(request):
    _require_staff(request)
    return render(request,"internal/settings.html",_staff_context(request,page_title="Settings",page_description="Manage your own staff account settings.",password_change_url=reverse("admin:password_change")))
