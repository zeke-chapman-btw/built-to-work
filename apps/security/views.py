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
    if not services.covered(request):
        raise PermissionDenied


def _safe(value, user=None):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return reverse("customer_portal:home") if user is not None and services.is_customer(user) else reverse("internal:home")


class CodeForm(forms.Form):
    code = forms.CharField(label="Code", max_length=20, widget=forms.TextInput(attrs={
        "autocomplete": "one-time-code", "inputmode": "numeric", "autofocus": True}))


def _recently_sent(user, seconds=60):
    from datetime import timedelta
    from django.utils import timezone
    from .models import SentCode
    return SentCode.objects.filter(user=user, used_at__isnull=True,
                                   created_at__gte=timezone.now() - timedelta(seconds=seconds)).exists()


@never_cache
@login_required(login_url="/accounts/login/")
@require_http_methods(["GET", "POST"])
def verify(request):
    _staff(request)
    target = _safe(request.GET.get("next") or request.POST.get("next"), request.user)
    row = services.device(request.user)
    if not row:
        services.mark(request, "ok")
        return redirect(target)
    resend = bool(request.POST.get("resend"))
    form = CodeForm(request.POST if request.method == "POST" and not resend else None)
    problem = ""
    if row.method != "app" and (resend or (request.method == "GET" and not _recently_sent(request.user))):
        problem = services.send_code(request.user, row.method)
        if not problem and resend:
            messages.success(request, "A new code is on its way.")
    if request.method == "POST" and not resend and form.is_valid():
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
            form.add_error("code", "That code didn't work. Use the newest code, or one of your recovery codes.")
    return render(request, "security/verify.html", {"form": form, "next": target, "page_title": "Two-step sign-in",
                                                    "method": row.method, "where": services.masked(row.method, request.user),
                                                    "problem": problem})


@never_cache
@login_required(login_url="/accounts/login/")
@require_http_methods(["GET", "POST"])
def setup(request):
    _staff(request)
    if services.device(request.user):
        messages.info(request, "Two-step sign-in is already on.")
        return redirect(services.home_url(request.user))
    user = request.user
    required = request.GET.get("required") == "1" or request.POST.get("required") == "1"
    method = request.GET.get("method") or request.POST.get("method") or ""
    if method not in ("app", "text", "email") or not services.can_use(method, user):
        options = [{"key": key, "label": label, "where": services.masked(key, user), "ok": services.can_use(key, user)}
                   for key, label in (("text", "Text me a code"), ("email", "Email me a code"),
                                      ("app", "Use an authenticator app"))]
        return render(request, "security/setup_choose.html", {"page_title": "Two-step sign-in", "options": options,
                                                              "required": required, "home_url": services.home_url(user)})
    row = TwoStepDevice.objects.filter(user=user, confirmed_at__isnull=True, method=method).first()
    resend = bool(request.POST.get("resend"))
    form = CodeForm(request.POST if request.method == "POST" and not resend else None)
    codes, problem, secret = None, "", ""
    if method == "app":
        secret = services.secret_of(row) if row and request.method == "POST" else services.start(user, "app")
    elif request.method == "GET" or resend:
        if not row:
            services.start(user, method)
        problem = services.send_code(user, method)
    if request.method == "POST" and not resend and form.is_valid():
        codes = services.confirm(user, form.cleaned_data["code"])
        if codes:
            services.mark(request, "ok")
        else:
            form.add_error("code", "That code didn't match. Try the newest code" +
                           (" in your app." if method == "app" else ", or send a new one."))
    context = {"page_title": "Two-step sign-in", "form": form, "codes": codes, "required": required, "method": method,
               "where": services.masked(method, user), "problem": problem, "home_url": services.home_url(user)}
    if method == "app" and not codes:
        account = user.email or user.get_username()
        context.update(secret=" ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
                       qr=totp.qr_data_uri(totp.uri(secret, account)))
    return render(request, "security/setup.html", context)


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
    return redirect(services.home_url(request.user))


