"""Customer domain, explicit customer identity and organization-scoped collaboration."""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q, F
from django.db.models.functions import Lower

from apps.core.models import UUIDModel, TimestampedModel


class VersionedRecord(UUIDModel, TimestampedModel):
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True


class CustomerOrganization(VersionedRecord):
    class Status(models.TextChoices):
        PROSPECT = "prospect", "Prospect"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=240, blank=True)
    industry = models.CharField(max_length=160, blank=True)
    website = models.URLField(blank=True)
    business_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROSPECT)
    archived_from_status = models.CharField(max_length=16, blank=True, editable=False)
    provisioned = models.BooleanField(default=False, editable=False)
    primary_lead = models.ForeignKey("CustomerMembership", on_delete=models.PROTECT, null=True,
                                    blank=True, related_name="primary_for", editable=False)
    primary_contact = models.ForeignKey("CustomerContact", on_delete=models.PROTECT, null=True,
                                       blank=True, related_name="primary_for")

    class Meta:
        ordering = ["name", "pk"]
        permissions = [("manage_customers", "Manage internal customer organizations")]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=["prospect", "active", "inactive", "archived"]), name="customer_org_status_valid"),
            models.CheckConstraint(condition=(Q(provisioned=False, primary_lead__isnull=True) | Q(provisioned=True, primary_lead__isnull=False)), name="customer_primary_provisioned"),
        ]

    def __str__(self):
        return self.name


class CustomerLocation(VersionedRecord):
    organization = models.ForeignKey(CustomerOrganization, on_delete=models.PROTECT, related_name="locations")
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=20, choices=[("headquarters", "Headquarters"), ("branch", "Branch"), ("site", "Work site"), ("other", "Other")], default="site")
    address_line1 = models.CharField(max_length=240, blank=True)
    address_line2 = models.CharField(max_length=240, blank=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    postal_code = models.CharField(max_length=30, blank=True)
    country = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [models.UniqueConstraint(fields=["organization", "id"], name="customer_location_org_id")]

    def __str__(self):
        return self.name


class CustomerContact(VersionedRecord):
    organization = models.ForeignKey(CustomerOrganization, on_delete=models.PROTECT, related_name="contacts")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=160, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=60, blank=True)
    responsibilities = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["first_name", "last_name", "pk"]
        constraints = [models.UniqueConstraint(fields=["organization", "id"], name="customer_contact_org_id")]

    def __str__(self):
        return " ".join(x for x in [self.first_name, self.last_name] if x)


class CustomerRelationship(VersionedRecord):
    source = models.ForeignKey(CustomerOrganization, on_delete=models.PROTECT, related_name="outgoing_relationships")
    target = models.ForeignKey(CustomerOrganization, on_delete=models.PROTECT, related_name="incoming_relationships")
    kind = models.CharField(max_length=16, choices=[("parent", "Parent → subsidiary"), ("division", "Parent → division"), ("connected", "Connected organization")])
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=~Q(source=F("target")), name="customer_relation_not_self"),
            models.CheckConstraint(condition=Q(kind__in=["parent", "division", "connected"]), name="customer_relation_kind_valid"),
            models.UniqueConstraint(fields=["source", "target", "kind"], name="customer_relation_unique"),
        ]


class CustomerIdentity(VersionedRecord):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="customer_identity", editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=60, blank=True)
    title = models.CharField(max_length=160, blank=True)

    activated_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["first_name", "last_name", "email", "pk"]
        constraints = [
            models.UniqueConstraint(Lower("email"), name="customer_identity_email_ci"),
            models.CheckConstraint(condition=Q(email=Lower("email")) & ~Q(email=""), name="customer_identity_email_normal"),
        ]

    def clean(self):
        self.email = self.email.strip().casefold()
        if self.user_id:
            from apps.participants.models import ParticipantAccount
            from apps.staffing.models import StaffProfile
            from apps.accounts.models import StaffInvitation
            u = self.user
            if (u.is_staff or u.is_superuser or u.email
                    or ((self._state.adding or self.activated_at is None) and (u.is_active or u.has_usable_password()))
                    or u.groups.exists() or u.user_permissions.exists()
                    or ParticipantAccount.objects.filter(user=u).exists()
                    or StaffProfile.objects.filter(user=u).exists()
                    or StaffInvitation.objects.filter(user=u).exists()):
                raise ValidationError("Customer identities must remain separate from staff and participant accounts; activation requires a verified invitation.")

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip() + f" · {self.email}"


