"""Internal participant review, independent of credential verification and hiring."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from apps.core.models import TimestampedModel


class ParticipantReviewRecord(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    participant = models.ForeignKey("participants.Participant", on_delete=models.PROTECT, related_name="internal_reviews")
    status = models.CharField(max_length=16, choices=Status.choices)
    internal_notes = models.TextField(max_length=4000)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="internal_participant_reviews")
    reviewed_at = models.DateTimeField(default=timezone.now, editable=False)
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="later_reviews")

    class Meta:
        ordering = ("-created_at", "-pk")
        permissions = [("review_participants", "Can record internal participant review decisions")]

    def clean(self):
        super().clean()
        if not self.internal_notes.strip():
            raise ValidationError({"internal_notes": "Internal review notes are required."})
        if self.supersedes_id and self.supersedes.participant_id != self.participant_id:
            raise ValidationError({"supersedes": "The prior review must belong to this participant."})

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Review history is append-only; record a new decision.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Internal review history cannot be deleted.")