@login_required(login_url="/accounts/login/")
@require_POST
def new_codes(request):
    _staff(request)
    if not services.device(request.user):
        raise Http404
    codes = services.new_recovery_codes(request.user)
    return render(request, "security/codes.html", {"page_title": "Recovery codes", "codes": codes,
                                                   "home_url": services.home_url(request.user)})


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
        if field in ("require_two_step_managers", "require_two_step_staff", "require_two_step_customers", "error_alerts"):
            setattr(row, field, request.POST.get("value") == "1")
            row.updated_by = request.user
            row.save()
            messages.success(request, "Saved.")
        return redirect("security:settings")
    from django.contrib.auth import get_user_model
    from apps.accounts.roles import manager
    from .models import PersonRequirement
    staff = list(get_user_model().objects.filter(is_active=True, is_staff=True).order_by("first_name", "username"))
    on = set(TwoStepDevice.objects.filter(confirmed_at__isnull=False).values_list("user_id", flat=True))
    personal = set(PersonRequirement.objects.values_list("user_id", flat=True))
    pages = [{"slug": slug, "title": title, "row": LegalPage.objects.filter(slug=slug).first()} for slug, title in LEGAL.items()]
    return render(request, "security/settings.html", {
        "page_title": "Settings", "row": row, "errors": ErrorAlert.objects.all()[:15], "pages": pages,
        "managers": [{"user": u, "on": u.pk in on, "personal": u.pk in personal, "group": services.required_by_group(u),
                      "role": "Admin" if u.is_superuser else "Manager" if manager(u) else "Staff"} for u in staff],
        "render_service": __import__("os").environ.get("RENDER_SERVICE_NAME") or "btw-preview"})


@login_required(login_url="/admin/login/")
@require_POST
def require_person(request, user_id):
    """Admins turn two-step on as a requirement for one person (or take that requirement away)."""
    _admin(request)
    from django.contrib.auth import get_user_model
    from apps.core.audit import record_audit
    from .models import PersonRequirement
    target = get_object_or_404(get_user_model(), pk=user_id, is_staff=True)
    want = request.POST.get("value") == "1"
    if want:
        PersonRequirement.objects.get_or_create(user=target, defaults={"set_by": request.user})
        messages.success(request, f"Two-step sign-in is now required for {target.get_full_name() or target.username}. "
                                  "They'll set it up the next time they sign in.")
    else:
        PersonRequirement.objects.filter(user=target).delete()
        messages.success(request, f"{target.get_full_name() or target.username} no longer has to use two-step sign-in "
                                  "(unless their role requires it).")
    record_audit(actor=request.user, action="security.two_step_required" if want else "security.two_step_not_required",
                 instance=target, source="security_settings", new_data={"required": want},
                 reason="Two-step sign-in requirement changed for one person.")
    return redirect(_safe(request.POST.get("next")) if request.POST.get("next") else "security:settings")


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


# ---------------------------------------------------------------- Settings → Security → Copy setup

@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def setup_copy(request):
    _admin(request)
    from django.http import HttpResponse
    from django.utils import timezone
    from . import setup_transfer as transfer
    result = problem = None
    if request.method == "POST" and request.POST.get("action") == "export":
        chosen = request.POST.getlist("item")
        if not chosen:
            messages.error(request, "Tick at least one thing to copy.")
            return redirect("security:setup-copy")
        response = HttpResponse(transfer.export(chosen), content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="btw-setup-{timezone.localdate():%Y-%m-%d}.json"'
        return response
    if request.method == "POST" and request.POST.get("action") == "import":
        upload = request.FILES.get("file")
        if not upload:
            problem = "Choose the setup file you downloaded."
        elif upload.size > 60 * 1024 * 1024:
            problem = "That file is too big."
        elif not request.POST.get("confirm"):
            problem = "Tick the box to confirm."
        else:
            try:
                bundle = transfer.read(upload.read())
                behind = transfer.differences(bundle)
                if behind:
                    problem = ("The two copies of the app are on different versions (" + ", ".join(behind) +
                               "). Publish the same release to both, then try again.")
                else:
                    created, updated = transfer.apply(bundle, request.user)
                    result = dict(transfer.summary(bundle), created=created, updated=updated)
                    from apps.core.audit import record_audit
                    record_audit(actor=request.user, action="setup.copied_in", instance=request.user, source="setup_copy",
                                 new_data={"groups": result["groups"], "created": created, "updated": updated},
                                 reason="Setup copied in from another copy of the app.")
            except ValueError as exc:
                problem = str(exc)
            except Exception as exc:
                problem = f"Nothing was changed. The copy stopped on a problem: {str(exc)[:200]}"
    from django.conf import settings as django_settings
    return render(request, "security/setup_copy.html", {
        "page_title": "Settings", "catalog": transfer.catalog(), "result": result, "problem": problem,
        "site": getattr(django_settings, "PARTICIPANT_PORTAL_BASE_URL", "") or request.get_host()})