class CustomerMembership(VersionedRecord):
    class Role(models.TextChoices):
        LEAD = "lead", "Lead"
        MEMBER = "member", "Member"

    class State(models.TextChoices):
        ENABLED = "enabled", "Prepared / enabled"
        SUSPENDED = "suspended", "Suspended"
        REMOVED = "removed", "Removed"

    organization = models.ForeignKey(CustomerOrganization, on_delete=models.PROTECT, related_name="memberships")
    identity = models.ForeignKey(CustomerIdentity, on_delete=models.PROTECT, related_name="memberships")
    contact = models.ForeignKey(CustomerContact, on_delete=models.PROTECT, null=True, blank=True, related_name="memberships")
    role = models.CharField(max_length=8, choices=Role.choices)
    state = models.CharField(max_length=12, choices=State.choices, default=State.ENABLED)

    accepted_at = models.DateTimeField(null=True, blank=True, editable=False)
    manage_needs = models.BooleanField(default=False)
    manage_campaigns = models.BooleanField(default=False)

    class Meta:
        ordering = ["identity__first_name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "identity"], name="customer_membership_unique"),
            models.UniqueConstraint(fields=["organization", "id"], name="customer_membership_org_id"),
            models.CheckConstraint(condition=Q(role__in=["lead", "member"]), name="customer_member_role_valid"),
            models.CheckConstraint(condition=Q(state__in=["enabled", "suspended", "removed"]), name="customer_member_state_valid"),
        ]

    def clean(self):
        if self.contact_id and self.contact.organization_id != self.organization_id:
            raise ValidationError({"contact": "Choose a contact from this organization."})

    def __str__(self):
        return str(self.identity)


class CustomerAccessPlan(VersionedRecord):
    organization = models.OneToOneField(CustomerOrganization, on_delete=models.PROTECT, related_name="access_plan")
    lead_limit = models.PositiveIntegerField(null=True, blank=True)
    member_limit = models.PositiveIntegerField(null=True, blank=True)
    workforce_need_creation = models.BooleanField(null=True, blank=True, default=None)
    candidate_matching = models.BooleanField(null=True, blank=True, default=None)
    directory_search = models.BooleanField(null=True, blank=True, default=None)
    expanded_candidate_profile = models.BooleanField(null=True, blank=True, default=None)
    authorized_candidate_contact = models.BooleanField(null=True, blank=True, default=None)


class Campaign(VersionedRecord):
    class Status(models.TextChoices):
        PLANNING = "planning", "Planning"
        ACTIVE = "active", "Active"
        HOLD = "hold", "On Hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey(CustomerOrganization, on_delete=models.PROTECT, related_name="campaigns")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    objectives = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    primary_contact = models.ForeignKey(CustomerContact, on_delete=models.PROTECT, null=True, blank=True, related_name="campaigns")
    contact_name = models.CharField(max_length=220, blank=True, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PLANNING)
    planning_version = models.PositiveIntegerField(default=0, editable=False)
    approved_planning_version = models.PositiveIntegerField(null=True, blank=True, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="customer_campaign_approvals", editable=False)
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)

    review_state = models.CharField(max_length=12, choices=[("draft", "Draft"), ("submitted", "Submitted"), ("review", "In review"), ("returned", "Changes requested"), ("approved", "Approved")], default="draft", editable=False)
    submitted_planning_version = models.PositiveIntegerField(null=True, blank=True, editable=False)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="customer_campaign_submissions", editable=False)
    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-updated_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "id"], name="customer_campaign_org_id"),
            models.CheckConstraint(condition=Q(status__in=["planning", "active", "hold", "completed", "cancelled"]), name="customer_campaign_status_valid"),
            models.CheckConstraint(condition=(~Q(status="active") | Q(approved_planning_version__isnull=False, approved_planning_version=F("planning_version"), approved_by__isnull=False, approved_at__isnull=False)), name="customer_active_campaign_approved"),
            models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(start_date__isnull=True) | Q(end_date__gte=F("start_date")), name="customer_campaign_dates_valid"),
        ]

    def clean(self):
        errors = {}
        if self.primary_contact_id and self.primary_contact.organization_id != self.organization_id:
            errors["primary_contact"] = "Choose a contact from this organization."
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "End date cannot precede the start date."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


