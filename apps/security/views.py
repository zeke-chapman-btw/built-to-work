from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.internal_ui.record_editors import editor_response, editor_success

from . import services, totp
from .models import ErrorAlert, LegalPage, SecuritySettings, TwoStepDevice

LEGAL = {"privacy": "Privacy Policy", "terms": "Terms of Use"}
PLACEHOLDER = ("DRAFT — REPLACE BEFORE GOING LIVE.\n\nThis page is a placeholder so links work. An administrator can paste "
               "Built to Work's {title} in Settings → Security. Have it reviewed by whoever handles legal for the company.")


def _staff(request):
    if not (request.user.is_authenticated and request.user.is_staff):
        raise PermissionDenied


def _safe(value):
    return value if value and value.startswith("/") and not value.startswith("//") else reverse("internal:home")


class CodeForm(forms.Form):
    code = forms.CharField(label="Code", max_length=20, widget=forms.TextInput(attrs={
        "autocomplete": "one-time-code", "inputmode": "numeric", "autofocus": True}))


@never_cache
@login_required(login_url="/accounts/login/")
@require_http_methods(["GET", "POST"])
def verify(request):
    _staff(request)
    target = _safe(request.GET.get("next") or request.POST.get("next"))
    if not services.device(request.user):
        services.mark(request, "ok")
        return redirect(target)
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        from django.db import transaction
        from apps.accounts.staff_services import rate_limit
        with transaction.atomic():
            allowed = rate_limit("two-step-verify", str(request.user.pk), 8)
        if not allowed:
            form.add_error("code", "Too many tries. Wait a few minutes and try again.")
        elif services.check(request.user, form.cleaned_data["code"]):
            request.session.cycle_key()
            services.mark(request, "ok")
            return redirect(target)
        else:
            form.add_error("code", "That code didn't work. Use the newest code in your app, or a recovery code.")
    return render(request, "security/verify.html", {"form": form, "next": target, "page_title": "Two-step sign-in"})


@never_cache
@login_required(login_url="/accounts/login/")
@require_http_methods(["GET", "POST"])
def setup(request):
    _staff(request)
    if services.device(request.user):
        messages.info(request, "Two-step sign-in is already on.")
        return redirect(reverse("internal:settings"))
    row = TwoStepDevice.objects.filter(user=request.user, confirmed_at__isnull=True).first()
    secret = services.secret_of(row) if row and request.method == "POST" else services.start(request.user)
    form = CodeForm(request.POST or None)
    codes = None
    if request.method == "POST" and form.is_valid():
        codes = services.confirm(request.user, form.cleaned_data["code"])
        if codes:
            services.mark(request, "ok")
        else:
            form.add_error("code", "That code didn't match. Check the time on your phone and try the newest code.")
    account = request.user.email or request.user.get_username()
    return render(request, "security/setup.html", {
        "page_title": "Two-step sign-in", "form": form, "secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
        "qr": totp.qr_data_uri(totp.uri(secret, account)), "codes": codes, "required": request.GET.get("required") == "1"})


@login_required(login_url="/accounts/login/")
@require_POST
def turn_off(request):
    _staff(request)
    if services.required(request.user):
        messages.error(request, "Two-step sign-in is required for your role, so it can't be turned off.")
    else:
        services.turn_off(request.user)
        services.mark(request, "ok")
        messages.success(request, "Two-step sign-in is off.")
    return redirect(reverse("internal:settings"))


@login_required(login_url="/accounts/login/")
@require_POST
def new_codes(request):
    _staff(request)
    if not services.device(request.user):
        raise Http404
    codes = services.new_recovery_codes(request.user)
    return render(request, "security/codes.html", {"page_title": "Recovery codes", "codes": codes})


# ---------------------------------------------------------------- Settings → Security

def _admin(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only administrators can change security settings.")


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def settings_page(request):
    _admin(request)
    row = SecuritySettings.current()
    if request.method == "POST":
        field = request.POST.get("field")
        if field in ("require_two_step_managers", "error_alerts"):
            setattr(row, field, request.POST.get("value") == "1")
            row.updated_by = request.user
            row.save()
            messages.success(request, "Saved.")
        return redirect("security:settings")
    from django.contrib.auth import get_user_model
    from apps.accounts.roles import manager
    staff = [u for u in get_user_model().objects.filter(is_active=True, is_staff=True).order_by("first_name") if manager(u)]
    on = set(TwoStepDevice.objects.filter(confirmed_at__isnull=False).values_list("user_id", flat=True))
    pages = [{"slug": slug, "title": title, "row": LegalPage.objects.filter(slug=slug).first()} for slug, title in LEGAL.items()]
    return render(request, "security/settings.html", {
        "page_title": "Settings", "row": row, "errors": ErrorAlert.objects.all()[:15], "pages": pages,
        "managers": [{"user": u, "on": u.pk in on} for u in staff]})


class LegalForm(forms.Form):
    body = forms.CharField(label="Text", widget=forms.Textarea(attrs={"rows": 20}),
                           help_text="Plain text. Blank lines start new paragraphs. Shown to anyone at the public link.")


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def legal_edit(request, slug):
    _admin(request)
    if slug not in LEGAL:
        raise Http404
    row = LegalPage.objects.filter(slug=slug).first()
    back = reverse("security:settings")
    form = LegalForm(request.POST or None, initial={"body": row.body if row else PLACEHOLDER.format(title=LEGAL[slug])})
    if request.method == "POST" and form.is_valid():
        LegalPage.objects.update_or_create(slug=slug, defaults={"title": LEGAL[slug], "body": form.cleaned_data["body"].strip(),
                                                                "updated_by": request.user})
        messages.success(request, f"{LEGAL[slug]} saved.")
        return editor_success(request, back)
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
                           context={"form": form, "page_title": LEGAL[slug], "editor_context": f"Public page: /{slug}/",
                                    "save_label": "Save", "back": back, "back_label": "Security",
                                    "fields_template": "internal/ui/form_fields.html"})


def legal(request, slug):
    if slug not in LEGAL:
        raise Http404
    row = LegalPage.objects.filter(slug=slug).first()
    body = row.body if row else PLACEHOLDER.format(title=LEGAL[slug])
    return render(request, "security/legal.html", {"title": LEGAL[slug], "paragraphs": [p.strip() for p in body.split("\n\n") if p.strip()],
                                                   "updated": row.updated_at if row else None})
