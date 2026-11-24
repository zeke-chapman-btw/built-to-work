"""Customer onboarding: staff pages, the customer's link, and Settings → Customer onboarding."""
from django import forms as djforms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.roles import manager
from apps.internal_ui.record_editors import editor_response, editor_success

from . import services
from .forms import CompanyForm, ContactsForm, DocumentsForm, HiringForm, SetupForm
from .models import AgreementVersion, CustomerOnboarding, OnboardingQuestion

STEPS = (("company", "Company"), ("contacts", "Contacts"), ("hiring", "Your work & hiring"),
         ("documents", "W-9 & agreement"), ("setup", "Access & billing"), ("review", "Review"))
PUBLIC_STEPS = tuple(s for s in STEPS if s[0] != "setup")


def _manager(request):
    if not manager(request.user):
        raise PermissionDenied("Customer onboarding is for Admins and Managers.")


def _base(request):
    from apps.billing.views import base_url
    return base_url(request)


# ------------------------------------------------------------------ staff

@login_required(login_url="/admin/login/")
def onboarding_list(request):
    _manager(request)
    rows = CustomerOnboarding.objects.exclude(status="canceled").select_related("organization", "created_by")
    show = request.GET.get("show", "open")
    if show == "open":
        rows = rows.exclude(status="completed")
    rows = list(rows[:100])
    base = _base(request)
    for row in rows:
        row.link = "" if row.organization_id else base + reverse("onboarding:public", args=[services.token_for(row)])
        row.contact = (row.contacts or {}).get("main") or {}
        row.parts = _parts(row)
        row.parts_done = sum(1 for _label, ok in row.parts if ok)
        by = row.created_by
        row.started_by = (f"{by.first_name} {by.last_name[:1]}.".strip() if by and by.first_name else (by.get_username() if by else ""))
    return render(request, "onboarding/list.html", {"page_title": "Customer onboarding", "rows": rows, "show": show})


class StartForm(djforms.Form):
    name = djforms.CharField(label="Company name", max_length=200, help_text="So you can tell this one apart in the list. The customer can correct it later.")
    email = djforms.EmailField(label="Main contact email (optional)", required=False, help_text="Used when you send them the link.")


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def start(request):
    _manager(request)
    back = reverse("onboarding:list")
    form = StartForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        row = CustomerOnboarding.objects.create(
            created_by=request.user, company={"name": form.cleaned_data["name"].strip()},
            contacts={"main": {"email": email}} if email else {})
        return editor_success(request, reverse("onboarding:step", args=[row.pk, "company"]))
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
                           context={"form": form, "page_title": "Onboard a customer", "editor_context": "New onboarding form",
                                    "save_label": "Start", "back": back, "back_label": "Customer onboarding",
                                    "fields_template": "internal/ui/form_fields.html"})


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def staff_step(request, pk, step):
    _manager(request)
    row = get_object_or_404(CustomerOnboarding, pk=pk)
    return _step(request, row, step, staff=True)


class SendForm(djforms.Form):
    email = djforms.EmailField(label="Send to", help_text="The customer's main contact. The link works for 60 days.")
    note = djforms.CharField(label="Add a note (optional)", required=False, widget=djforms.Textarea(attrs={"rows": 3}))


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def send(request, pk):
    _manager(request)
    row = get_object_or_404(CustomerOnboarding, pk=pk)
    back = reverse("onboarding:list")
    form = SendForm(request.POST or None, initial={"email": (row.contacts.get("main") or {}).get("email") or row.sent_to})
    link = _base(request) + reverse("onboarding:public", args=[services.token_for(row)])
    if request.method == "POST" and form.is_valid():
        from django.core.mail import send_mail
        from apps.messaging import store
        sender = request.user.get_full_name() or "Built to Work"
        note = form.cleaned_data["note"].strip()
        body = (f"Hello,\n\nWelcome to Built to Work! Please fill out our customer onboarding form. It covers your company, "
                f"contacts, the kind of people you hire, your W-9 and our service agreement. It takes about 15 minutes, and "
                f"you can stop and come back; your answers are saved after each step.\n\n{link}\n\n"
                + (note + "\n\n" if note else "") + f"Thank you,\n{sender}\nBuilt to Work\n")
        try:
            send_mail("Built to Work customer onboarding", body, store.email_settings().get("from_address") or None,
                      [form.cleaned_data["email"]], fail_silently=False, connection=store.smtp_connection())
        except Exception as exc:
            form.add_error(None, f"The email didn't send: {exc}")
        else:
            row.sent_to, row.sent_at = form.cleaned_data["email"], timezone.now()
            if row.status == "draft":
                row.status = CustomerOnboarding.Status.SENT
            row.save()
            messages.success(request, f"Onboarding form sent to {row.sent_to}.")
            return editor_success(request, back)
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
                           context={"form": form, "page_title": "Send onboarding link", "editor_context": row.name,
                                    "save_label": "Send", "back": back, "back_label": "Customer onboarding",
                                    "fields_template": "onboarding/send_fields.html", "link": link})


