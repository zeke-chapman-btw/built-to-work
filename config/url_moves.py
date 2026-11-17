"""Tidy page addresses that match the menu, without touching each app's own URL list.

Each app keeps its URL names (so ``reverse()`` and templates are unchanged); only the
address a page is served at moves. Every old address forwards to the new one, so
bookmarks and links in old emails keep working.

Kiosk, trailer-device, sign-up, portal, webhook and Google sign-in addresses never move:
they are printed, registered with other services, or saved on devices.
"""
import importlib
import re

from django.http import HttpResponsePermanentRedirect, HttpResponseRedirect
from django.urls import path

# (old prefix, new prefix, old prefixes that stay where they are, exact-match only)
MOVES = (
    ("internal/", "home/", (), True),
    ("internal/quick-actions/", "home/quick-actions/", (), False),
    ("internal/settings/", "settings/", (), False),
    ("internal/reports/", "reports/", (), False),
    ("internal/customers/", "customers/", (), False),
    ("internal/trailer-control/", "operations/trailer-control/", (), False),
    ("participants/staff/consents/", "operations/consents/", (), False),
    ("participants/staff/intake-forms/", "operations/forms/", (), False),
    ("participants/staff/testing/", "settings/test-participant/", (), False),
    ("participants/staff/", "participants/", ("participants/staff/intake-trailer/",), False),
    ("events/offline/", "operations/offline/", ("events/offline/ticket",), False),
    ("events/operations/", "settings/system/", (), False),
    ("stations/", "operations/stations/", ("stations/operate/", "stations/service/"), False),
    ("quizzes/", "operations/quizzes/", ("quizzes/station/", "quizzes/staff/"), False),
    ("staff/directory/", "settings/staff-directory/", (), False),
    ("accounts/staff/", "settings/staff/", (), False),
    ("reports/categories/", "settings/expense-categories/", (), False),
    ("reports/", "events/reports/", (), False),
    ("workdays/compensation/", "settings/pay/", (), False),
    ("messaging/connections/", "settings/connections/", (), False),
    ("messaging/texts/", "inbox/", (), False),
    ("messaging/signature/", "settings/signature/", (), False),
)
# The menu's section pages ("Participants", "Operations", …) get their own short address.
WORKSPACES = {"participants": "participants/", "operations": "operations/", "approvals": "approvals/",
              "administration": "settings/admin/", "platform-tools": "settings/platform/"}
WORKSPACE_ROUTE = "internal/workspaces/<slug:workspace_id>/"


def moved(route):
    """The new address for an old route string (unchanged when it doesn't move)."""
    for old, new, keep, exact in MOVES:
        if exact:
            if route == old:
                return new
            continue
        if route.startswith(old) and not route.startswith(tuple(keep)):
            return new + route[len(old):]
    return route


def _fill(route, kwargs):
    return "/" + re.sub(r"<(?:\w+:)?(\w+)>", lambda m: str(kwargs[m.group(1)]), route)


def _forward(new_route):
    def view(request, **kwargs):
        target = _fill(new_route, kwargs)
        if request.META.get("QUERY_STRING"):
            target += "?" + request.META["QUERY_STRING"]
        if request.method in ("GET", "HEAD"):
            return HttpResponsePermanentRedirect(target)
        response = HttpResponseRedirect(target)
        response.status_code = 308  # keeps the form post
        return response
    view.url_forward = new_route
    return view


def relocate(module, prefix):
    """``(patterns, app_name)`` for an app's URLs, served at their tidy addresses."""
    urls = importlib.import_module(module)
    patterns, forwards = [], []
    for item in urls.urlpatterns:
        old = prefix + item.pattern._route
        if old == WORKSPACE_ROUTE:
            # The generic page stays for any other section; the short addresses come after it so
            # reverse() prefers them.
            patterns.append(path(old, item.callback, item.default_args, name=item.name))
            for workspace, address in WORKSPACES.items():
                patterns.append(path(address, item.callback, dict(item.default_args, workspace_id=workspace),
                                     name=item.name))
            continue
        new = moved(old)
        patterns.append(path(new, item.callback, item.default_args, name=item.name))
        if new != old:
            forwards.append(path(old, _forward(new)))
    return patterns, forwards, getattr(urls, "app_name", None)