ANSWERS = [("provided", "Provided"), ("none", "None"), ("not_applicable", "Not applicable")]
YES_NO = [("yes", "Yes"), ("no", "No"), ("not_applicable", "Not applicable")]


class HiringTerms(models.Model):
    """Typed hiring payload; unanswered is distinct from explicit None / N/A."""
    title = models.CharField(max_length=200, blank=True)
    trade = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    worker_quantity = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    employment_type = models.CharField(max_length=20, blank=True, choices=[("full_time", "Full time"), ("part_time", "Part time"), ("temporary", "Temporary"), ("contract", "Contract"), ("seasonal", "Seasonal"), ("apprenticeship", "Apprenticeship"), ("internship", "Internship"), ("other", "Other")])
    employment_details = models.TextField(blank=True)
    location_mode = models.CharField(max_length=12, blank=True, choices=[("location", "Customer location"), ("custom", "Other work location"), ("remote", "Remote")])
    location = models.ForeignKey(CustomerLocation, on_delete=models.PROTECT, null=True, blank=True, related_name="hiring_revisions")
    location_name = models.CharField(max_length=200, blank=True)
    address_line1 = models.CharField(max_length=240, blank=True)
    address_line2 = models.CharField(max_length=240, blank=True)
    city = models.CharField(max_length=120, blank=True)
    location_state = models.CharField(max_length=120, blank=True)
    postal_code = models.CharField(max_length=30, blank=True)
    country = models.CharField(max_length=80, blank=True)
    location_details = models.TextField(blank=True)
    pay_response = models.CharField(max_length=16, blank=True, choices=[("provided", "Rate / range provided"), ("not_applicable", "Not applicable — explain")])
    pay_minimum = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    pay_maximum = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    pay_currency = models.CharField(max_length=3, blank=True)
    pay_basis = models.CharField(max_length=12, blank=True, choices=[("hour", "Hourly"), ("day", "Daily"), ("week", "Weekly"), ("month", "Monthly"), ("year", "Annual"), ("project", "Per project"), ("other", "Other")])
    pay_details = models.TextField(blank=True)
    overtime_response = models.CharField(max_length=16, blank=True, choices=YES_NO)
    overtime_details = models.TextField(blank=True)
    benefits_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    benefits_details = models.TextField(blank=True)
    per_diem_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    per_diem_details = models.TextField(blank=True)
    per_diem_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    shift = models.CharField(max_length=16, blank=True, choices=[("day", "Day"), ("evening", "Evening"), ("night", "Night"), ("rotating", "Rotating"), ("flexible", "Flexible"), ("other", "Other")])
    schedule_details = models.TextField(blank=True)
    hours_mode = models.CharField(max_length=12, blank=True, choices=[("specified", "Weekly hours specified"), ("variable", "Variable — explain")])
    hours_per_week = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(Decimal("168"))])
    start_mode = models.CharField(max_length=12, blank=True, choices=[("date", "Specific date"), ("timeframe", "Timeframe")])
    start_date = models.DateField(null=True, blank=True)
    start_timeframe = models.CharField(max_length=240, blank=True)
    end_date = models.DateField(null=True, blank=True)
    duration_response = models.CharField(max_length=16, blank=True, choices=[("provided", "Duration provided"), ("ongoing", "Ongoing"), ("not_applicable", "Not applicable")])
    duration_details = models.TextField(blank=True)
    required_skills_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    required_skills_details = models.TextField(blank=True)
    preferred_skills_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    preferred_skills_details = models.TextField(blank=True)
    required_experience_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    required_experience_details = models.TextField(blank=True)
    required_experience_years = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    preferred_experience_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    preferred_experience_details = models.TextField(blank=True)
    preferred_experience_years = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    required_credentials_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    required_credentials_details = models.TextField(blank=True)
    preferred_credentials_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    preferred_credentials_details = models.TextField(blank=True)
    travel_response = models.CharField(max_length=16, blank=True, choices=YES_NO)
    travel_details = models.TextField(blank=True)
    overnight_response = models.CharField(max_length=16, blank=True, choices=YES_NO)
    overnight_details = models.TextField(blank=True)
    conditions_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    conditions_details = models.TextField(blank=True)
    equipment_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    equipment_details = models.TextField(blank=True)
    primary_contact = models.ForeignKey(CustomerContact, on_delete=models.PROTECT, null=True, blank=True, related_name="hiring_revisions")
    contact_name = models.CharField(max_length=220, blank=True, editable=False)
    contact_email = models.EmailField(blank=True, editable=False)
    contact_phone = models.CharField(max_length=60, blank=True, editable=False)
    screening_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    screening_details = models.TextField(blank=True)
    other_response = models.CharField(max_length=16, blank=True, choices=ANSWERS)
    other_details = models.TextField(blank=True)
    hiring_timeline = models.TextField(blank=True)
    hiring_deadline = models.DateField(null=True, blank=True)

    class Meta:
        abstract = True

    def clean(self):
        errors = {}
        if self.pay_minimum is not None and self.pay_maximum is not None and self.pay_maximum < self.pay_minimum:
            errors["pay_maximum"] = "Maximum pay cannot be below minimum pay."
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "End date cannot precede the start date."
        if self.pay_currency and (len(self.pay_currency) != 3 or not self.pay_currency.isalpha()):
            errors["pay_currency"] = "Use a three-letter currency code."
        for field in ("location", "primary_contact"):
            obj = getattr(self, field)
            if obj and obj.organization_id != self.organization_id:
                errors[field] = "Choose a record from this organization."
        if errors:
            raise ValidationError(errors)


