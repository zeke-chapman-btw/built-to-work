"""Participant-owned extensions; canonical identity and domain evidence stay authoritative."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, F
from apps.core.models import TimestampedModel, ArchivableModel


class ParticipantAcquisition(TimestampedModel):
    class Channel(models.TextChoices):
        UNKNOWN = "unknown", "Unknown / unverified"
        EVENT = "event", "Event"
        ONLINE = "online", "Online"

    participant = models.OneToOneField("participants.Participant", on_delete=models.PROTECT, related_name="acquisition")
    channel = models.CharField(max_length=12, choices=Channel.choices, default=Channel.UNKNOWN, db_index=True)
    acquired_at = models.DateTimeField(null=True, blank=True, db_index=True)
    event = models.ForeignKey("events.Event", null=True, blank=True, on_delete=models.PROTECT, related_name="original_participant_acquisitions")
    online_source = models.CharField(max_length=240, blank=True)
    source_submission = models.ForeignKey("participants.RegistrationSubmission", null=True, blank=True, on_delete=models.PROTECT, related_name="original_acquisitions")
    provenance = models.CharField(max_length=240, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="participant_acquisitions_recorded")
    correction_reason = models.CharField(max_length=1000, blank=True)

    class Meta:
        permissions = [("manage_participant_acquisition", "Can correct original participant acquisition with attribution")]
        constraints = [
            models.CheckConstraint(condition=Q(channel__in=("unknown", "event", "online")), name="participant_acq_valid_channel"),
            models.CheckConstraint(condition=Q(channel="event", event__isnull=False) | Q(channel__in=("unknown", "online"), event__isnull=True), name="participant_acq_event_matches"),
            models.CheckConstraint(condition=~Q(channel="unknown") | Q(acquired_at__isnull=True, online_source=""), name="participant_acq_unknown_explicit"),
            models.CheckConstraint(condition=Q(channel="online") | Q(online_source=""), name="participant_acq_online_source"),
        ]

    def clean(self):
        from django.utils import timezone
        super().clean()
        if self.acquired_at and self.acquired_at > timezone.now():
            raise ValidationError({"acquired_at": "Original acquisition cannot be in the future."})
        if self.source_submission_id and self.source_submission.participant_id != self.participant_id:
            raise ValidationError({"source_submission": "The intake reference must belong to this participant."})
        if self.channel == "event" and not self.event_id:
            raise ValidationError({"event": "Choose the original acquisition event."})
        if self.channel != "event" and self.event_id:
            raise ValidationError({"event": "An original event belongs only to Event acquisition."})
        if self.channel != "online" and self.online_source:
            raise ValidationError({"online_source": "An online source belongs only to Online acquisition."})
        if self.channel == "unknown" and self.acquired_at:
            raise ValidationError({"acquired_at": "Leave the date unknown when original acquisition is unknown."})


class ParticipantWorkExperience(TimestampedModel, ArchivableModel):
    participant = models.ForeignKey("participants.Participant", on_delete=models.PROTECT, related_name="work_experiences")
    employer = models.CharField(max_length=200)
    role = models.CharField(max_length=200)
    location = models.CharField(max_length=200, blank=True)
    started_on = models.DateField(null=True, blank=True)
    ended_on = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=False)
    description = models.TextField(max_length=3000, blank=True)
    source = models.CharField(max_length=24, default="staff", editable=False)
    source_reference = models.CharField(max_length=240, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="participant_work_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="participant_work_updated")

    class Meta:
        ordering = ("-is_current", "-started_on", "-pk")
        constraints = [
            models.CheckConstraint(condition=Q(ended_on__isnull=True) | Q(started_on__isnull=True) | Q(ended_on__gte=F("started_on")), name="participant_work_dates_ordered"),
            models.CheckConstraint(condition=Q(is_current=False) | Q(ended_on__isnull=True), name="participant_current_work_no_end"),
        ]

    def clean(self):
        super().clean()
        if self.started_on and self.ended_on and self.ended_on < self.started_on:
            raise ValidationError({"ended_on": "The end date cannot precede the start date."})
        if self.is_current and self.ended_on:
            raise ValidationError({"ended_on": "Current employment must not have an end date."})

    def __str__(self):
        return f"{self.role} · {self.employer}"


class ParticipantNote(TimestampedModel, ArchivableModel):
    """General staff notes, separate from append-only qualification/review decisions.

    Only BTW-internal notes are representable in Build 11. Build 14 must extend
    this foundation with an authoritative organization FK and membership policy;
    a nullable/free-text customer label must never confer customer authorship.
    """
    participant = models.ForeignKey("participants.Participant", on_delete=models.PROTECT, related_name="staff_notes")
    audience = models.CharField(max_length=20, default="btw_internal", editable=False)
    content = models.TextField(max_length=4000)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="participant_notes_authored")
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="participant_notes_edited")
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [models.CheckConstraint(condition=Q(audience="btw_internal"), name="participant_note_internal_only")]

    def clean(self):
        super().clean()
        if self.audience != "btw_internal":
            raise ValidationError("Customer-specific note creation is unavailable in Build 11.")
        if not self.content.strip():
            raise ValidationError({"content": "Enter an internal note."})
        if self.author_id and (not self.author.is_staff or not self.author.is_active) and not self.pk:
            raise ValidationError("Only an active authorized BTW staff member may author an internal note.")


class SavedParticipantView(TimestampedModel, ArchivableModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="saved_participant_views")
    name = models.CharField(max_length=100)
    criteria = models.JSONField(default=dict)
    schema_version = models.PositiveSmallIntegerField(default=1, editable=False)

    class Meta:
        ordering = ("name", "pk")
        constraints = [models.UniqueConstraint(fields=("owner", "name"), name="participant_saved_view_owner_name")]

    def __str__(self):
        return self.name
