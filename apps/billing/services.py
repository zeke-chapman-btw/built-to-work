"""Billing actions. Stripe calls happen here; views and webhooks only call these."""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from . import pricing, stripe_api
from .models import BillingProfile, MonthlyAgreement, PositionCharge, StripeSettings

log = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
LINK_SALT = "btw-bank-setup"
LINK_DAYS = 30


def profile_for(org):
    profile, _ = BillingProfile.objects.get_or_create(organization=org)
    return profile


def _stamp(day, at=time(0)):
    return int(datetime.combine(day, at, EASTERN).timestamp())


# ------------------------------------------------------------- bank account

def ensure_customer(profile):
    if profile.stripe_customer_id:
        return profile.stripe_customer_id
    org = profile.organization
    data = stripe_api.call("POST", "customers", {"name": org.legal_name or org.name, "email": profile.contact_email() or None,
                                                 "metadata": {"btw_organization": str(org.pk)}},
                           idempotency_key=f"btw-customer-{org.pk}")
    profile.stripe_customer_id = data["id"]
    profile.save(update_fields=["stripe_customer_id", "updated_at"])
    return profile.stripe_customer_id


def setup_token(profile):
    return signing.dumps({"org": str(profile.organization_id)}, salt=LINK_SALT)


def profile_from_token(token):
    data = signing.loads(token, salt=LINK_SALT, max_age=LINK_DAYS * 86400)
    return BillingProfile.objects.select_related("organization").get(organization_id=data["org"])


def checkout_url(profile, base):
    """A fresh Stripe page where the customer connects their bank account."""
    customer = ensure_customer(profile)
    token = setup_token(profile)
    done = base + reverse("billing:bank-done", args=[token]) + "?session_id={CHECKOUT_SESSION_ID}"
    session = stripe_api.call("POST", "checkout/sessions", {
        "mode": "setup", "currency": "usd", "customer": customer, "payment_method_types": ["us_bank_account"],
        "payment_method_options": {"us_bank_account": {"verification_method": "automatic"}},
        "success_url": done, "cancel_url": base + reverse("billing:bank", args=[token]),
        "metadata": {"btw_organization": str(profile.organization_id)},
    })
    return session["url"]


def finish_setup(setup_intent_id):
    """Save the bank account from a finished (or pending) setup."""
    intent = stripe_api.call("GET", f"setup_intents/{setup_intent_id}", {"expand": ["payment_method"]})
    profile = BillingProfile.objects.filter(stripe_customer_id=intent.get("customer") or "-").first()
    if not profile:
        return None
    method = intent.get("payment_method") or {}
    method_id = method.get("id") if isinstance(method, dict) else method
    bank = (method.get("us_bank_account") or {}) if isinstance(method, dict) else {}
    status = intent.get("status")
    if status == "succeeded" and method_id:
        profile.bank_status = BillingProfile.Bank.READY
    elif status in ("processing", "requires_action") and method_id:
        profile.bank_status = BillingProfile.Bank.PENDING
    else:
        return profile
    profile.payment_method_id = method_id
    profile.mandate_id = intent.get("mandate") or ""
    profile.bank_label = " ".join(p for p in (bank.get("bank_name"), f"••{bank['last4']}" if bank.get("last4") else "") if p)
    profile.bank_added_at = timezone.now()
    profile.save()
    if profile.bank_status == BillingProfile.Bank.READY:
        stripe_api.call("POST", f"customers/{profile.stripe_customer_id}",
                        {"invoice_settings": {"default_payment_method": method_id}})
        run_waiting(profile.organization)
    return profile


def run_waiting(org):
    for charge in PositionCharge.objects.filter(organization=org, status__in=["waiting", "not_set_up"]):
        attempt_charge(charge)
    for agreement in MonthlyAgreement.objects.filter(organization=org, status__in=["waiting", "not_set_up"]):
        start_agreement(agreement)


# ---------------------------------------------------------- position fees

