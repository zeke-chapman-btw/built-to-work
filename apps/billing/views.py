"""Customer → Billing tab actions, Settings → Billing (Stripe keys), the bank setup link and the webhook."""
import json
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.roles import manager
from apps.internal_ui.record_editors import editor_response, editor_success

from . import pricing, services, stripe_api
from .delivery import position_progress
from .models import BillingProfile, MonthlyAgreement, PositionCharge, StripeSettings


def _manager(request):
    if not manager(request.user):
        raise PermissionDenied("Billing is managed by Admins and Managers.")


def _org(request, org_id):
    _manager(request)
    from apps.customers.views import org_for
    return org_for(request, org_id)


def _tab(org):
    return reverse("customers:workspace", args=[org.pk, "billing"])


def base_url(request):
    configured = (getattr(settings, "PARTICIPANT_PORTAL_BASE_URL", "") or "").rstrip("/")
    return configured or request.build_absolute_uri("/").rstrip("/")


def billing_context(org):
    """For the customer page's Billing tab."""
    services.refresh_dates()
    profile = services.profile_for(org)
    ending = timezone.localdate() + timedelta(days=45)
    return {"billing": profile, "billing_ready": stripe_api.configured(),
            "billing_test": stripe_api.configured() and stripe_api.test_mode(),
            "agreements": MonthlyAgreement.objects.filter(organization=org).select_related("approved_by")[:20],
            "charges": PositionCharge.objects.filter(organization=org).select_related("approved_by")[:30],
            "ending_soon": ending, "delivery": position_progress(org)}


# ----------------------------------------------------------- bank account

@login_required(login_url="/admin/login/")
@require_POST
def send_bank_link(request, org_id):
    org = _org(request, org_id)
    profile = services.profile_for(org)
    email = profile.contact_email()
    if not email:
        messages.error(request, "Add a main contact with an email (or a billing email) first.")
        return redirect(_tab(org))
    link = base_url(request) + reverse("billing:bank", args=[services.setup_token(profile)])
    from django.core.mail import send_mail
    from apps.messaging import store
    config = store.email_settings()
    body = (f"Hello,\n\nPlease connect the bank account Built to Work should use for position fees and monthly service "
            f"for {org.name}. It takes about two minutes and is handled securely by Stripe.\n\n{link}\n\n"
            f"This link works for {services.LINK_DAYS} days. Questions? Reply to this email or call your Built to Work account manager.\n")
    try:
        send_mail(f"Set up bank payments for {org.name}", body, config.get("from_address") or None, [email],
                  fail_silently=False, connection=store.smtp_connection())
    except Exception as exc:
        messages.error(request, f"The email didn't send: {exc}")
    else:
        messages.success(request, f"Bank setup link sent to {email}.")
    return redirect(_tab(org))


@require_http_methods(["GET"])
def bank(request, token):
    """Public page from the emailed link: send the customer to Stripe to connect their bank."""
    try:
        profile = services.profile_from_token(token)
    except (signing.BadSignature, BillingProfile.DoesNotExist):
        return render(request, "billing/bank_public.html", {"problem": "This link has expired. Ask Built to Work for a new one."})
    if request.GET.get("go") == "1":
        try:
            return HttpResponseRedirect(services.checkout_url(profile, base_url(request)))
        except stripe_api.StripeError:
            return render(request, "billing/bank_public.html", {"org": profile.organization,
                          "problem": "Bank setup isn't available right now. Please try again later."})
    return render(request, "billing/bank_public.html", {"org": profile.organization, "profile": profile, "token": token})


@require_http_methods(["GET"])
def bank_done(request, token):
    try:
        profile = services.profile_from_token(token)
    except (signing.BadSignature, BillingProfile.DoesNotExist):
        return render(request, "billing/bank_public.html", {"problem": "This link has expired."})
    session_id = request.GET.get("session_id", "")
    if session_id.startswith("cs_"):
        try:
            session = stripe_api.call("GET", f"checkout/sessions/{session_id}")
            if session.get("customer") == profile.stripe_customer_id and session.get("setup_intent"):
                profile = services.finish_setup(session["setup_intent"]) or profile
        except stripe_api.StripeError:
            pass
    return render(request, "billing/bank_public.html", {"org": profile.organization, "profile": profile, "done": True})


