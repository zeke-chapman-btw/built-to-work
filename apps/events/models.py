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
    allow_station_auto_check_in = models.BooleanField(default=False)
    registration_opens_at = models.DateTimeField(null=True, blank=True)
    registration_closes_at = models.DateTimeField(null=True, blank=True)
    registration_deadline_at = models.DateTimeField(null=True, blank=True)
    registration_capacity = models.PositiveIntegerField(null=True, blank=True)
    group_mode = models.CharField(max_length=12, choices=(("disabled", "Disabled"), ("optional", "Optional"), ("required", "Required")), default="disabled")
    group_label = models.CharField(max_length=80, default="Group")
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
    def registration_is_open(self):
        now = timezone.now()
        return ((self.registration_opens_at is None or now >= self.registration_opens_at)
                and (self.registration_closes_at is None or now <= self.registration_closes_at)
                and (self.registration_deadline_at is None or now <= self.registration_deadline_at))

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


class EventGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="groups")
    name = models.CharField(max_length=160)
    is_active = models.BooleanField(default=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    registration_deadline_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [models.UniqueConstraint(fields=("event", "name"), name="event_group_unique_name")]

    def __str__(self):
        return self.name

    @property
    def registration_count(self):
        return self.registrations.filter(status=EventRegistration.Status.ACTIVE).count()


class EventRegistrationQuestion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="registration_questions")
    key = models.SlugField(max_length=80)
    label = models.CharField(max_length=200)
    field_type = models.CharField(max_length=20, choices=(("short_text", "Short text"), ("long_text", "Long text"), ("single_choice", "Single choice"), ("yes_no", "Yes / no")), default="short_text")
    options = models.JSONField(default=list, blank=True)
    is_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("position", "id")
        constraints = [models.UniqueConstraint(fields=("event", "key"), name="event_registration_question_unique_key")]

    def __str__(self):
        return self.label


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
    group = models.ForeignKey(EventGroup, null=True, blank=True, on_delete=models.PROTECT, related_name="registrations")
    custom_answers = models.JSONField(default=dict, blank=True)
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

    def clean(self):
        super().clean()
        if self.group_id and self.event_id != self.group.event_id:
            raise ValidationError({"group": "The group must belong to this Event."})

    @property
    def is_walk_in(self):
        return self.source == self.Source.WALK_IN

    def __str__(self):
        return f"{self.participant} — {self.event}"


class TicketAllocationBlock(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=32, unique=True)
    start_number = models.BigIntegerField(unique=True)
    end_number = models.BigIntegerField()
    next_number = models.BigIntegerField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ("start_number",)
    def clean(self):
        super().clean()
        if self.start_number < 0 or self.end_number > 9999999999 or self.start_number > self.end_number:
            raise ValidationError("Ticket allocation block must contain ten-digit values.")
        if not self.start_number <= self.next_number <= self.end_number + 1:
            raise ValidationError("Ticket allocation counter is outside the block.")
        overlap = type(self).objects.exclude(pk=self.pk).filter(
            start_number__lte=self.end_number, end_number__gte=self.start_number
        ).exists()
        if overlap:
            raise ValidationError("Ticket allocation blocks cannot overlap.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
    def __str__(self):
        return self.name


class QrTicket(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.ForeignKey(EventRegistration, on_delete=models.PROTECT, related_name="tickets")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    ticket_number = models.CharField(max_length=10, unique=True, null=True, blank=True)
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


class TrailerInstance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    identity = models.CharField(max_length=120, unique=True)
    display_name = models.CharField(max_length=160)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.display_name


class OfflineEventPreparation(models.Model):
    class Status(models.TextChoices):
        PREPARED = "prepared", "Prepared"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"
        BLOCKED = "blocked", "Blocked"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="offline_preparations")
    trailer = models.ForeignKey(TrailerInstance, on_delete=models.PROTECT, related_name="event_preparations")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PREPARED)
    consent_document = models.ForeignKey("participants.ConsentDocumentVersion", on_delete=models.PROTECT, null=True, blank=True)
    consent_hash = models.CharField(max_length=64, blank=True)
    sync_identity = models.CharField(max_length=120, blank=True)
    last_readiness_check_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("event", "trailer"), name="offline_prep_event_trailer_unique")]


class OfflineTicketRange(models.Model):
    class Kind(models.TextChoices):
        PRIMARY = "primary", "Primary"
        BACKUP = "backup", "Backup"
    preparation = models.ForeignKey(OfflineEventPreparation, on_delete=models.PROTECT, related_name="ticket_ranges")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    start_number = models.BigIntegerField()
    end_number = models.BigIntegerField()
    next_number = models.BigIntegerField()
    is_exhausted = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("preparation", "kind"), name="offline_range_kind_unique")]

    def clean(self):
        if not (0 <= self.start_number <= self.end_number <= 9999999999):
            raise ValidationError("Offline ranges must contain ten-digit values.")
        if not self.start_number <= self.next_number <= self.end_number + 1:
            raise ValidationError("Offline range counter is outside the range.")


class SyncOutboxItem(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_FLIGHT = "in_flight", "In flight"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    preparation = models.ForeignKey(OfflineEventPreparation, on_delete=models.PROTECT, related_name="sync_outbox")
    operation_key = models.CharField(max_length=160, unique=True)
    operation_type = models.CharField(max_length=60)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=("status", "next_attempt_at"), name="sync_outbox_ready_idx")]


class OfflineRegistrationEnvelope(models.Model):
    class Status(models.TextChoices):
        LOCAL = "local", "Local"
        SYNCED = "synced", "Synced"
        REVIEW = "review", "Needs review"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    preparation = models.ForeignKey(OfflineEventPreparation, on_delete=models.PROTECT, related_name="registrations")
    participant_identity = models.UUIDField()
    registration_id = models.UUIDField(null=True, blank=True)
    ticket_number = models.CharField(max_length=10)
    consent_document_version = models.PositiveIntegerField()
    consent_hash = models.CharField(max_length=64)
    group_name = models.CharField(max_length=160, blank=True)
    custom_answers = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.LOCAL)
    created_at = models.DateTimeField(auto_now_add=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("preparation", "ticket_number"), name="offline_ticket_unique_per_prep")]


class TicketDelivery(models.Model):
    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "Text"
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        UNCERTAIN = "uncertain", "Uncertain"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.ForeignKey(EventRegistration, on_delete=models.PROTECT, related_name="deliveries")
    channel = models.CharField(max_length=8, choices=Channel.choices)
    destination_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    provider_reference = models.CharField(max_length=160, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("registration", "channel", "destination_hash"), name="delivery_destination_unique")]


class ParticipantMatchReview(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        REJECTED = "rejected", "Rejected"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    envelope = models.ForeignKey(OfflineRegistrationEnvelope, on_delete=models.PROTECT, related_name="match_reviews")
    candidate_participant = models.ForeignKey(Participant, on_delete=models.PROTECT, null=True, blank=True)
    evidence = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.CharField(max_length=240, blank=True)


class BackupRecord(models.Model):
    class Status(models.TextChoices):
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trailer = models.ForeignKey(TrailerInstance, on_delete=models.PROTECT, related_name="backups")
    storage_path = models.CharField(max_length=500)
    ciphertext_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
