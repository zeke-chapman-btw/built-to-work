"""Admins can view the staff app as a specific staff member, read-only.

The admin stays signed in. For each request, the page is built with the chosen
staff member's account so menus, permissions and data match what they see.
Nothing can be saved while viewing: only GET/HEAD requests are let through.
"""
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
from django.urls import reverse

SESSION_KEY = "btw_staff_preview_user"
BLOCKED_KEY = "btw_staff_preview_blocked"


def eligible_targets():
    return (get_user_model().objects.filter(is_active=True, is_staff=True, is_superuser=False)
            .order_by("first_name", "last_name", "username"))


def can_preview(user):
    return bool(getattr(user, "is_authenticated", False) and user.is_active and user.is_staff and user.is_superuser)


class StaffPreviewMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.staff_preview = None
        session = getattr(request, "session", None)
        target_id = session.get(SESSION_KEY) if session is not None else None
        if not target_id:
            return self.get_response(request)
        real = request.user
        target = eligible_targets().filter(pk=target_id).first() if can_preview(real) else None
        if target is None:
            session.pop(SESSION_KEY, None)
            return self.get_response(request)
        exit_path = reverse("internal:staff-preview-exit")
        if request.path == exit_path:
            return self.get_response(request)
        if request.method not in ("GET", "HEAD") or request.path.startswith("/admin/") or "logout" in request.path:
            session[BLOCKED_KEY] = True
            back = request.META.get("HTTP_REFERER", "")
            parts = urlsplit(back)
            safe = parts.path if (not parts.netloc or parts.netloc == request.get_host()) and parts.path.startswith("/") and not parts.path.startswith("/admin/") else "/internal/"
            return HttpResponseRedirect(safe + ("?" + parts.query if parts.query and safe == parts.path else ""))
        from apps.accounts.roles import roles_for, ROLE_GROUPS
        names = {v: k for k, v in ROLE_GROUPS.items()}
        label = {"manager": "Manager", "recruiter": "Recruiter", "event_staff": "Event staff"}
        roles = [label[r] for r in sorted(roles_for(target)) if r in label]
        request.staff_preview = {"name": target.get_full_name() or target.get_username(), "roles": roles,
                                 "blocked": bool(session.pop(BLOCKED_KEY, False)), "exit_url": exit_path}
        request.real_user = real
        request.user = target
        return self.get_response(request)
