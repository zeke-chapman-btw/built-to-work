"""Enter and leave the read-only staff view."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.accounts.staff_preview import SESSION_KEY, can_preview, eligible_targets
from apps.core.audit import record_audit


@login_required(login_url="/admin/login/")
@require_POST
def enter(request):
    if not can_preview(request.user):
        raise PermissionDenied
    target = get_object_or_404(eligible_targets(), pk=request.POST.get("user"))
    request.session[SESSION_KEY] = str(target.pk)
    record_audit(actor=request.user, action="staff_preview.entered", instance=target, source="staff_preview",
                 reason="Admin viewed the staff app as this person (read-only).")
    return redirect("internal:home")


@login_required(login_url="/admin/login/")
@require_POST
def exit(request):
    real = getattr(request, "real_user", request.user)
    target_id = request.session.pop(SESSION_KEY, None)
    if target_id and can_preview(real):
        from django.contrib.auth import get_user_model
        target = get_user_model().objects.filter(pk=target_id).first()
        if target:
            record_audit(actor=real, action="staff_preview.exited", instance=target, source="staff_preview")
    return redirect("internal:portal-preview")
