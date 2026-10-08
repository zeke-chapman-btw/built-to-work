import uuid
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.functions import Lower
from apps.core.models import ArchivableModel, TimestampedModel
from .normalization import normalize_email, normalize_phone

class Participant(TimestampedModel, ArchivableModel):
    class Kind(models.TextChoices):
        PERSON = "person", "Person"
        SYSTEM_TEST = "system_test", "BTW test identity"

    identity_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.PERSON)
    test_qr_token = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    preferred_name = models.CharField(max_length=100, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=24, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("kind",), condition=Q(kind="system_test"), name="participants_one_system_test_identity")]
        ordering = ["last_name", "first_name", "id"]
    def save(self, *args, **kwargs):
        self.contact_email = normalize_email(self.contact_email)
        self.contact_phone = normalize_phone(self.contact_phone)
        super().save(*args, **kwargs)
    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()

class ParticipantCareerProfile(TimestampedModel):
    participant = models.OneToOneField(Participant, on_delete=models.PROTECT, related_name="career_profile")
    employment_status = models.CharField(max_length=100, blank=True)
    career_interests = models.TextField(blank=True)
    work_availability = models.CharField(max_length=160, blank=True)
    willing_to_travel = models.BooleanField(null=True, blank=True)
    education_training = models.TextField(blank=True)
    skills_interests = models.TextField(blank=True)


class TestParticipantRun(models.Model):
    """Nonofficial run linked to the permanent test identity; never a registration."""
    participant = models.ForeignKey(Participant, on_delete=models.PROTECT, related_name="test_runs")
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="test_participant_runs")
    experience_session = models.OneToOneField("stations.ExperienceSession", on_delete=models.PROTECT, related_name="test_participant_run")
    started_at = models.DateTimeField(auto_now_add=True)
    reset_at = models.DateTimeField(null=True, blank=True)
    reset_reason = models.CharField(max_length=240, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("participant", "event"), condition=Q(reset_at__isnull=True), name="participants_one_active_test_run")]

    def clean(self):
        if self.participant_id and self.participant.kind != Participant.Kind.SYSTEM_TEST:
            raise ValidationError("A test run requires the system test identity.")
        if self.experience_session_id:
            session = self.experience_session
            if session.mode != "staff_test" or session.registration_id or session.participant_id or session.event_id != self.event_id:
                raise ValidationError("A test run requires an unlinked staff-test experience in the same Event.")


class ParticipantAccountRequest(TimestampedModel):
    class Status(models.TextChoices):
        PENDING_VERIFICATION = "pending_verification", "Pending email verification"
        PENDING_REVIEW = "pending_review", "Pending staff review"
        LINKED = "linked", "Account linked"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    preferred_name = models.CharField(max_length=100, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    login_email = models.EmailField()
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING_VERIFICATION)
    matched_participant = models.ForeignKey(Participant, null=True, blank=True, on_delete=models.PROTECT, related_name="account_requests")
    match_count = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="resolved_participant_requests")
    resolved_at = models.DateTimeField(null=True, blank=True)
    def save(self, *args, **kwargs):
        self.login_email = normalize_email(self.login_email)
        self.contact_email = normalize_email(self.contact_email)
        self.contact_phone = normalize_phone(self.contact_phone)
        super().save(*args, **kwargs)
    def __str__(self):
        return f"Account request for {self.login_email} ({self.status})"

class ParticipantAccount(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending setup"
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled"
    participant = models.OneToOneField(Participant, on_delete=models.PROTECT, related_name="account")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="participant_account")
    login_email = models.EmailField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    password_set_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(Lower("login_email"), condition=Q(status="active"), name="participant_active_login_email_ci_uniq")]
    def save(self, *args, **kwargs):
        self.login_email = normalize_email(self.login_email)
        super().save(*args, **kwargs)
    def __str__(self):
        return f"{self.login_email} ({self.status})"

class ParticipantEmailChange(TimestampedModel):
    account = models.OneToOneField(ParticipantAccount, on_delete=models.CASCADE, related_name="pending_email_change")
    new_email = models.EmailField()
    verified_at = models.DateTimeField(null=True, blank=True)
    def save(self, *args, **kwargs):
        self.new_email = normalize_email(self.new_email)
        super().save(*args, **kwargs)
