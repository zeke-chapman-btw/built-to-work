import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from apps.core.models import TimestampedModel


class QualificationCategory(TimestampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=110, unique=True)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="qualification_categories_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="qualification_categories_updated")

    class Meta:
        ordering = ("position", "name")
        verbose_name_plural = "qualification categories"

    def __str__(self):
        return self.name


class QualificationCatalogEntry(TimestampedModel):
    class Kind(models.TextChoices):
        SKILL = "skill", "Skill"
        CERTIFICATION = "certification", "Certification"
        LICENSE = "license", "License"
        QUALIFICATION = "qualification", "Qualification"
        EXPERIENCE = "experience", "Relevant experience"

    stable_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    identifier = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    category = models.ForeignKey(QualificationCategory, on_delete=models.PROTECT, related_name="entries")
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="qualification_catalog_entries_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="qualification_catalog_entries_updated")

    class Meta:
        ordering = ("category__position", "position", "name")
        permissions = (("manage_qualification_catalog", "Can manage qualification catalog configuration"),)
        indexes = [models.Index(fields=("kind", "is_active"), name="qual_catalog_kind_active_idx")]

    def clean(self):
        if self.is_active and self.category_id and not self.category.is_active:
            raise ValidationError({"category": "An active catalog entry must use an active category."})

    def __str__(self):
        return self.name


class ParticipantQualification(TimestampedModel):
    class Source(models.TextChoices):
        REGISTRATION = "registration", "Registration response"
        STAFF = "staff", "Staff recorded"
        HISTORICAL = "historical", "Historical import"
        OTHER = "other", "Other source"

    participant = models.ForeignKey("participants.Participant", on_delete=models.PROTECT, related_name="qualifications")
    catalog_entry = models.ForeignKey(QualificationCatalogEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="participant_records")
    custom_name = models.CharField(max_length=200, blank=True)
    experience_details = models.TextField(blank=True)
    years_of_experience = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(80)])
    notes = models.TextField(blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.STAFF)
    is_self_reported = models.BooleanField(default=False)
    source_submission = models.ForeignKey("participants.RegistrationSubmission", null=True, blank=True, on_delete=models.PROTECT, related_name="qualification_records")
    source_question = models.ForeignKey("participants.RegistrationQuestion", null=True, blank=True, on_delete=models.PROTECT, related_name="qualification_records")
    source_question_key = models.CharField(max_length=120, blank=True)
    source_answer_hash = models.CharField(max_length=64, blank=True)
    source_answer_snapshot = models.JSONField(default=dict, blank=True)
    mapping_snapshot = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="participant_qualifications_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="participant_qualifications_updated")

    class Meta:
        ordering = ("catalog_entry__category__position", "catalog_entry__position", "custom_name", "id")
        constraints = [
            models.CheckConstraint(condition=(Q(catalog_entry__isnull=False, custom_name="") | (Q(catalog_entry__isnull=True) & ~Q(custom_name=""))), name="participant_qualification_catalog_or_custom"),
            models.CheckConstraint(condition=(Q(years_of_experience__isnull=True) | Q(years_of_experience__gte=0)), name="participant_qualification_years_nonnegative"),
            models.UniqueConstraint(fields=("source_submission", "source_question_key", "source_answer_hash"), condition=Q(source_submission__isnull=False) & ~Q(source_answer_hash=""), name="participant_qualification_source_unique"),
        ]
        permissions = (("manage_participant_qualifications", "Can manage participant qualification records"),)
        indexes = [models.Index(fields=("participant", "is_active"), name="participant_qual_active_idx")]

    def clean(self):
        if bool(self.catalog_entry_id) == bool(self.custom_name.strip()):
            raise ValidationError("Choose one catalog qualification or enter one custom qualification.")
        if self.source_submission_id:
            if self.source_submission.participant_id != self.participant_id:
                raise ValidationError({"source_submission": "The source submission must belong to this participant."})
            if not self.source_question_key or not self.source_answer_hash:
                raise ValidationError({"source_question_key": "Mapped qualifications require their source question and answer fingerprint."})
        elif self.source_question_id or self.source_question_key or self.source_answer_hash:
            raise ValidationError("Question provenance requires a source registration submission.")
        if self.source == self.Source.REGISTRATION and not self.source_submission_id:
            raise ValidationError({"source_submission": "Registration qualifications require their source submission."})

    def __str__(self):
        return self.catalog_entry.name if self.catalog_entry_id else self.custom_name


