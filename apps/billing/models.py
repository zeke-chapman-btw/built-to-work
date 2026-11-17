"""Customer billing through Stripe: bank accounts (ACH), position fees and monthly agreements."""
from django.conf import settings
from django.db import models


class StripeSettings(models.Model):
    """One row. Keys are encrypted and never shown again once saved."""
    secret_key_encrypted = models.TextField(blank=True)
    webhook_secret_encrypted = models.TextField(blank=True)
    product_id = models.CharField(max_length=80, blank=True)
    last_test_ok = models.BooleanField(null=True, blank=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_detail = models.CharField(max_length=240, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()

    @classmethod
    def current(cls):
        row, _ = cls.objects.get_or_create(pk=1)
        return row


class BillingProfile(models.Model):
    class Bank(models.TextChoices):
        NONE = "none", "No bank account yet"
        PENDING = "pending", "Waiting for verification"
        READY = "ready", "Ready"

    organization = models.OneToOneField("customers.CustomerOrganization", on_delete=models.PROTECT, related_name="billing")
    account_manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="managed_billing_accounts")
    billing_email = models.EmailField(blank=True, help_text="Where bank setup links and billing reminders go. Blank = the main contact.")
    stripe_customer_id = models.CharField(max_length=80, blank=True)
    payment_method_id = models.CharField(max_length=80, blank=True)
    mandate_id = models.CharField(max_length=80, blank=True)
    bank_status = models.CharField(max_length=10, choices=Bank.choices, default=Bank.NONE)
    bank_label = models.CharField(max_length=120, blank=True)
    bank_added_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()

    def contact_email(self):
        org = self.organization
        return self.billing_email or (org.primary_contact.email if org.primary_contact_id else "") or org.business_email


class PositionCharge(models.Model):
    class Kind(models.TextChoices):
        SKILLED = "skilled", "Skilled position (sets of 3 candidates)"
        GENERAL = "general", "General labor (per person)"
        NONE = "none", "No charge"

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting for bank account"
        NOT_SET_UP = "not_set_up", "Billing not set up"
        PROCESSING = "processing", "Processing"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        NO_CHARGE = "no_charge", "No charge"
        CANCELED = "canceled", "Canceled"

    organization = models.ForeignKey("customers.CustomerOrganization", on_delete=models.PROTECT, related_name="position_charges")
    need = models.ForeignKey("customers.WorkforceNeed", on_delete=models.PROTECT, related_name="charges")
    revision = models.OneToOneField("customers.WorkforceNeedRevision", on_delete=models.PROTECT, related_name="charge")
    title = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    quantity = models.PositiveIntegerField(default=0)
    amount_cents = models.PositiveIntegerField(default=0)
    breakdown = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices)
    payment_intent_id = models.CharField(max_length=80, blank=True)
    error = models.CharField(max_length=240, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        default_permissions = ()

    @property
    def amount(self):
        return self.amount_cents / 100


class MonthlyAgreement(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Starts later"
        ACTIVE = "active", "Active"
        WAITING = "waiting", "Waiting for bank account"
        NOT_SET_UP = "not_set_up", "Billing not set up"
        ENDED = "ended", "Ended"
        CANCELED = "canceled", "Canceled"

    organization = models.ForeignKey("customers.CustomerOrganization", on_delete=models.PROTECT, related_name="monthly_agreements")
    amount_cents = models.PositiveIntegerField()
    starts_on = models.DateField()
    ends_on = models.DateField()
    description = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.WAITING)
    subscription_id = models.CharField(max_length=80, blank=True)
    renews = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="renewals")
    last_payment_status = models.CharField(max_length=20, blank=True)
    last_payment_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=240, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-starts_on",)
        default_permissions = ()

    @property
    def amount(self):
        return self.amount_cents / 100


class StripeEvent(models.Model):
    """Webhook events already handled (Stripe can send one more than once)."""
    event_id = models.CharField(max_length=80, unique=True)
    kind = models.CharField(max_length=80)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