# ----------------------------------------------------------- account manager

class ProfileForm(forms.ModelForm):
    account_manager = forms.ModelChoiceField(queryset=None, required=False, label="BTW account manager",
                                             help_text="Gets billing reminders for this customer, like an agreement ending.")

    class Meta:
        model = BillingProfile
        fields = ("account_manager", "billing_email")
        labels = {"billing_email": "Billing email"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account_manager"].queryset = get_user_model().objects.filter(is_active=True, is_staff=True).order_by("first_name", "last_name")
        self.fields["account_manager"].label_from_instance = lambda u: u.get_full_name() or u.get_username()


def _editor(request, form, title, org, fields_template="internal/ui/form_fields.html", **extra):
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
                           context={"form": form, "page_title": title, "editor_context": org.name, "save_label": "Save",
                                    "back": _tab(org), "back_label": org.name, "fields_template": fields_template, **extra})


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def edit_profile(request, org_id):
    org = _org(request, org_id)
    form = ProfileForm(request.POST or None, instance=services.profile_for(org))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Billing details saved.")
        return editor_success(request, _tab(org))
    return _editor(request, form, "Billing details", org)


# ----------------------------------------------------------- agreements

class AgreementForm(forms.Form):
    amount = forms.DecimalField(label="Monthly amount ($)", min_value=1, max_digits=9, decimal_places=2)
    starts_on = forms.DateField(label="First month starts", widget=forms.DateInput(attrs={"type": "date"}))
    ends_on = forms.DateField(label="Agreement ends", widget=forms.DateInput(attrs={"type": "date"}),
                              help_text="Charges stop after this date. It does not renew until an Admin or Manager renews it.")
    description = forms.CharField(label="What it's for", max_length=200, required=False)

    def clean(self):
        data = super().clean()
        if data.get("starts_on") and data.get("ends_on") and data["ends_on"] <= data["starts_on"]:
            raise forms.ValidationError("The end date has to be after the start date.")
        if data.get("ends_on") and data["ends_on"] < timezone.localdate():
            raise forms.ValidationError("The end date is already past.")
        return data


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def agreement_new(request, org_id, renew_id=None):
    org = _org(request, org_id)
    old = get_object_or_404(MonthlyAgreement, pk=renew_id, organization=org) if renew_id else None
    initial = {}
    if old:
        length = (old.ends_on - old.starts_on)
        initial = {"amount": old.amount, "starts_on": old.ends_on + timedelta(days=1),
                   "ends_on": old.ends_on + timedelta(days=1) + length, "description": old.description}
    form = AgreementForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        agreement = MonthlyAgreement.objects.create(organization=org, amount_cents=int(d["amount"] * 100), starts_on=d["starts_on"],
                                                    ends_on=d["ends_on"], description=d["description"], renews=old,
                                                    approved_by=request.user)
        services.start_agreement(agreement)
        messages.success(request, ("Renewal approved. " if old else "Monthly agreement saved. ") +
                         {"active": "Monthly charges have started.", "scheduled": "Charges start on the start date.",
                          "waiting": "Charges start once the customer connects their bank account.",
                          "not_set_up": "Charges start once Stripe is connected in Settings → Billing."}.get(agreement.status, agreement.error))
        return editor_success(request, _tab(org))
    return _editor(request, form, "Renew agreement" if old else "New monthly agreement", org)


@login_required(login_url="/admin/login/")
@require_POST
def agreement_cancel(request, org_id, pk):
    org = _org(request, org_id)
    agreement = get_object_or_404(MonthlyAgreement, pk=pk, organization=org)
    try:
        services.cancel_agreement(agreement)
    except stripe_api.StripeError as exc:
        messages.error(request, f"Stripe didn't cancel it: {exc}")
    else:
        messages.success(request, "Agreement canceled. No more monthly charges.")
    return redirect(_tab(org))


