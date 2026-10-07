import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


def default_identifier_prefixes():
    return ["tel"]


def _valid_rectangle(value):
    return (
        isinstance(value, dict)
        and set(value) == {"x", "y", "width", "height"}
        and all(type(value[key]) is int for key in value)
        and value["x"] >= 0 and value["y"] >= 0
        and value["width"] > 0 and value["height"] > 0
    )


class SimulatorCaptureProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    simulator_type = models.CharField(max_length=120)
    expected_width = models.PositiveIntegerField(null=True, blank=True)
    expected_height = models.PositiveIntegerField(null=True, blank=True)
    identifier_region = models.JSONField(default=dict, blank=True)
    score_region = models.JSONField(default=dict, blank=True)
    state_regions = models.JSONField(default=list, blank=True)
    known_identifier_prefixes = models.JSONField(default=default_identifier_prefixes)
    profile_version = models.CharField(max_length=40, default="1")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def clean(self):
        super().clean()
        errors = {}
        for field in ("identifier_region", "score_region"):
            region = getattr(self, field)
            if region and not _valid_rectangle(region):
                errors[field] = "Use x, y, width, and height as non-negative integer pixel coordinates with positive size."
        if not isinstance(self.state_regions, list) or any(not _valid_rectangle(item) for item in self.state_regions):
            errors["state_regions"] = "Each state region must be a calibrated rectangle."
        if not isinstance(self.known_identifier_prefixes, list) or any(
            not isinstance(prefix, str) or not prefix.strip() for prefix in self.known_identifier_prefixes
        ):
            errors["known_identifier_prefixes"] = "Provide a list of nonempty prefixes."
        if bool(self.expected_width) != bool(self.expected_height):
            errors["expected_width"] = "Specify both display dimensions, or leave both blank until calibration."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.name} (v{self.profile_version})"


class EventSimulatorConfiguration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="simulator_configurations")
    station = models.ForeignKey("stations.Station", on_delete=models.PROTECT, related_name="simulator_configurations")
    profile = models.ForeignKey(SimulatorCaptureProfile, on_delete=models.PROTECT, related_name="event_configurations")
    simulator_type = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("event", "station"), name="simulator_event_station_unique")]

    def clean(self):
        super().clean()
        if self.station_id and self.station.station_type != "simulator":
            raise ValidationError({"station": "Choose a Simulator station."})

    def __str__(self):
        return f"{self.event} / {self.station}"


class SimulatorCapture(models.Model):
    class Status(models.TextChoices):
        MATCHED = "matched", "Matched official result"
        NEEDS_REVIEW = "needs_review", "Needs review"
        RESOLVED = "resolved", "Resolved official result"
        TEST = "test", "Simulator test"

    class Mode(models.TextChoices):
        OFFICIAL = "official", "Official"
        STAFF_TEST = "staff_test", "Staff test"
        DEMO = "demo", "Demo"
        UNRESOLVED = "unresolved", "Unresolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission_id = models.UUIDField(unique=True)
    request_fingerprint = models.CharField(max_length=64)
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="simulator_captures")
    station = models.ForeignKey("stations.Station", on_delete=models.PROTECT, related_name="simulator_captures")
    profile = models.ForeignKey(SimulatorCaptureProfile, on_delete=models.PROTECT, related_name="captures")
    profile_version = models.CharField(max_length=40)
    profile_snapshot = models.JSONField(default=dict)
    raw_identifier = models.CharField(max_length=128)
    normalized_identifier = models.CharField(max_length=64, blank=True)
    raw_total_score = models.CharField(max_length=64)
    total_score = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    captured_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEEDS_REVIEW)
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.UNRESOLVED)
    is_official = models.BooleanField(default=False)
    review_reason = models.CharField(max_length=80, blank=True)
    registration = models.ForeignKey("events.EventRegistration", null=True, blank=True, on_delete=models.PROTECT, related_name="simulator_captures")
    participant = models.ForeignKey("participants.Participant", null=True, blank=True, on_delete=models.PROTECT, related_name="simulator_captures")
    experience_session = models.ForeignKey("stations.ExperienceSession", null=True, blank=True, on_delete=models.PROTECT, related_name="simulator_captures")
    diagnostic_metadata = models.JSONField(default=dict, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="resolved_simulator_captures")
    resolution_reason = models.CharField(max_length=500, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="voided_simulator_captures")
    void_reason = models.CharField(max_length=500, blank=True)
    replaced_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replaces")

    class Meta:
        ordering = ("-received_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("experience_session",),
                condition=Q(is_official=True, voided_at__isnull=True),
                name="simulator_one_current_official_result",
            ),
        ]
        indexes = [models.Index(fields=("event", "status"), name="simulator_event_status_idx")]

    @property
    def leaderboard_eligible(self):
        return self.is_official and self.voided_at is None and self.status in (self.Status.MATCHED, self.Status.RESOLVED)

    def __str__(self):
        return f"Simulator capture {self.submission_id} ({self.status})"
