import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.participants.models import Participant


class Event(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UPCOMING = "upcoming", "Upcoming"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=40, unique=True, null=True, blank=True)
    description = models.TextField(blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    timezone_name = models.CharField(max_length=64, default="UTC")
    location_name = models.CharField(max_length=200, blank=True)
    address_line_1 = models.CharField(max_length=200, blank=True)
    address_line_2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=24, blank=True)
    country = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_events")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("start_at", "name")
        constraints = [models.CheckConstraint(condition=Q(end_at__gte=F("start_at")), name="event_end_at_or_after_start")]
        indexes = [models.Index(fields=("status", "start_at"), name="event_status_start_idx")]

    def clean(self):
        super().clean()
        try:
            ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, TypeError):
            raise ValidationError({"timezone_name": "Enter a valid IANA timezone, such as America/Chicago."})
        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValidationError({"end_at": "Event end must be at or after event start."})

    @property
    def is_current(self):
        now = timezone.now()
        return self.status == self.Status.UPCOMING and self.start_at <= now <= self.end_at

    @property
    def local_start_at(self):
        return timezone.localtime(self.start_at, ZoneInfo(self.timezone_name))

    @property
    def local_end_at(self):
        return timezone.localtime(self.end_at, ZoneInfo(self.timezone_name))

    def __str__(self):
        return self.name


class EventRegistration(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        VOID = "void", "Voided"

    class Source(models.TextChoices):
        PUBLIC = "public", "Public registration"
        STAFF = "staff", "Staff registration"
        WALK_IN = "walk_in", "Walk-in"
        IMPORT = "import", "Import"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="registrations")
    participant = models.ForeignKey(Participant, on_delete=models.PROTECT, related_name="event_registrations")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.STAFF)
    registered_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_event_registrations")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-registered_at",)
        constraints = [models.UniqueConstraint(fields=("event", "participant"), condition=Q(status="active"), name="one_active_event_registration")]
        indexes = [models.Index(fields=("event", "status"), name="event_reg_status_idx")]

    @property
    def is_walk_in(self):
        return self.source == self.Source.WALK_IN

    def __str__(self):
        return f"{self.participant} — {self.event}"


class QrTicket(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.ForeignKey(EventRegistration, on_delete=models.PROTECT, related_name="tickets")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    is_current = models.BooleanField(default=True)
    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="supersedes")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="issued_event_tickets")
    reissue_reason = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ("-issued_at",)
        constraints = [models.UniqueConstraint(fields=("registration",), condition=Q(is_current=True), name="one_current_qr_ticket_per_registration")]
        indexes = [models.Index(fields=("token", "is_current"), name="qr_ticket_token_current_idx")]

    def __str__(self):
        return f"Ticket for registration {self.registration_id}"


class Attendance(models.Model):
    class Source(models.TextChoices):
        STAFF = "staff", "Staff"
        QR_SCAN = "qr_scan", "QR scan"
        STATION = "station", "Station auto check-in"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="attendance_records")
    registration = models.ForeignKey(EventRegistration, on_delete=models.PROTECT, related_name="attendance_records")
    participant = models.ForeignKey(Participant, on_delete=models.PROTECT, related_name="attendance_records")
    checked_in_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.STAFF)
    checked_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="event_check_ins")
    is_void = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="voided_event_check_ins")
    void_reason = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-checked_in_at",)
        constraints = [models.UniqueConstraint(fields=("registration",), condition=Q(is_void=False), name="one_active_attendance_per_registration")]
        indexes = [models.Index(fields=("event", "checked_in_at"), name="attendance_event_time_idx")]

    def clean(self):
        super().clean()
        if self.registration_id and (self.event_id != self.registration.event_id or self.participant_id != self.registration.participant_id):
            raise ValidationError("Attendance event and participant must match its registration.")

    def __str__(self):
        return f"Check-in for registration {self.registration_id}"
