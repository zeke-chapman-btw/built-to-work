"""Customer onboarding: one form (sent as a link or filled by staff) that becomes a customer."""
import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from .storage import PrivateOnboardingStorage


class OnboardingQuestion(models.Model):
    class Section(models.TextChoices):
        WORK = "work", "Your work"
        PROCESS = "process", "Your hiring process"
        PEOPLE = "people", "What you look for in people"

    class Kind(models.TextChoices):
        SHORT = "short", "Short answer"
        LONG = "long", "Paragraph"
        CHOICE = "choice", "Pick one"
        MULTI = "multi", "Pick any"
        YESNO = "yesno", "Yes / no"

    section = models.CharField(max_length=10, choices=Section.choices, default=Section.WORK)
    label = models.CharField(max_length=240)
    help_text = models.CharField(max_length=300, blank=True)
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.LONG)
    options = models.TextField(blank=True, help_text="For Pick one / Pick any: one choice per line.")
    required = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("section", "position", "pk")
        default_permissions = ()

    def option_list(self):
        return [line.strip() for line in self.options.splitlines() if line.strip()]

    def __str__(self):
        return self.label


class AgreementVersion(models.Model):
    title = models.CharField(max_length=160, default="Built to Work Service Agreement")
    body = models.TextField()
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-version",)
        default_permissions = ()

    @classmethod
    def current(cls):
        return cls.objects.filter(is_active=True).order_by("-version").first()


class CustomerOnboarding(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Started"
        SENT = "sent", "Sent to customer"
        SUBMITTED = "submitted", "Ready to review"
        COMPLETED = "completed", "Customer created"
        CANCELED = "canceled", "Canceled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    company = models.JSONField(default=dict, blank=True)
    contacts = models.JSONField(default=dict, blank=True)
    answers = models.JSONField(default=dict, blank=True)
    setup = models.JSONField(default=dict, blank=True)
    w9 = models.FileField(upload_to="w9/", storage=PrivateOnboardingStorage(), blank=True,
                          validators=[FileExtensionValidator(("pdf", "png", "jpg", "jpeg"))])
    w9_name = models.CharField(max_length=200, blank=True)
    agreement = models.ForeignKey(AgreementVersion, null=True, blank=True, on_delete=models.PROTECT, related_name="acceptances")
    agreement_title = models.CharField(max_length=160, blank=True)
    agreement_text = models.TextField(blank=True, help_text="Exactly what they agreed to.")
    signed_name = models.CharField(max_length=160, blank=True)
    signed_title = models.CharField(max_length=160, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    signed_ip = models.GenericIPAddressField(null=True, blank=True)
    sent_to = models.EmailField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by_customer = models.BooleanField(default=False)
    organization = models.OneToOneField("customers.CustomerOrganization", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="onboarding")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        default_permissions = ()

    @property
    def name(self):
        return self.company.get("name") or "New customer"


class CustomerDocument(models.Model):
    """A file kept with a customer: W-9, signed agreement, insurance certificate, contracts…"""
    class Kind(models.TextChoices):
        W9 = "w9", "W-9"
        AGREEMENT = "agreement", "Service agreement"
        INSURANCE = "insurance", "Certificate of insurance"
        CONTRACT = "contract", "Contract / SOW"
        TAX = "tax", "Tax exemption"
        OTHER = "other", "Other"

    organization = models.ForeignKey("customers.CustomerOrganization", on_delete=models.PROTECT, related_name="documents")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.OTHER)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="documents/", storage=PrivateOnboardingStorage(), blank=True,
                            validators=[FileExtensionValidator(("pdf", "png", "jpg", "jpeg", "doc", "docx", "xls", "xlsx", "txt"))])
    file_name = models.CharField(max_length=200, blank=True)
    onboarding = models.ForeignKey(CustomerOnboarding, null=True, blank=True, on_delete=models.SET_NULL, related_name="documents",
                                   help_text="Set for the agreement signed in the onboarding form.")
    expires_on = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("kind", "-uploaded_at")
        default_permissions = ()

    def __str__(self):
        return self.title
