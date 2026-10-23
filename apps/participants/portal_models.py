"""Portal delivery state and event intent; canonical people/registrations stay existing models."""
import uuid
from django.conf import settings
from django.db import models
from apps.core.models import TimestampedModel


class PortalInvitation(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    participant = models.OneToOneField('participants.Participant', on_delete=models.PROTECT, related_name='portal_invitation')
    submission = models.ForeignKey('participants.RegistrationSubmission', on_delete=models.PROTECT)
    email = models.EmailField()
    nonce = models.UUIDField(default=uuid.uuid4, editable=False)
    activated_at = models.DateTimeField(null=True, blank=True)
    last_requested_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [('manage_portal_invitations', 'Can inspect and resend participant portal invitations')]


class PortalDelivery(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT = 'sent', 'Accepted by email backend'
        TEST = 'test', 'Test backend only; not delivered'
        HELD = 'held', 'Held for configuration or eligibility'
        SENDING = 'sending', 'Sending; uncertain until reconciled'
        CANCELLED = 'cancelled', 'Cancelled'

    invitation = models.ForeignKey(PortalInvitation, on_delete=models.PROTECT, related_name='deliveries')
    slot = models.CharField(max_length=64)
    due_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    attempted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    detail = models.CharField(max_length=160, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['invitation', 'slot'], name='portal_invite_delivery_slot_unique')]
        indexes = [models.Index(fields=['status', 'due_at'], name='portal_delivery_due_idx')]


class ParticipantEventInterest(TimestampedModel):
    participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='portal_interests')
    event = models.ForeignKey('events.Event', on_delete=models.PROTECT, related_name='portal_interests')
    # Interest never confers admission. Confirmations live only in EventRegistration.
    class Meta:
        constraints = [models.UniqueConstraint(fields=['participant', 'event'], name='portal_person_event_interest_unique')]


class PortalAccessAttempt(models.Model):
    """Bounded recovery/login throttling without retaining raw email or IP addresses."""
    key = models.CharField(max_length=64, primary_key=True)
    started_at = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)