class WorkforceNeed(VersionedRecord):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted for Review"
        REVIEW = "review", "Under BTW Review"
        ACTIVE = "active", "Approved / Active"
        HOLD = "hold", "On Hold"
        CLOSED = "closed", "Closed / Completed"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey(CustomerOrganization, on_delete=models.PROTECT, related_name="workforce_needs")
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, null=True, blank=True, related_name="workforce_needs")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    internal_reference = models.CharField(max_length=160, blank=True)
    internal_note = models.TextField(blank=True)
    effective_revision = models.ForeignKey("WorkforceNeedRevision", on_delete=models.PROTECT, null=True, blank=True, related_name="effective_for", editable=False)
    working_revision = models.ForeignKey("WorkforceNeedRevision", on_delete=models.PROTECT, null=True, blank=True, related_name="working_for", editable=False)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="customer_need_closures", editable=False)
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    closure_reason = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ["-updated_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "id"], name="customer_need_org_id"),
            models.CheckConstraint(condition=Q(status__in=["draft", "submitted", "review", "active", "hold", "closed", "cancelled"]), name="customer_need_status_valid"),
            models.CheckConstraint(condition=~Q(status__in=["active", "hold", "closed"]) | Q(effective_revision__isnull=False), name="customer_effective_need_required"),
            models.CheckConstraint(condition=~Q(status="closed") | (Q(closed_by__isnull=False, closed_at__isnull=False) & ~Q(closure_reason="")), name="customer_need_closure_recorded"),
        ]

    @property
    def display_revision(self):
        return self.effective_revision or self.working_revision

    @property
    def title(self):
        return (self.display_revision.title if self.display_revision else "") or "Untitled Workforce Need"

    def __str__(self):
        return self.title