def record_position_charge(*, org, need, revision, kind, quantity, actor):
    dollars, words = pricing.position_fee(kind, quantity)
    charge, created = PositionCharge.objects.get_or_create(revision=revision, defaults=dict(
        organization=org, need=need, title=revision.title or "Position", kind=kind, quantity=quantity or 0,
        amount_cents=dollars * 100, breakdown=words, approved_by=actor,
        status=PositionCharge.Status.NO_CHARGE if kind == "none" else PositionCharge.Status.WAITING))
    if created and kind != "none":
        transaction.on_commit(lambda: attempt_charge(PositionCharge.objects.get(pk=charge.pk)))
    return charge


def attempt_charge(charge):
    if charge.kind == "none" or charge.status in ("processing", "paid", "no_charge", "canceled"):
        return charge
    if not stripe_api.configured():
        charge.status = PositionCharge.Status.NOT_SET_UP
        charge.save(update_fields=["status", "updated_at"])
        return charge
    profile = profile_for(charge.organization)
    if profile.bank_status != BillingProfile.Bank.READY:
        charge.status = PositionCharge.Status.WAITING
        charge.save(update_fields=["status", "updated_at"])
        return charge
    data = {"amount": charge.amount_cents, "currency": "usd", "customer": profile.stripe_customer_id,
            "payment_method": profile.payment_method_id, "payment_method_types": ["us_bank_account"],
            "confirm": True, "off_session": True, "description": f"Position fee: {charge.title}"[:200],
            "statement_descriptor_suffix": "POSITION",
            "metadata": {"btw_charge": str(charge.pk), "btw_organization": str(charge.organization_id)}}
    if profile.mandate_id:
        data["mandate"] = profile.mandate_id
    try:
        intent = stripe_api.call("POST", "payment_intents", data,
                                 idempotency_key=f"btw-charge-{charge.pk}-{charge.payment_intent_id or 'first'}")
    except stripe_api.StripeError as exc:
        charge.status, charge.error = PositionCharge.Status.FAILED, str(exc)[:240]
        charge.save(update_fields=["status", "error", "updated_at"])
        return charge
    charge.payment_intent_id = intent.get("id", "")
    apply_intent(charge, intent)
    return charge


def apply_intent(charge, intent):
    status = intent.get("status")
    if status == "succeeded":
        charge.status, charge.paid_at, charge.error = PositionCharge.Status.PAID, timezone.now(), ""
    elif status == "processing":
        charge.status, charge.error = PositionCharge.Status.PROCESSING, ""
    elif status in ("requires_payment_method", "canceled", "requires_action"):
        error = intent.get("last_payment_error") or {}
        charge.status = PositionCharge.Status.FAILED
        charge.error = (error.get("message") or "The bank payment didn't go through.")[:240]
    charge.save()


def retry_charge(charge):
    if charge.status != PositionCharge.Status.FAILED:
        return charge
    charge.status, charge.error = PositionCharge.Status.WAITING, ""
    charge.payment_intent_id = charge.payment_intent_id + "r" if charge.payment_intent_id else "r"
    charge.save()
    return attempt_charge(charge)


# ------------------------------------------------------- monthly agreements

def ensure_product():
    row = StripeSettings.current()
    if row.product_id:
        return row.product_id
    data = stripe_api.call("POST", "products", {"name": "Built to Work monthly service"}, idempotency_key="btw-monthly-product")
    row.product_id = data["id"]
    row.save(update_fields=["product_id", "updated_at"])
    return row.product_id


