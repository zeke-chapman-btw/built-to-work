import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from apps.core.models import TimestampedModel
from .participant_document_storage import PrivateParticipantDocumentStorage


def participant_document_upload_to(instance, filename):
    # Never derive a path from the client-provided filename.
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"{instance.uuid.hex[:2]}/{instance.uuid.hex}.{suffix}"


class ParticipantDocumentCategory(TimestampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="participant_document_categories_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="participant_document_categories_updated")

    class Meta:
        ordering = ("position", "name")
        verbose_name = "participant document category"
        verbose_name_plural = "participant document categories"
        permissions = [("manage_participant_document_categories", "Can manage participant document categories")]

    def __str__(self):
        return self.name


class ParticipantDocument(TimestampedModel):
    class State(models.TextChoices):
        ACTIVE = "active", "Active"
        REPLACED = "replaced", "Replaced"
        ARCHIVED = "archived", "Archived"

    class UploadSource(models.TextChoices):
        STAFF = "staff", "Staff"
        PARTICIPANT = "participant", "Participant"
        IMPORT = "import", "Import"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    participant = models.ForeignKey("participants.Participant", on_delete=models.PROTECT, related_name="documents")
    category = models.ForeignKey(ParticipantDocumentCategory, on_delete=models.PROTECT, related_name="documents")
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=participant_document_upload_to, storage=PrivateParticipantDocumentStorage())
    content_type = models.CharField(max_length=80)
    file_size = models.PositiveBigIntegerField()
    uploaded_at = models.DateTimeField(default=timezone.now, editable=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="participant_documents_uploaded")
    upload_source = models.CharField(max_length=16, choices=UploadSource.choices, default=UploadSource.STAFF)
    description = models.CharField(max_length=500, blank=True)
    participant_visible = models.BooleanField(default=False, help_text='Allow this participant to download the file in My Profile. Internal staff notes and verification evidence remain private.')
    issue_date = models.DateField(null=True, blank=True)
    expiration_date = models.DateField(null=True, blank=True)
    state = models.CharField(max_length=12, choices=State.choices, default=State.ACTIVE)
    replaces = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="replacements")

    class Meta:
        ordering = ("-uploaded_at", "-pk")
        permissions = [("manage_participant_documents", "Can manage participant documents")]
        indexes = [models.Index(fields=("participant", "state")), models.Index(fields=("expiration_date", "state"))]

    def clean(self):
        super().clean()
        if self.expiration_date and self.issue_date and self.expiration_date < self.issue_date:
            raise ValidationError({"expiration_date": "Expiration cannot be before the issue date."})
        if self.replaces_id and self.participant_id and self.replaces.participant_id != self.participant_id:
            raise ValidationError({"replaces": "A replacement must belong to the same participant."})
        if self.replaces_id and self.replaces.state == self.State.ARCHIVED:
            raise ValidationError({"replaces": "An archived document cannot be replaced."})

    @property
    def is_expired(self):
        return bool(self.expiration_date and self.expiration_date < timezone.localdate())

    def __str__(self):
        return f"{self.original_filename} — {self.participant}"


class ParticipantDocumentAudit(TimestampedModel):
    document = models.ForeignKey(ParticipantDocument, on_delete=models.PROTECT, related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="participant_document_audit_events")
    action = models.CharField(max_length=24)
    reason = models.CharField(max_length=500, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("created_at", "pk")
        permissions = [("view_participant_document_audit", "Can view participant document audit history")]


class QualificationDocumentLink(TimestampedModel):
    document = models.ForeignKey(ParticipantDocument, on_delete=models.PROTECT, related_name="qualification_links")
    qualification = models.ForeignKey("participants.ParticipantQualification", on_delete=models.PROTECT, related_name="document_links")
    linked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="qualification_document_links_created")
    reason = models.CharField(max_length=500)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("document", "qualification"), name="uniq_qualification_document_link")]
        permissions = [("manage_qualification_document_links", "Can manage qualification document links")]

    def clean(self):
        super().clean()
        if self.document_id and self.qualification_id and self.document.participant_id != self.qualification.participant_id:
            raise ValidationError("A document and qualification must belong to the same participant.")