class WorkforceNeedRevision(HiringTerms, UUIDModel, TimestampedModel):
    class State(models.TextChoices):
        DRAFT = "draft", "Working draft"
        SUBMITTED = "submitted", "Submitted for Review"
        REVIEW = "review", "Under BTW Review"
        APPROVED = "approved", "Approved"
        RETURNED = "returned", "Returned for correction"
        WITHDRAWN = "withdrawn", "Withdrawn"

    organization = models.ForeignKey(CustomerOrganization, on_delete=models.PROTECT, related_name="hiring_revisions")
    need = models.ForeignKey(WorkforceNeed, on_delete=models.PROTECT, related_name="revisions")
    number = models.PositiveIntegerField()
    state = models.CharField(max_length=12, choices=State.choices, default=State.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="customer_need_submissions", editable=False)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="customer_need_reviews", editable=False)
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="customer_need_approvals", editable=False)
    decision_reason = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ["-number", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["need", "number"], name="customer_revision_number_unique"),
            models.UniqueConstraint(fields=["need", "id"], name="customer_revision_need_id"),
            models.CheckConstraint(condition=Q(number__gte=1), name="customer_revision_positive"),
            models.CheckConstraint(condition=Q(state__in=["draft", "submitted", "review", "approved", "returned", "withdrawn"]), name="customer_revision_state_valid"),
            models.CheckConstraint(condition=Q(worker_quantity__isnull=True) | Q(worker_quantity__gte=1), name="customer_need_quantity_valid"),
            models.CheckConstraint(condition=Q(pay_minimum__isnull=True) | Q(pay_minimum__gte=0), name="customer_pay_minimum_valid"),
            models.CheckConstraint(condition=Q(pay_maximum__isnull=True) | Q(pay_maximum__gte=0), name="customer_pay_maximum_valid"),
            models.CheckConstraint(condition=Q(pay_minimum__isnull=True) | Q(pay_maximum__isnull=True) | Q(pay_maximum__gte=F("pay_minimum")), name="customer_pay_range_valid"),
            models.CheckConstraint(condition=Q(per_diem_amount__isnull=True) | Q(per_diem_amount__gte=0), name="customer_per_diem_valid"),
            models.CheckConstraint(condition=Q(hours_per_week__isnull=True) | Q(hours_per_week__gt=0, hours_per_week__lte=168), name="customer_hours_valid"),
            models.CheckConstraint(condition=Q(required_experience_years__isnull=True) | Q(required_experience_years__gte=0), name="customer_required_years_valid"),
            models.CheckConstraint(condition=Q(preferred_experience_years__isnull=True) | Q(preferred_experience_years__gte=0), name="customer_preferred_years_valid"),
            models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(start_date__isnull=True) | Q(end_date__gte=F("start_date")), name="customer_hiring_dates_valid"),
            models.CheckConstraint(condition=~Q(state="approved") | Q(approved_by__isnull=False, approved_at__isnull=False), name="customer_revision_approval_recorded"),
            models.CheckConstraint(condition=~Q(state__in=["submitted", "review", "approved", "returned"]) | Q(submitted_at__isnull=False, submitted_by__isnull=False), name="customer_submission_recorded"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            if old.state != self.State.DRAFT and any(getattr(old, x) != getattr(self, x) for x in TERM_FIELD_NAMES):
                raise ValidationError("Submitted hiring details are immutable; create a new revision.")
            if old.state in {self.State.APPROVED, self.State.RETURNED, self.State.WITHDRAWN}:
                immutable = TERM_FIELD_NAMES + ("state", "number", "need_id", "organization_id", "submitted_at", "submitted_by_id", "reviewed_by_id", "approved_at", "approved_by_id", "decision_reason")
                if any(getattr(old, x) != getattr(self, x) for x in immutable):
                    raise ValidationError("Retain historical revisions unchanged.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.state != self.State.DRAFT:
            raise ValidationError("Retain submitted revision history.")
        return super().delete(*args, **kwargs)


TERM_FIELD_NAMES = tuple(f.attname for f in HiringTerms._meta.fields)
EDITABLE_TERM_FIELDS = tuple(f.name for f in HiringTerms._meta.fields if f.editable)


class CustomerAuditScope(models.Model):
    """An indexed scope for the existing AuditLog, never a competing audit payload."""
    organization = models.ForeignKey(CustomerOrganization, on_delete=models.PROTECT, related_name="audit_scopes")
    audit = models.ForeignKey("core.AuditLog", on_delete=models.PROTECT, related_name="customer_scopes")
    workforce_need = models.ForeignKey(WorkforceNeed, on_delete=models.PROTECT, null=True, blank=True, related_name="audit_scopes")
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, null=True, blank=True, related_name="audit_scopes")

    @property
    def action_label(self):
        return self.audit.action.removeprefix("customer.").replace(".", " · ").replace("_", " ").capitalize()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "audit"], name="customer_audit_scope_unique")]
        indexes = [models.Index(fields=["organization", "audit"], name="customer_history_scope_idx")]


