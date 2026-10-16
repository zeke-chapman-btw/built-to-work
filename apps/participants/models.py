import uuid
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.functions import Lower
from apps.core.models import ArchivableModel, TimestampedModel
from .normalization import normalize_email, normalize_phone
from .consent_storage import PrivateConsentStorage
from .historical_storage import PrivateHistoricalStorage

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
    address_line_1 = models.CharField(max_length=200, blank=True)
    address_line_2 = models.CharField(max_length=200, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    life_stage = models.CharField(max_length=64, blank=True)
    age_classification = models.CharField(max_length=16, blank=True)

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


class RegistrationForm(TimestampedModel):
    slug = models.SlugField(unique=True, default="standard")
    name = models.CharField(max_length=160, default="BTW Standard Intake")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    def __str__(self): return self.name

class RegistrationFormVersion(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        RETIRED = "retired", "Retired"
    form = models.ForeignKey(RegistrationForm, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    snapshot = models.JSONField(default=dict)
    published_at = models.DateTimeField(null=True, blank=True)
    effective_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="registration_form_versions")
    class Meta:
        constraints = [models.UniqueConstraint(fields=("form", "version"), name="registration_form_version_unique")]
        ordering = ("-version",)

class RegistrationQuestion(TimestampedModel):
    class FieldType(models.TextChoices):
        SHORT_TEXT = "short_text", "Short text"
        LONG_TEXT = "long_text", "Long text"
        YES_NO = "yes_no", "Yes / No"
        SINGLE = "single_choice", "Single choice"
        MULTIPLE = "multiple_choice", "Multiple choice"
        DROPDOWN = "dropdown", "Dropdown"
        DATE = "date", "Date"
        NUMBER = "number", "Number"
    form = models.ForeignKey(RegistrationForm, on_delete=models.PROTECT, related_name="questions")
    canonical_key = models.SlugField(max_length=100)
    label = models.CharField(max_length=240)
    help_text = models.TextField(blank=True)
    field_type = models.CharField(max_length=24, choices=FieldType.choices, default=FieldType.SHORT_TEXT)
    options = models.JSONField(default=list, blank=True)
    visibility_rule = models.JSONField(default=dict, blank=True)
    is_required = models.BooleanField(default=False)
    is_protected = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ("position", "id")
        constraints = [models.UniqueConstraint(fields=("form", "canonical_key"), name="registration_question_key_unique")]

class RegistrationSubmission(TimestampedModel):
    class Status(models.TextChoices):
        STARTED = "started", "Started"
        SUBMITTED = "submitted", "Submitted"
        INELIGIBLE = "ineligible", "Ineligible"
    form_version = models.ForeignKey(RegistrationFormVersion, on_delete=models.PROTECT, related_name="submissions")
    participant = models.ForeignKey(Participant, null=True, blank=True, on_delete=models.PROTECT, related_name="registration_submissions")
    event = models.ForeignKey("events.Event", null=True, blank=True, on_delete=models.PROTECT, related_name="registration_submissions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.STARTED)
    answers = models.JSONField(default=dict)
    answer_snapshot = models.JSONField(default=dict)
    age_classification = models.CharField(max_length=16, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=32, default="public")
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, blank=True)

class ConsentDocumentVersion(TimestampedModel):
    key = models.SlugField(max_length=80)
    version = models.CharField(max_length=40)
    title = models.CharField(max_length=200)
    body = models.TextField()
    original_pdf = models.FileField(upload_to="consent_documents/", storage=PrivateConsentStorage(), blank=True, null=True)
    content_hash = models.CharField(max_length=64, blank=True)
    is_approved = models.BooleanField(default=False)
    effective_at = models.DateTimeField(null=True, blank=True)
    text_verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="verified_consent_documents")
    text_verified_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="published_consent_documents")
    published_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("key", "version"), name="consent_document_version_unique")]

class ConsentAcceptance(TimestampedModel):
    participant = models.ForeignKey(Participant, on_delete=models.PROTECT, related_name="consent_acceptances")
    submission = models.ForeignKey(RegistrationSubmission, on_delete=models.PROTECT, related_name="consent_acceptances")
    document = models.ForeignKey(ConsentDocumentVersion, on_delete=models.PROTECT, related_name="acceptances")
    accepted_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)


class HistoricalMappingTemplate(models.Model):
    class Kind(models.TextChoices):
        IMPORT = "import", "Import"
        EXPORT = "export", "Export"

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    mapping = models.JSONField(default=dict, blank=True)
    fields = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class HistoricalImportBatch(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        PREVIEWED = "previewed", "Previewed"
        IMPORTED = "imported", "Imported"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_file = models.FileField(storage=PrivateHistoricalStorage(), upload_to="sources/")
    original_filename = models.CharField(max_length=255)
    source_sha256 = models.CharField(max_length=64)
    mapping = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UPLOADED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class HistoricalImportRow(models.Model):
    class Status(models.TextChoices):
        READY = "ready", "Ready"
        ERROR = "error", "Error"
        REVIEW = "review", "Needs duplicate review"
        IMPORTED = "imported", "Imported"
        SKIPPED = "skipped", "Skipped"

    batch = models.ForeignKey(HistoricalImportBatch, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField()
    raw_data = models.JSONField(default=dict)
    normalized_data = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices)
    error = models.TextField(blank=True)
    possible_matches = models.JSONField(default=list, blank=True)
    participant = models.ForeignKey(Participant, null=True, blank=True, on_delete=models.PROTECT)
    review_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("batch", "row_number"), name="historical_batch_row_unique")]
        ordering = ("row_number",)


class HistoricalEventRecord(models.Model):
    participant = models.ForeignKey(Participant, on_delete=models.PROTECT, related_name="historical_events")
    event = models.ForeignKey("events.Event", null=True, blank=True, on_delete=models.PROTECT)
    event_name = models.CharField(max_length=255)
    event_date = models.DateField(null=True, blank=True)
    group_name = models.CharField(max_length=120, blank=True)
    source_batch = models.ForeignKey(HistoricalImportBatch, on_delete=models.PROTECT)
    source_row_number = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=("source_batch", "source_row_number"), name="historical_event_source_unique")]


class HistoricalSimulatorScore(models.Model):
    participant = models.ForeignKey(Participant, on_delete=models.PROTECT, related_name="historical_simulator_scores")
    event_record = models.ForeignKey(HistoricalEventRecord, null=True, blank=True, on_delete=models.PROTECT)
    raw_score = models.DecimalField(max_digits=14, decimal_places=4)
    score_method = models.CharField(max_length=120)
    source = models.CharField(max_length=255)
    scored_at = models.DateTimeField(null=True, blank=True)
    source_batch = models.ForeignKey(HistoricalImportBatch, on_delete=models.PROTECT)
    source_row_number = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=("source_batch", "source_row_number"), name="historical_score_source_unique")]


class HistoricalExportRecord(models.Model):
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    criteria = models.JSONField(default=dict)
    row_count = models.PositiveIntegerField()
    format = models.CharField(max_length=4)
    created_at = models.DateTimeField(auto_now_add=True)

from .qualification_models import QualificationCategory, QualificationCatalogEntry, ParticipantQualification, RegistrationQualificationMapping

from .participant_document_models import ParticipantDocumentCategory, ParticipantDocument, ParticipantDocumentAudit, QualificationDocumentLink

from .verification_models import ParticipantVerificationRecord

from .participant_review_models import ParticipantReviewRecord