def _parts(row):
    company, main = row.company or {}, (row.contacts or {}).get("main") or {}
    return [("Company", bool(company.get("name") and (company.get("city") or company.get("phone") or company.get("address_line1")))),
            ("Contacts", bool(main.get("first_name") and main.get("email"))),
            ("Hiring", bool(row.answers)),
            ("W-9", bool(row.w9)),
            ("Agreement", bool(row.signed_at))]


class DeleteForm(djforms.Form):
    confirm = djforms.BooleanField(label="Yes, delete this onboarding form", required=True,
                                   help_text="This removes the form, its answers and any W-9 uploaded to it. A link already sent to the customer stops working. This can't be undone.")


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def delete(request, pk):
    """Remove an onboarding form that was started but never turned into a customer."""
    _manager(request)
    row = get_object_or_404(CustomerOnboarding, pk=pk, organization__isnull=True)
    form = DeleteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        name = row.name
        for doc in row.documents.filter(organization__isnull=True):
            if doc.file:
                doc.file.delete(save=False)
            doc.delete()
        if row.w9:
            row.w9.delete(save=False)
        row.delete()
        messages.success(request, f"Deleted the onboarding form for {name}.")
        return editor_success(request, reverse("onboarding:list"))
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
                           context={"form": form, "page_title": "Delete onboarding form", "editor_context": row.name,
                                    "save_label": "Delete", "back": reverse("onboarding:list"), "back_label": "Customer onboarding",
                                    "fields_template": "internal/ui/form_fields.html"})


@login_required(login_url="/admin/login/")
@require_POST
def cancel(request, pk):
    _manager(request)
    row = get_object_or_404(CustomerOnboarding, pk=pk, organization__isnull=True)
    row.status = CustomerOnboarding.Status.CANCELED
    row.save(update_fields=["status", "updated_at"])
    messages.success(request, "Onboarding canceled.")
    return redirect("onboarding:list")


@login_required(login_url="/admin/login/")
@require_POST
def create(request, pk):
    _manager(request)
    row = get_object_or_404(CustomerOnboarding, pk=pk)
    if not (row.company or {}).get("name"):
        messages.error(request, "Add the company name first.")
        return redirect("onboarding:step", row.pk, "company")
    org = services.create_customer(row, request.user, base_url=_base(request))
    messages.success(request, f"{org.name} is set up.")
    return redirect(reverse("customers:workspace", args=[org.pk, "overview"]))


@login_required(login_url="/admin/login/")
def w9(request, pk):
    _manager(request)
    row = get_object_or_404(CustomerOnboarding, pk=pk)
    if not row.w9:
        raise Http404
    return FileResponse(row.w9.open("rb"), as_attachment=True, filename=row.w9_name or "w9.pdf")


# ------------------------------------------------------------------ customer link

def _from_token(token):
    try:
        row = services.from_token(token)
    except (signing.BadSignature, CustomerOnboarding.DoesNotExist):
        return None
    return None if row.status == "canceled" else row


@require_http_methods(["GET", "POST"])
def public(request, token, step="company"):
    row = _from_token(token)
    if not row:
        return render(request, "onboarding/public_message.html", {"title": "This link isn't available",
                      "text": "It may have expired. Please ask your Built to Work contact for a new one."}, status=404)
    if row.status in ("submitted", "completed"):
        return render(request, "onboarding/public_message.html", {"title": "Thank you!",
                      "text": "We have your onboarding form. Your Built to Work contact will be in touch soon."})
    return _step(request, row, step, staff=False, token=token)


# ------------------------------------------------------------------ the steps

def _url(row, step, staff, token):
    return reverse("onboarding:step", args=[row.pk, step]) if staff else reverse("onboarding:public-step", args=[token, step])


