"""Additive Build 11B records owned by existing forms and participants."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from apps.core.models import TimestampedModel


class IntakeFormAssignment(TimestampedModel):
    class Channel(models.TextChoices):
        ONLINE = "online", "General online"
        EVENT = "event", "Event-linked online"
        STAFF = "staff", "Staff-assisted"
        TRAILER = "trailer", "Trailer"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.CharField(max_length=12, choices=Channel.choices)
    event = models.ForeignKey("events.Event", null=True, blank=True, on_delete=models.PROTECT)
    trailer = models.ForeignKey("events.TrailerInstance", null=True, blank=True, on_delete=models.PROTECT)
    scope_key = models.CharField(max_length=160, unique=True, editable=False)
    form = models.ForeignKey("participants.RegistrationForm", on_delete=models.PROTECT, related_name="intake_assignments")
    active_version = models.ForeignKey("participants.RegistrationFormVersion", on_delete=models.PROTECT, related_name="intake_assignments")
    is_active = models.BooleanField(default=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    def clean(self):
        if self.channel == self.Channel.ONLINE and (self.event_id or self.trailer_id):
            raise ValidationError("General online assignments cannot name an event or trailer.")
        if self.channel == self.Channel.EVENT and not self.event_id:
            raise ValidationError("An event-linked assignment needs an event.")
        if self.channel == self.Channel.TRAILER and (not self.event_id or not self.trailer_id):
            raise ValidationError("Trailer assignments need an event and trailer.")
        if self.channel != self.Channel.TRAILER and self.trailer_id:
            raise ValidationError("Only a trailer assignment may name a trailer.")
        if self.active_version_id and self.active_version.form_id != self.form_id:
            raise ValidationError("The version must belong to the assigned form.")
        self.scope_key = f"{self.channel}:{self.event_id or 'global'}:{self.trailer_id or 'global'}"


class TrailerIntakeActivation(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trailer = models.ForeignKey("events.TrailerInstance", on_delete=models.PROTECT)
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT)
    active_version = models.ForeignKey("participants.RegistrationFormVersion", on_delete=models.PROTECT, related_name="trailer_activations")
    activated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    last_success_at = models.DateTimeField()
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    available_updates = models.JSONField(default=list, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("trailer", "event"), name="intake_trailer_event_unique")]


class IntakeSession(TimestampedModel):
    class State(models.TextChoices):
        OPEN = "open", "In progress"
        SUBMITTED = "submitted", "Submitted"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    form_version = models.ForeignKey("participants.RegistrationFormVersion", on_delete=models.PROTECT, related_name="intake_sessions")
    contract_hash = models.CharField(max_length=64)
    consent_document = models.ForeignKey("participants.ConsentDocumentVersion", on_delete=models.PROTECT)
    consent_hash = models.CharField(max_length=64)
    event = models.ForeignKey("events.Event", null=True, blank=True, on_delete=models.PROTECT)
    trailer = models.ForeignKey("events.TrailerInstance", null=True, blank=True, on_delete=models.PROTECT)
    channel = models.CharField(max_length=12, choices=IntakeFormAssignment.Channel.choices)
    browser_key = models.CharField(max_length=64)
    answers = models.JSONField(default=dict, blank=True)
    current_step = models.PositiveSmallIntegerField(default=1)
    state = models.CharField(max_length=12, choices=State.choices, default=State.OPEN)
    context = models.JSONField(default=dict, blank=True)
    consent_accepted_at = models.DateTimeField(null=True, blank=True)
    submission = models.OneToOneField("participants.RegistrationSubmission", null=True, blank=True, on_delete=models.PROTECT, related_name="intake_session")
    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(current_step__gte=1, current_step__lte=4), name="intake_session_valid_step")]


class IntakeContactLock(models.Model):
    key = models.CharField(max_length=64, primary_key=True)


class IntakeFieldState(TimestampedModel):
    participant = models.ForeignKey("participants.Participant", on_delete=models.PROTECT, related_name="intake_field_states")
    destination = models.CharField(max_length=100)
    source_submission = models.ForeignKey("participants.RegistrationSubmission", null=True, blank=True, on_delete=models.PROTECT)
    last_value = models.JSONField(null=True, blank=True)
    value_hash = models.CharField(max_length=64)
    is_protected = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("participant", "destination"), name="intake_field_owner_unique"),
                       models.CheckConstraint(condition=models.Q(source_submission__isnull=False) | models.Q(is_protected=True), name="intake_unowned_field_protected")]


class ParticipantHandsOnExperience(TimestampedModel):
    participant = models.ForeignKey("participants.Participant", on_delete=models.PROTECT, related_name="hands_on_experiences")
    source_submission = models.ForeignKey("participants.RegistrationSubmission", on_delete=models.PROTECT, related_name="hands_on_entries")
    entry_id = models.UUIDField()
    area = models.CharField(max_length=100)
    experience_source = models.CharField(max_length=100)
    paid = models.CharField(max_length=6, choices=[("paid", "Paid"), ("unpaid", "Unpaid")])
    duration = models.CharField(max_length=100)
    practiced_skills = models.JSONField(default=list, blank=True)
    proficiency = models.CharField(max_length=100, blank=True)
    description = models.TextField(max_length=1000, blank=True)
    definition_snapshot = models.JSONField(default=dict)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("source_submission", "entry_id"), name="intake_experience_source_unique"),
            models.CheckConstraint(condition=models.Q(paid__in=["paid", "unpaid"]), name="intake_experience_paid_valid")]
