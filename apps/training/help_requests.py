"""Ask for help: anyone signed in can send a question from Help; BTW staff see and handle them."""
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import HelpRequest


def _safe_back(value, fallback="/"):
    value = (value or "").strip()
    return value if value.startswith("/") and not value.startswith("//") else fallback


def who(user):
    """(sent_from, organization, participant) for the person asking."""
    if user.is_staff:
        return "staff", None, None
    try:
        account = user.participant_account
    except Exception:
        account = None
    if account is not None:
        return "participant", None, account.participant
    try:
        identity = user.customer_identity
    except Exception:
        identity = None
    if identity is not None:
        member = identity.memberships.filter(state="active").select_related("organization").first() or identity.memberships.select_related("organization").first()
        return "customer", (member.organization if member else None), None
    return "", None, None


class AskForm(forms.Form):
    message = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))
    page = forms.CharField(max_length=300, required=False)


@login_required
@require_POST
def ask(request):
    back = _safe_back(request.POST.get("back"), "/")
    from apps.participants.portal_preview import SESSION_KEY as PARTICIPANT_PREVIEW
    from apps.customers.portal_preview import SESSION_KEY as CUSTOMER_PREVIEW
    if request.session.get(PARTICIPANT_PREVIEW) or request.session.get(CUSTOMER_PREVIEW):
        return redirect(back + ("&" if "?" in back else "?") + "asked=preview")
    form = AskForm(request.POST)
    sent_from, organization, participant = who(request.user)
    if not form.is_valid() or not sent_from:
        return redirect(back + ("&" if "?" in back else "?") + "asked=error")
    HelpRequest.objects.create(
        user=request.user, sent_from=sent_from, organization=organization, participant=participant,
        name=request.user.get_full_name() or request.user.get_username(), email=request.user.email or "",
        page=_safe_back(form.cleaned_data["page"], "")[:300], message=form.cleaned_data["message"].strip())
    return redirect(back + ("&" if "?" in back else "?") + "asked=1")


def _can_handle(user):
    if not user.is_staff:
        return False
    from .access import audiences_for
    return bool(audiences_for(user) & {"admin", "manager", "recruiter"})


@login_required
@require_GET
def inbox(request):
    if not _can_handle(request.user):
        raise PermissionDenied
    from apps.internal_ui.views import _staff_context
    show = request.GET.get("show", "open")
    rows = HelpRequest.objects.select_related("organization", "participant", "handled_by")
    rows = rows.filter(handled_at__isnull=True) if show == "open" else rows.filter(handled_at__isnull=False)[:100]
    return render(request, "training/help_requests.html", _staff_context(
        request, page_title="Help questions", rows=rows, show=show,
        open_count=HelpRequest.objects.filter(handled_at__isnull=True).count()))


@login_required
@require_POST
def handled(request, pk):
    if not _can_handle(request.user):
        raise PermissionDenied
    item = get_object_or_404(HelpRequest, pk=pk)
    if item.handled_at is None:
        item.handled_at, item.handled_by = timezone.now(), request.user
        item.save(update_fields=["handled_at", "handled_by"])
    return redirect("training:help-requests")


def open_count(user):
    return HelpRequest.objects.filter(handled_at__isnull=True).count() if _can_handle(user) else 0
