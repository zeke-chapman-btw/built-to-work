import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.core.models import TimestampedModel, UUIDModel


class ActivityType(models.TextChoices):
    CALL = "call", "Phone call"
    CONVERSATION = "conversation", "Conversation"
    ATTEMPT = "attempt", "Attempted contact"
    VOICEMAIL = "voicemail", "Voicemail"
    EMAIL = "email", "Email"
    SMS = "sms", "Text message"
    MEETING = "meeting", "In-person meeting"
    VIDEO = "video", "Video meeting"
    INTERVIEW = "interview", "Interview"
    EVENT = "event", "Event interaction"
    RECRUITING = "recruiting", "Recruiting discussion"
    CUSTOMER_MEETING = "customer_meeting", "Customer meeting"
    FEEDBACK = "feedback", "Candidate feedback / response"
    FOLLOW_UP = "follow_up", "Follow-up"
    NOTE = "note", "General note"
    OTHER = "other", "Other interaction"


class Interaction(UUIDModel, TimestampedModel):
    """An attributed observation, never a messaging instruction or hiring decision.

    BTW and employer histories have distinct ownership and service/query guards.
    Associations and provenance are immutable after creation; corrections retain
    the original author and an append-only revision snapshot.
    """

    class Audience(models.TextChoices):
        BTW = "btw", "BTW Interactions"
        EMPLOYER = "employer", "Employer Interactions"

    audience = models.CharField(max_length=12, choices=Audience.choices)
    owner_organization = models.ForeignKey("customers.CustomerOrganization", null=True, blank=True,
        on_delete=models.PROTECT, related_name="owned_interactions", editable=False)
    participant = models.ForeignKey("participants.Participant", null=True, blank=True,
        on_delete=models.PROTECT, related_name="crm_interactions", editable=False)
    organization = models.ForeignKey("customers.CustomerOrganization", null=True, blank=True,
        on_delete=models.PROTECT, related_name="relationship_interactions", editable=False)
    contact = models.ForeignKey("customers.CustomerContact", null=True, blank=True,
        on_delete=models.PROTECT, related_name="crm_interactions", editable=False)
    engagement = models.ForeignKey("recruiting.RecruitingOpportunity", null=True, blank=True,
        on_delete=models.PROTECT, related_name="crm_interactions", editable=False)
    candidate = models.ForeignKey("recruiting.CandidateOpportunity", null=True, blank=True,
        on_delete=models.PROTECT, related_name="crm_interactions", editable=False)
    workforce_request = models.ForeignKey("recruiting.WorkforceRequest", null=True, blank=True,
        on_delete=models.PROTECT, related_name="crm_interactions", editable=False)
    need = models.ForeignKey("customers.WorkforceNeed", null=True, blank=True,
        on_delete=models.PROTECT, related_name="crm_interactions", editable=False)
    campaign = models.ForeignKey("customers.Campaign", null=True, blank=True,
        on_delete=models.PROTECT, related_name="crm_interactions", editable=False)
    disclosure = models.ForeignKey("recruiting.CandidateDisclosure", null=True, blank=True,
        on_delete=models.PROTECT, related_name="crm_interactions", editable=False)
    membership = models.ForeignKey("customers.CustomerMembership", null=True, blank=True,
        on_delete=models.PROTECT, related_name="crm_interactions", editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="created_interactions", editable=False)
    responsible_staff = models.ForeignKey("staffing.StaffProfile", null=True, blank=True,
        on_delete=models.PROTECT, related_name="responsible_interactions")
    activity_type = models.CharField(max_length=24, choices=ActivityType.choices)
    direction = models.CharField(max_length=10, blank=True,
        choices=[("", "Not specified"), ("inbound", "Inbound"), ("outbound", "Outbound")])
    summary = models.CharField(max_length=240)
    notes = models.TextField(blank=True, max_length=6000)
    outcome = models.CharField(max_length=240, blank=True)
    occurred_at = models.DateTimeField(db_index=True)
    next_step = models.CharField(max_length=500, blank=True)
    follow_up_on = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=16, default="manual", editable=False,
        choices=[("manual", "Manually recorded"), ("provider", "Provider-confirmed")])
    provider = models.CharField(max_length=80, blank=True, editable=False)
    external_reference = models.CharField(max_length=240, blank=True, editable=False)
    delivery_status = models.CharField(max_length=80, blank=True, editable=False)
    operation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    payload_fingerprint = models.CharField(max_length=64, editable=False)
    revision = models.PositiveIntegerField(default=1, editable=False)
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    archived_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name="archived_interactions", editable=False)

    IMMUTABLE = ("audience", "owner_organization_id", "participant_id", "organization_id", "contact_id",
        "engagement_id", "candidate_id", "workforce_request_id", "need_id", "campaign_id", "disclosure_id",
        "membership_id", "created_by_id", "created_at", "source", "provider", "external_reference",
        "delivery_status", "operation_id", "payload_fingerprint")

    class Meta:
        default_permissions = ()  # Reuse existing domain authority, not another role system.
        ordering = ("-occurred_at", "-created_at", "-pk")
        indexes = [models.Index(fields=("audience", "participant", "-occurred_at"), name="crm_person_history"),
                   models.Index(fields=("audience", "owner_organization", "-occurred_at"), name="crm_employer_history")]
        constraints = [
            models.CheckConstraint(condition=Q(participant__isnull=False) | Q(organization__isnull=False), name="crm_context_required"),
            models.CheckConstraint(condition=(Q(audience="btw", owner_organization__isnull=True,
                membership__isnull=True, disclosure__isnull=True) | Q(audience="employer",
                owner_organization__isnull=False, organization__isnull=False,
                organization=F("owner_organization"), participant__isnull=False,
                membership__isnull=False, disclosure__isnull=False, engagement__isnull=False,
                candidate__isnull=False, need__isnull=False, responsible_staff__isnull=True)), name="crm_owner_boundary"),
            models.CheckConstraint(condition=(Q(source="manual", provider="", external_reference="", delivery_status="") |
                (Q(source="provider") & ~Q(provider="") & ~Q(external_reference=""))), name="crm_provenance_boundary"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="crm_revision_positive"),
            models.CheckConstraint(condition=~Q(summary=""), name="crm_summary_required"),
        ]

    def clean(self):
        if self.occurred_at and (timezone.is_naive(self.occurred_at) or self.occurred_at > timezone.now()):
            raise ValidationError({"occurred_at": "Record an actual interaction time, not a future or unzoned time."})
        if self.contact_id and self.contact.organization_id != self.organization_id:
            raise ValidationError("Choose a contact from the associated company.")
        for key in ("need", "campaign", "workforce_request"):
            item = getattr(self, key)
            if item and item.organization_id != self.organization_id:
                raise ValidationError("All relationship associations must belong to the same company.")
        if self.engagement_id and self.engagement.need_id != self.need_id:
            raise ValidationError("The recruiting engagement must match the associated Workforce Need.")
        if self.candidate_id and (self.candidate.participant_id != self.participant_id or
                                  self.candidate.opportunity_id != self.engagement_id):
            raise ValidationError("The candidate must match the participant and recruiting engagement.")
        if self.disclosure_id:
            grant = self.disclosure
            if (grant.participant_id != self.participant_id or grant.organization_id != self.owner_organization_id or
                    grant.candidate_id != self.candidate_id or grant.need_id != self.need_id or
                    self.membership.organization_id != self.owner_organization_id or
                    self.membership.identity.user_id != self.created_by_id):
                raise ValidationError("Employer history must retain its exact disclosed candidate and author context.")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            if any(getattr(old, key) != getattr(self, key) for key in self.IMMUTABLE):
                raise ValidationError("Ownership, original author, associations and provenance cannot be rewritten.")
        return super().save(*args, **kwargs)


