"""Settings > Staff pay: turn each person's My pay on or off in one list."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.roles import manager, roles_for, ROLE_GROUPS
from apps.staffing.models import StaffProfile
from .views import _staff_context


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def staff_pay(request):
    if not manager(request.user):
        raise PermissionDenied
    if request.method == "POST":
        from apps.accounts.staff_onboarding import set_own_pay_access
        try:
            set_own_pay_access(actor=request.user, profile_id=request.POST.get("profile"), enabled=request.POST.get("enabled") == "1",
                               expected_revision=request.POST.get("revision", ""))
        except (ValidationError, PermissionDenied, StaffProfile.DoesNotExist) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [])) or "That change couldn't be saved. Reload and try again.")
        else:
            messages.success(request, "My pay updated.")
        return redirect(request.path + ("?q=" + request.POST["q"] if request.POST.get("q") else ""))
    q = request.GET.get("q", "").strip()[:80]
    rows = StaffProfile.objects.filter(is_active=True).select_related("user").order_by("last_name", "first_name")
    if q:
        from django.db.models import Q
        rows = rows.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))
    names = {v: k for k, v in ROLE_GROUPS.items()}
    label = {"manager": "Manager", "recruiter": "Recruiter", "event_staff": "Event staff"}
    people = []
    for profile in rows:
        roles = [label[r] for r in sorted(roles_for(profile.user)) if r in label] if profile.user_id else []
        if profile.user_id and profile.user.is_superuser:
            roles.insert(0, "Admin")
        people.append(dict(profile=profile, roles=roles, revision=profile.updated_at.isoformat()))
    on = sum(1 for p in people if p["profile"].my_pay_enabled)
    response = render(request, "internal/staff_pay.html", _staff_context(request, page_title="Staff pay", people=people, q=q, on_count=on))
    response["Cache-Control"] = "private, no-store"
    return response