class RegistrationQualificationMapping(TimestampedModel):
    class MappingType(models.TextChoices):
        CHOICE = "choice", "Exact predefined answer"
        CUSTOM_TEXT = "custom_text", "Explicit custom qualification text"

    form = models.ForeignKey("participants.RegistrationForm", on_delete=models.PROTECT, related_name="qualification_mappings")
    registration_question = models.ForeignKey("participants.RegistrationQuestion", on_delete=models.PROTECT, related_name="qualification_mappings")
    question_key = models.CharField(max_length=120, blank=True, help_text="Copied from the stable question key when saved.")
    mapping_type = models.CharField(max_length=16, choices=MappingType.choices)
    answer_value = models.CharField(max_length=200, blank=True, help_text="Exact choice value; blank only for an explicitly mapped custom-text field.")
    catalog_entry = models.ForeignKey(QualificationCatalogEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="registration_mappings")
    is_active = models.BooleanField(default=True)
    is_published = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="qualification_mappings_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="qualification_mappings_updated")

    class Meta:
        ordering = ("form_id", "question_key", "answer_value", "id")
        constraints = [models.UniqueConstraint(fields=("registration_question", "answer_value"), name="qual_mapping_question_answer_unique")]
        permissions = (("manage_qualification_mappings", "Can manage registration qualification mappings"),)
        indexes = [models.Index(fields=("form", "question_key", "is_published"), name="qual_mapping_lookup_idx")]

    def clean(self):
        errors = {}
        question = self.registration_question if self.registration_question_id else None
        if question and self.form_id and question.form_id != self.form_id:
            errors["registration_question"] = "The question must belong to the selected registration form."
        if question:
            if not self.question_key:
                self.question_key = question.canonical_key
            field_type = question.field_type
            if self.mapping_type == self.MappingType.CUSTOM_TEXT:
                if field_type not in {"short_text", "long_text"}:
                    errors["mapping_type"] = "Custom qualification mapping is allowed only for short or long text questions."
                if self.answer_value:
                    errors["answer_value"] = "Custom-text mappings do not use a fixed answer value."
                if self.catalog_entry_id:
                    errors["catalog_entry"] = "Custom-text mappings create custom qualifications, not catalog choices."
            elif self.mapping_type == self.MappingType.CHOICE:
                if field_type not in {"single_choice", "multiple_choice", "dropdown", "yes_no"}:
                    errors["mapping_type"] = "Exact mappings require a predefined-choice question."
                if not self.answer_value:
                    errors["answer_value"] = "Enter the exact choice value to map."
                if not self.catalog_entry_id:
                    errors["catalog_entry"] = "Select the catalog qualification for this choice."
                elif not self.catalog_entry.is_active and self.is_published:
                    errors["catalog_entry"] = "A published mapping must target an active catalog entry."
                if field_type == "yes_no":
                    options = {"yes", "no", "true", "false", "1", "0"}
                else:
                    options = set()
                    for option in question.options or []:
                        value = option.get("value", option.get("key", "")) if isinstance(option, dict) else option
                        if value is not None:
                            options.add(str(value))
                if self.answer_value and self.answer_value not in options:
                    errors["answer_value"] = "The value must exactly match one configured choice."
            if self.is_published and not question.is_active:
                errors["registration_question"] = "A published mapping must use an active question."
        if self.is_published and not self.is_active:
            errors["is_published"] = "An inactive mapping cannot be published."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.registration_question_id and not self.question_key:
            self.question_key = self.registration_question.canonical_key
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.form}: {self.question_key}"