class CustomerAccessToken(UUIDModel, TimestampedModel):
    """Only a digest is stored. Reissuing revokes all older links of that scope."""
    identity = models.ForeignKey(CustomerIdentity, on_delete=models.PROTECT, related_name="access_tokens")
    membership = models.ForeignKey(CustomerMembership, on_delete=models.PROTECT, null=True, blank=True, related_name="invitations")
    purpose = models.CharField(max_length=12, choices=[("invite", "Invitation"), ("reset", "Recovery")])
    token_digest = models.CharField(max_length=64, unique=True, editable=False)
    email = models.EmailField(editable=False)
    security_revision = models.PositiveIntegerField(editable=False)
    expires_at = models.DateTimeField(editable=False)
    consumed_at = models.DateTimeField(null=True, blank=True, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True, editable=False)
    delivery = models.CharField(max_length=12, choices=[("pending", "Pending"), ("sent", "Transport accepted"), ("captured", "Test capture only"), ("failed", "Delivery failed")], default="pending", editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(purpose="invite", membership__isnull=False) | Q(purpose="reset", membership__isnull=True), name="customer_token_purpose_scope"),
            models.CheckConstraint(condition=Q(consumed_at__isnull=True) | Q(revoked_at__isnull=True), name="customer_token_single_outcome"),
            models.CheckConstraint(condition=Q(delivery__in=["pending", "sent", "captured", "failed"]), name="customer_delivery_valid"),
        ]


class CustomerFeedback(UUIDModel, TimestampedModel):
    """Deliberately published customer text, never copied from private audit reasons."""
    organization = models.ForeignKey(CustomerOrganization, on_delete=models.PROTECT, related_name="feedback")
    workforce_need = models.ForeignKey(WorkforceNeed, on_delete=models.PROTECT, null=True, blank=True, related_name="feedback")
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, null=True, blank=True, related_name="feedback")
    body = models.TextField(max_length=4000)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="customer_feedback")
    audit = models.OneToOneField("core.AuditLog", on_delete=models.PROTECT, related_name="published_customer_feedback")

    class Meta:
        ordering = ["-created_at", "pk"]
        constraints = [models.CheckConstraint(condition=Q(workforce_need__isnull=False, campaign__isnull=True) | Q(workforce_need__isnull=True, campaign__isnull=False), name="customer_feedback_one_subject")]
