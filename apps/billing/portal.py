"""Customer portal → Billing & documents (company leads only)."""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from . import services, stripe_api
from .models import MonthlyAgreement, PositionCharge

CUSTOMER_KINDS = (("w9", "W-9"), ("insurance", "Certificate of insurance"), ("tax", "Tax exemption"), ("other", "Other"))


def portal_context(org):
    from apps.onboarding.models import CustomerDocument
    profile = services.profile_for(org)
    today = timezone.localdate()
    docs = list(CustomerDocument.objects.filter(organization=org, removed_at__isnull=True, shared=True).order_by("kind", "-uploaded_at"))
    for d in docs:
        d.expired = bool(d.expires_on and d.expires_on < today)
    from .delivery import position_progress
    return {"billing": profile, "billing_ready": stripe_api.configured(),
            "agreements": MonthlyAgreement.objects.filter(organization=org).exclude(status="canceled")[:10],
            "charges": PositionCharge.objects.filter(organization=org).exclude(status="no_charge")[:30],
            "documents": docs, "document_form": CustomerDocumentForm(), "delivery": position_progress(org)}


def _lead(request):
    member = getattr(request, "customer_member", None)
    if not member or member.role != "lead":
        raise PermissionDenied("Billing is available to your company's portal leads.")
    if getattr(request, "customer_preview", False):
        raise PermissionDenied("This is a preview. Customers make these changes themselves.")
    return member.organization


class CustomerDocumentForm(forms.Form):
    kind = forms.ChoiceField(label="Type", choices=CUSTOMER_KINDS)
    upload = forms.FileField(label="File", help_text="PDF or photo, up to 10 MB.")
    expires_on = forms.DateField(label="Expires (if it has a date)", required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean_upload(self):
        upload = self.cleaned_data["upload"]
        if upload.size > 10 * 1024 * 1024:
            raise forms.ValidationError("That file is over 10 MB.")
        if not upload.name.lower().endswith((".pdf", ".png", ".jpg", ".jpeg")):
            raise forms.ValidationError("Upload a PDF, JPG or PNG.")
        return upload


def _views():
    from apps.customers.portal_views import customer_view
    return customer_view


def bank(request):
    org = _lead(request)
    try:
        return HttpResponseRedirect(services.checkout_url(services.profile_for(org), _base(request)))
    except stripe_api.StripeError:
        messages.error(request, "Bank setup isn't available right now. Please try again later or contact Built to Work.")
        return redirect(_tab())


def upload(request):
    from apps.onboarding.models import CustomerDocument
    org = _lead(request)
    form = CustomerDocumentForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
        return redirect(_tab())
    d = form.cleaned_data
    doc = CustomerDocument(organization=org, kind=d["kind"], title=dict(CUSTOMER_KINDS)[d["kind"]], expires_on=d.get("expires_on"),
                           uploaded_by=request.user, from_customer=True, notes="Uploaded by the customer")
    doc.file_name = d["upload"].name[:200]
    doc.file.save(f"{org.pk}-{d['upload'].name}"[:100], d["upload"], save=False)
    doc.save()
    _tell_account_manager(org, doc)
    messages.success(request, f"Thanks! Your {doc.title} was sent to Built to Work.")
    return redirect(_tab())


def download(request, pk):
    from apps.onboarding.models import CustomerDocument
    member = getattr(request, "customer_member", None)
    if not member or member.role != "lead":
        raise PermissionDenied
    doc = get_object_or_404(CustomerDocument, pk=pk, organization_id=member.organization_id, shared=True, removed_at__isnull=True)
    if doc.file:
        return FileResponse(doc.file.open("rb"), as_attachment=True, filename=doc.file_name or f"{doc.title}.pdf")
    if doc.kind == "agreement" and doc.onboarding_id:
        from django.shortcuts import render
        return render(request, "onboarding/signed_agreement.html", {"row": doc.onboarding, "org": member.organization})
    raise Http404


def _tab():
    return reverse("customer_portal:workspace", args=["billing"])


def _base(request):
    from .views import base_url
    return base_url(request)


def _tell_account_manager(org, doc):
    profile = services.profile_for(org)
    manager = profile.account_manager
    if not (manager and manager.email):
        return
    from django.core.mail import send_mail
    from apps.messaging import store
    try:
        send_mail(f"{org.name} uploaded a {doc.title}", f"{org.name} uploaded a {doc.title} in the customer portal. "
                  f"It's on their Documents tab.", store.email_settings().get("from_address") or None, [manager.email],
                  fail_silently=True, connection=store.smtp_connection())
    except Exception:
        pass


# Wrapped with the portal's sign-in check when the URL list loads.
def wrapped():
    customer_view = _views()
    return {"bank": customer_view(require_POST(bank)), "upload": customer_view(require_POST(upload)),
            "download": customer_view(require_http_methods(["GET"])(download))}