def _step(request, row, step, *, staff, token=None):
    steps = STEPS if staff else PUBLIC_STEPS
    keys = [k for k, _ in steps]
    if step not in keys:
        raise Http404
    index = keys.index(step)
    following = keys[index + 1] if index + 1 < len(keys) else None
    post = request.POST if request.method == "POST" else None
    agreement = AgreementVersion.current()
    if step == "company":
        form = CompanyForm(post, initial=row.company)
    elif step == "contacts":
        form = ContactsForm(post, initial=ContactsForm.initial_from(row.contacts or {}))
    elif step == "hiring":
        initial = {k: v.get("value") for k, v in (row.answers or {}).items()}
        form = HiringForm(post, initial=initial)
    elif step == "documents":
        form = DocumentsForm(post, request.FILES or None, require_signature=not staff and not row.signed_at,
                             initial={"signed_name": row.signed_name, "signed_title": row.signed_title})
    elif step == "setup":
        form = SetupForm(post, initial=row.setup or None)
    else:
        form = None
    if request.method == "POST":
        if step == "review":
            if staff:
                raise Http404
            row.status, row.submitted_at, row.submitted_by_customer = CustomerOnboarding.Status.SUBMITTED, timezone.now(), True
            row.save()
            return render(request, "onboarding/public_message.html", {"title": "Thank you!",
                          "text": "Your onboarding form is in. Your Built to Work contact will review it and be in touch soon."})
        if form.is_valid():
            data = form.cleaned_data
            if step == "company":
                row.company = {k: (v if v is not None else "") for k, v in data.items()}
            elif step == "contacts":
                row.contacts = ContactsForm.to_data(data)
            elif step == "hiring":
                row.answers = form.to_data()
            elif step == "documents":
                if data.get("w9"):
                    row.w9_name = data["w9"].name[:200]
                    row.w9.save(f"{row.pk}-{data['w9'].name}"[:100], data["w9"], save=False)
                if data.get("agree") and (data.get("signed_name") or "").strip():
                    services.sign(row, data, ip=request.META.get("REMOTE_ADDR"))
            elif step == "setup":
                row.setup = SetupForm.to_data(data)
            row.save()
            if request.POST.get("then") == "stay":
                messages.success(request, "Saved.")
                return redirect(_url(row, step, staff, token))
            return redirect(_url(row, following or step, staff, token))
    return render(request, "onboarding/step.html", {
        "page_title": row.name if staff else "Customer onboarding", "row": row, "form": form, "step": step, "staff": staff, "token": token,
        "steps": [{"key": k, "label": label, "url": _url(row, k, staff, token), "current": k == step,
                   "done": i < index} for i, (k, label) in enumerate(steps)],
        "number": index + 1, "total": len(steps),
        "previous": _url(row, keys[index - 1], staff, token) if index else "",
        "agreement": row.agreement if row.signed_at else agreement,
        "sections": services.answer_sections(row), "billing_email": services.billing_email(row),
        "base_template": "shared/workspace_landing.html" if staff else "onboarding/public_base.html",
    })


# ------------------------------------------------------------------ Settings → Customer onboarding

@login_required(login_url="/admin/login/")
def settings_page(request):
    _manager(request)
    base = reverse("customers:directory")
    area_tabs = [dict(label="All customers", url=base), dict(label="Recent activity", url=base + "?listing=activity"),
                 dict(label="Follow-ups", url=base + "?listing=followups"), dict(label="Setup", url=request.path, selected=True)]
    return render(request, "onboarding/settings.html", {
        "page_title": "Customers", "area_tabs": area_tabs, "questions": OnboardingQuestion.objects.order_by("position", "pk"),
        "sections": OnboardingQuestion.Section.choices, "agreement": AgreementVersion.current(),
        "signed": CustomerOnboarding.objects.filter(signed_at__isnull=False).count()})


class QuestionForm(djforms.ModelForm):
    class Meta:
        model = OnboardingQuestion
        fields = ("section", "label", "help_text", "kind", "options", "required", "position", "is_active")
        labels = {"section": "Step section", "label": "Question", "help_text": "Hint under the question", "kind": "Answer type",
                  "required": "Required", "position": "Order", "is_active": "Show this question"}
        help_texts = {"position": "Lower numbers come first (10, 20, 30…)."}
        widgets = {"options": djforms.Textarea(attrs={"rows": 5})}

    def clean(self):
        data = super().clean()
        if data.get("kind") in ("choice", "multi") and len([o for o in (data.get("options") or "").splitlines() if o.strip()]) < 2:
            self.add_error("options", "Add at least two choices, one per line.")
        return data


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def question_edit(request, pk=None):
    _manager(request)
    row = get_object_or_404(OnboardingQuestion, pk=pk) if pk else None
    back = reverse("onboarding:settings")
    form = QuestionForm(request.POST or None, instance=row)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Question saved.")
        return editor_success(request, back)
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
                           context={"form": form, "page_title": "Edit question" if row else "New question",
                                    "editor_context": "Shown on the “Your work & hiring” step. Answers already given keep their wording.",
                                    "save_label": "Save question", "back": back, "back_label": "Customer onboarding",
                                    "fields_template": "internal/ui/form_fields.html"})


class AgreementForm(djforms.Form):
    title = djforms.CharField(max_length=160)
    body = djforms.CharField(label="Agreement text", widget=djforms.Textarea(attrs={"rows": 18}),
                             help_text="Saving creates a new version. Customers who already signed keep the version they signed.")


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def agreement_edit(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only administrators can change the service agreement.")
    current = AgreementVersion.current()
    back = reverse("onboarding:settings")
    form = AgreementForm(request.POST or None, initial={"title": current.title, "body": current.body} if current else None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if not current or d["body"].strip() != current.body.strip() or d["title"] != current.title:
            AgreementVersion.objects.filter(is_active=True).update(is_active=False)
            AgreementVersion.objects.create(title=d["title"], body=d["body"].strip(), created_by=request.user,
                                            version=(current.version + 1) if current else 1)
        messages.success(request, "Agreement saved.")
        return editor_success(request, back)
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
                           context={"form": form, "page_title": "Service agreement", "save_label": "Save new version",
                                    "editor_context": f"Version {current.version}" if current else "", "back": back,
                                    "back_label": "Customer onboarding", "fields_template": "internal/ui/form_fields.html"})
