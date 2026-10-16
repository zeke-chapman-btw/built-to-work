import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from apps.core.models import TimestampedModel


class ParticipantVerificationRecord(TimestampedModel):
    """Append-only review decision for one participant qualification or document."""

    class Status(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        PENDING = "pending", "Pending Review"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    class Method(models.TextChoices):
        DOCUMENT_VERIFIED = "document_verified", "Document-verified"
        SKILL_ASSESSED = "skill_assessed", "Skill-assessed"
        STAFF_CONFIRMED = "staff_confirmed", "Staff-confirmed"
        SELF_REPORTED = "self_reported", "Self-reported"

    participant = models.ForeignKey("participants.Participant", on_delete=models.PROTECT, related_name="verification_records")
    qualification = models.ForeignKey("participants.ParticipantQualification", null=True, blank=True, on_delete=models.PROTECT, related_name="verification_records")
    document = models.ForeignKey("participants.ParticipantDocument", null=True, blank=True, on_delete=models.PROTECT, related_name="verification_records")
    evidence_document = models.ForeignKey("participants.ParticipantDocument", null=True, blank=True, on_delete=models.PROTECT, related_name="qualification_evidence_records")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    method = models.CharField(max_length=24, choices=Method.choices, default=Method.SELF_REPORTED)
    evidence_notes = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    qualification_snapshot = models.JSONField(default=dict, blank=True)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="participant_verification_decisions")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by")
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=(Q(qualification__isnull=False, document__isnull=True) | Q(qualification__isnull=True, document__isnull=False)),
                name="participant_verification_one_target",
            ),
        ]
        permissions = [
            ("view_qualificationapprovals", "Can view participant qualification and document approvals"),
            ("review_qualificationapprovals", "Can decide participant qualification and document approvals"),
        ]

    def clean(self):
        super().clean()
        if bool(self.qualification_id) == bool(self.document_id):
            raise ValidationError("A verification record must target exactly one qualification or document.")
        if self.qualification_id and self.qualification.participant_id != self.participant_id:
            raise ValidationError({"qualification": "Qualification belongs to another participant."})
        if self.document_id and self.document.participant_id != self.participant_id:
            raise ValidationError({"document": "Document belongs to another participant."})
        if self.evidence_document_id:
            evidence = self.evidence_document
            if evidence.participant_id != self.participant_id:
                raise ValidationError({"evidence_document": "Evidence must belong to the same participant."})
            if evidence.state != evidence.State.ACTIVE:
                raise ValidationError({"evidence_document": "Archived or superseded documents cannot support a new decision."})
        if self.status == self.Status.VERIFIED and self.document_id:
            from django.utils import timezone
            if self.document.state != self.document.State.ACTIVE or (self.document.expiration_date and self.document.expiration_date < timezone.localdate()):
                raise ValidationError("Only current, unexpired documents can be verified.")
        if self.status == self.Status.VERIFIED and self.qualification_id and self.qualification.expiration_date:
            from django.utils import timezone
            if self.qualification.expiration_date < timezone.localdate():
                raise ValidationError("Expired qualifications cannot be verified.")
        if self.status == self.Status.VERIFIED:
            if not self.reviewer_id or not self.reviewed_at:
                raise ValidationError("A verified decision requires an authorized reviewer and review timestamp.")
            if self.method == self.Method.SELF_REPORTED:
                raise ValidationError("Self-reported information cannot be marked independently verified.")
            if self.method == self.Method.DOCUMENT_VERIFIED:
                evidence = self.evidence_document or self.document
                if not evidence or evidence.state != evidence.State.ACTIVE:
                    raise ValidationError("Document verification requires a current active document version.")
                from django.utils import timezone
                if evidence.expiration_date and evidence.expiration_date < timezone.localdate():
                    raise ValidationError("Expired documents cannot support a new verification decision.")
            elif self.qualification_id and self.method in (self.Method.SKILL_ASSESSED, self.Method.STAFF_CONFIRMED) and not self.evidence_notes.strip():
                raise ValidationError("Skill assessment or staff confirmation requires a supporting explanation.")
        if self.status == self.Status.REJECTED and not self.reason.strip():
            raise ValidationError({"reason": "A rejection requires a reason."})
        if self.status in (self.Status.VERIFIED, self.Status.REJECTED) and not self.reviewer_id:
            raise ValidationError("A decision requires an authorized reviewer.")

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Verification history is append-only; create a new decision instead.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Verification history cannot be deleted.")

    def __str__(self):
        target = self.qualification or self.document
        return f"{self.get_status_display()}: {target}"