class InteractionRevision(UUIDModel):
    interaction = models.ForeignKey(Interaction, on_delete=models.PROTECT, related_name="history")
    number = models.PositiveIntegerField()
    action = models.CharField(max_length=24, choices=[("created", "Created"), ("corrected", "Corrected"),
        ("archived", "Archived"), ("restored", "Restored"), ("task_linked", "Task linked")])
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="interaction_revisions")
    recorded_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=500)
    snapshot = models.JSONField()

    class Meta:
        default_permissions = ()
        ordering = ("number",)
        constraints = [models.UniqueConstraint(fields=("interaction", "number"), name="crm_unique_revision")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Interaction revision history is append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Retain interaction revision history.")


class InteractionTaskLink(UUIDModel, TimestampedModel):
    interaction = models.ForeignKey(Interaction, on_delete=models.PROTECT, related_name="task_links")
    task = models.ForeignKey("operations.Task", on_delete=models.PROTECT, related_name="crm_links")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="crm_task_links")
    payload_fingerprint = models.CharField(max_length=64, blank=True)

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=("interaction", "task"), name="crm_unique_task_link")]


class ContactRelationship(UUIDModel, TimestampedModel):
    """Optional relationship metadata, not another contact or portal identity."""

    contact = models.OneToOneField("customers.CustomerContact", on_delete=models.PROTECT, related_name="crm_relationship")
    organization = models.ForeignKey("customers.CustomerOrganization", on_delete=models.PROTECT, related_name="crm_contacts")
    role = models.CharField(max_length=160, blank=True)
    PREFERENCES = [("unknown", "Not recorded"), ("preferred", "Preferred contact method"), ("avoid", "Do not contact by this method")]
    email_preference = models.CharField(max_length=12, choices=PREFERENCES, default="unknown")
    sms_preference = models.CharField(max_length=12, choices=PREFERENCES, default="unknown")
    phone_preference = models.CharField(max_length=12, choices=PREFERENCES, default="unknown")
    preference_notes = models.CharField(max_length=1000, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        default_permissions = ()

    @property
    def is_primary(self):
        return self.organization.primary_contact_id == self.contact_id

    def clean(self):
        if self.contact.organization_id != self.organization_id:
            raise ValidationError("Contact relationship settings must use the contact's company.")