@login_required(login_url="/admin/login/")
@require_POST
def charge_retry(request, org_id, pk):
    org = _org(request, org_id)
    charge = get_object_or_404(PositionCharge, pk=pk, organization=org)
    charge = services.retry_charge(charge) if charge.status == "failed" else services.attempt_charge(charge)
    messages.success(request, f"Charge is now: {charge.get_status_display()}.")
    return redirect(_tab(org))


# ----------------------------------------------------------- Settings → Billing

class KeysForm(forms.Form):
    secret_key = forms.CharField(label="Stripe secret key", required=False, widget=forms.PasswordInput(render_value=False),
                                 help_text="Starts with sk_test_ (test mode) or sk_live_ (real money). Leave blank to keep the saved key.")
    webhook_secret = forms.CharField(label="Webhook signing secret", required=False, widget=forms.PasswordInput(render_value=False),
                                     help_text="Starts with whsec_. Leave blank to keep the saved one.")

    def clean_secret_key(self):
        value = self.cleaned_data["secret_key"].strip()
        if value and not value.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
            raise forms.ValidationError("That doesn't look like a Stripe secret key.")
        return value

    def clean_webhook_secret(self):
        value = self.cleaned_data["webhook_secret"].strip()
        if value and not value.startswith("whsec_"):
            raise forms.ValidationError("That doesn't look like a webhook signing secret.")
        return value


def _admin(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only administrators can change the Stripe connection.")


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def stripe_settings(request):
    _admin(request)
    row = StripeSettings.current()
    form = KeysForm(request.POST or None)
    if request.method == "POST" and request.POST.get("test") == "1":
        try:
            stripe_api.call("GET", "balance")
        except stripe_api.StripeError as exc:
            row.last_test_ok, row.last_test_detail = False, str(exc)[:240]
        else:
            row.last_test_ok, row.last_test_detail = True, "Connected"
        row.last_test_at = timezone.now()
        row.save()
        return redirect("billing:settings")
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["secret_key"]:
            row.secret_key_encrypted = stripe_api.encrypt(form.cleaned_data["secret_key"])
            row.product_id, row.last_test_ok = "", None
        if form.cleaned_data["webhook_secret"]:
            row.webhook_secret_encrypted = stripe_api.encrypt(form.cleaned_data["webhook_secret"])
        row.updated_by = request.user
        row.save()
        messages.success(request, "Stripe settings saved. Press Test to check the connection.")
        return redirect("billing:settings")
    key = stripe_api.secret_key() if row.secret_key_encrypted else ""
    return render(request, "billing/settings.html", {
        "page_title": "Settings", "row": row, "form": form, "has_key": bool(key),
        "mode": ("Test mode" if key.startswith(("sk_test_", "rk_test_")) else "Live — real money") if key else "",
        "has_webhook": bool(row.webhook_secret_encrypted),
        "webhook_url": base_url(request) + reverse("billing:webhook"),
        "events": ", ".join(EVENTS),
        "prices": {"FIRST": f"{pricing.FIRST:,}", "SECOND": f"{pricing.SECOND:,}", "AFTER": f"{pricing.AFTER:,}",
                   "GENERAL": f"{pricing.GENERAL:,}"}})


EVENTS = ("checkout.session.completed", "setup_intent.succeeded", "setup_intent.setup_failed", "payment_intent.succeeded",
          "payment_intent.processing", "payment_intent.payment_failed", "invoice.paid", "invoice.payment_failed",
          "customer.subscription.updated", "customer.subscription.deleted")


@csrf_exempt
@require_POST
def webhook(request):
    if not stripe_api.verify(request.body, request.headers.get("Stripe-Signature", ""), stripe_api.webhook_secret()):
        return HttpResponse(status=400)
    try:
        event = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest()
    try:
        services.handle_event(event)
    except Exception:
        # Let Stripe send it again later.
        from .models import StripeEvent
        StripeEvent.objects.filter(event_id=event.get("id", "")).delete()
        import logging
        logging.getLogger(__name__).exception("STRIPE_WEBHOOK_FAILED %s", event.get("type"))
        return HttpResponse(status=500)
    return HttpResponse("ok")
