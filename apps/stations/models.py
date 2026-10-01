import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Station(models.Model):
    class Type(models.TextChoices):
        KIOSK = "kiosk", "Kiosk"
        DUCK = "duck", "Duck experience"
        SIMULATOR = "simulator", "Simulator"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    station_type = models.CharField(max_length=20, choices=Type.choices)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=160, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} ({self.code})"


class EventStation(models.Model):
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="station_assignments")
    station = models.ForeignKey(Station, on_delete=models.PROTECT, related_name="event_assignments")
    enabled = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    configuration = models.JSONField(default=dict, blank=True)
    is_active_context = models.BooleanField(default=False)
    assigned_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display_order", "station__name")
        constraints = [
            models.UniqueConstraint(fields=("event", "station"), name="stations_unique_event_station"),
            models.UniqueConstraint(fields=("station",), condition=Q(is_active_context=True), name="stations_one_active_context_per_station"),
        ]

    def clean(self):
        if self.is_active_context and (not self.enabled or not self.station.is_active):
            raise ValidationError("Only an enabled, active station assignment can be active.")

    def __str__(self):
        return f"{self.station} → {self.event}"


class ExperienceSession(models.Model):
    class Mode(models.TextChoices):
        OFFICIAL = "official", "Official participant"
        STAFF_TEST = "staff_test", "Staff test"
        DEMO = "demo", "Demo"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="experience_sessions")
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.OFFICIAL)
    registration = models.OneToOneField("events.EventRegistration", null=True, blank=True, on_delete=models.PROTECT, related_name="experience_session")
    participant = models.ForeignKey("participants.Participant", null=True, blank=True, on_delete=models.PROTECT, related_name="experience_sessions")
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_experience_sessions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-started_at",)
        constraints = [
            models.CheckConstraint(condition=(Q(mode="official", registration__isnull=False, participant__isnull=False) | Q(mode__in=("staff_test", "demo"), registration__isnull=True, participant__isnull=True)), name="stations_session_identity_matches_mode"),
            models.CheckConstraint(condition=Q(completed_at__isnull=True) | Q(completed_at__gte=models.F("started_at")), name="stations_session_completion_after_start"),
        ]

    def clean(self):
        if self.registration_id and (self.registration.event_id != self.event_id or self.registration.participant_id != self.participant_id):
            raise ValidationError("Session registration, participant, and event must agree.")
        if self.mode == self.Mode.OFFICIAL and not (self.registration_id and self.participant_id):
            raise ValidationError("Official sessions require a registration and participant.")
        if self.mode != self.Mode.OFFICIAL and (self.registration_id or self.participant_id):
            raise ValidationError("Test and demo sessions cannot be attached to a participant.")

    def __str__(self):
        return f"{self.get_mode_display()} — {self.event}"


class ExperienceActivity(models.Model):
    class Activity(models.TextChoices):
        KIOSK = "kiosk", "Kiosk"
        DUCK = "duck", "Duck experience"
        SIMULATOR = "simulator", "Simulator"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        SKIPPED = "skipped", "Skipped by staff"

    session = models.ForeignKey(ExperienceSession, on_delete=models.CASCADE, related_name="activities")
    activity = models.CharField(max_length=16, choices=Activity.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    skipped_at = models.DateTimeField(null=True, blank=True)
    skipped_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="skipped_experience_activities")
    skip_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("id",)
        constraints = [models.UniqueConstraint(fields=("session", "activity"), name="stations_unique_session_activity")]

    def clean(self):
        if self.status == self.Status.SKIPPED and (not self.skipped_at or not self.skipped_by_id or not self.skip_reason.strip()):
            raise ValidationError("A skipped activity requires an actor, timestamp, and reason.")
        if self.status != self.Status.SKIPPED and (self.skipped_at or self.skipped_by_id or self.skip_reason):
            raise ValidationError("Skip audit fields are only valid for skipped activities.")

    def __str__(self):
        return f"{self.session}: {self.get_activity_display()} ({self.get_status_display()})"