def start_agreement(agreement):
    if agreement.subscription_id or agreement.status in ("ended", "canceled"):
        return agreement
    today = timezone.localdate()
    if agreement.ends_on < today:
        agreement.status = MonthlyAgreement.Status.ENDED
        agreement.save(update_fields=["status", "updated_at"])
        return agreement
    if not stripe_api.configured():
        agreement.status = MonthlyAgreement.Status.NOT_SET_UP
        agreement.save(update_fields=["status", "updated_at"])
        return agreement
    profile = profile_for(agreement.organization)
    if profile.bank_status != BillingProfile.Bank.READY:
        agreement.status = MonthlyAgreement.Status.WAITING
        agreement.save(update_fields=["status", "updated_at"])
        return agreement
    data = {"customer": profile.stripe_customer_id, "default_payment_method": profile.payment_method_id,
            "items": [{"price_data": {"currency": "usd", "unit_amount": agreement.amount_cents, "product": ensure_product(),
                                      "recurring": {"interval": "month"}}}],
            "payment_settings": {"payment_method_types": ["us_bank_account"]},
            "cancel_at": _stamp(agreement.ends_on + timedelta(days=1)), "proration_behavior": "none",
            "description": (agreement.description or "Monthly service")[:200],
            "metadata": {"btw_agreement": str(agreement.pk), "btw_organization": str(agreement.organization_id)}}
    if agreement.starts_on > today:
        data["trial_end"] = _stamp(agreement.starts_on, time(9))
    try:
        sub = stripe_api.call("POST", "subscriptions", data, idempotency_key=f"btw-agreement-{agreement.pk}")
    except stripe_api.StripeError as exc:
        agreement.error = str(exc)[:240]
        agreement.save(update_fields=["error", "updated_at"])
        return agreement
    agreement.subscription_id = sub.get("id", "")
    agreement.status = MonthlyAgreement.Status.SCHEDULED if agreement.starts_on > today else MonthlyAgreement.Status.ACTIVE
    agreement.error = ""
    agreement.save()
    return agreement


def cancel_agreement(agreement):
    if agreement.subscription_id and stripe_api.configured():
        stripe_api.call("DELETE", f"subscriptions/{agreement.subscription_id}")
    agreement.status = MonthlyAgreement.Status.CANCELED
    agreement.save(update_fields=["status", "updated_at"])
    return agreement


def refresh_dates():
    """Agreements past their end date are Ended; scheduled ones that started are Active."""
    today = timezone.localdate()
    MonthlyAgreement.objects.filter(status__in=["active", "scheduled", "waiting", "not_set_up"], ends_on__lt=today).update(status="ended")
    MonthlyAgreement.objects.filter(status="scheduled", starts_on__lte=today).update(status="active")


# ---------------------------------------------------------------- webhooks

def handle_event(event):
    from .models import StripeEvent
    from django.db import IntegrityError
    try:
        with transaction.atomic():
            StripeEvent.objects.create(event_id=event["id"], kind=event.get("type", ""))
    except IntegrityError:
        return "duplicate"
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if kind == "checkout.session.completed" and obj.get("mode") == "setup" and obj.get("setup_intent"):
        finish_setup(obj["setup_intent"])
    elif kind in ("setup_intent.succeeded", "setup_intent.setup_failed"):
        if kind == "setup_intent.succeeded":
            finish_setup(obj["id"])
        else:
            BillingProfile.objects.filter(stripe_customer_id=obj.get("customer") or "-", bank_status="pending").update(
                bank_status="none", payment_method_id="", bank_label="")
    elif kind.startswith("payment_intent."):
        charge = PositionCharge.objects.filter(payment_intent_id=obj.get("id") or "-").first()
        if charge:
            apply_intent(charge, obj)
    elif kind in ("invoice.paid", "invoice.payment_failed"):
        agreement = MonthlyAgreement.objects.filter(subscription_id=obj.get("subscription") or "-").first()
        if agreement and obj.get("amount_due"):
            agreement.last_payment_status = "paid" if kind == "invoice.paid" else "failed"
            agreement.last_payment_at = timezone.now()
            agreement.save(update_fields=["last_payment_status", "last_payment_at", "updated_at"])
    elif kind == "customer.subscription.deleted":
        MonthlyAgreement.objects.filter(subscription_id=obj.get("id") or "-").exclude(status="canceled").update(status="ended")
    elif kind == "customer.subscription.updated" and obj.get("status") == "active":
        MonthlyAgreement.objects.filter(subscription_id=obj.get("id") or "-", status="scheduled").update(status="active")
    return "ok"
