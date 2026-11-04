"""Recruiting extensions reference the existing authoritative person and customer records."""
import uuid
from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models
from django.db.models import Q
from apps.core.models import UUIDModel, TimestampedModel, ArchivableModel


class RecruitingLock(models.Model):
    """One lock order for identity changes and recruiting policy-dependent writes."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)


class Classification(UUIDModel, TimestampedModel):
    kind = models.CharField(max_length=16, choices=[('employment', 'Employment'), ('availability', 'Availability')])
    key = models.SlugField(max_length=80)
    label = models.CharField(max_length=120)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    class Meta:
        ordering = ['kind', 'position', 'label', 'pk']
        constraints = [models.UniqueConstraint(fields=['kind', 'key'], name='recruiting_classification_key')]
    def __str__(self):
        return self.label


class RecruitingProfile(TimestampedModel):
    participant = models.OneToOneField('participants.Participant', on_delete=models.PROTECT, related_name='recruiting_profile')
    is_lead = models.BooleanField(default=False)
    lead_source = models.CharField(max_length=240, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    employment_classification = models.ForeignKey(Classification, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    availability_classification = models.ForeignKey(Classification, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    willing_to_relocate = models.BooleanField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=0)
    last_relevant_update_at = models.DateTimeField(null=True, blank=True)
    last_confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_fingerprint = models.CharField(max_length=64, blank=True)


class ProfileConfirmation(UUIDModel, TimestampedModel):
    participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='recruiting_confirmations')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64)
    source = models.CharField(max_length=24, choices=[('participant', 'Participant'), ('staff_assisted', 'Staff assisted')])
    reason = models.CharField(max_length=500, blank=True)


class ProfileUpdateRequest(UUIDModel, TimestampedModel):
    participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='recruiting_update_requests')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    message = models.CharField(max_length=500)
    task = models.ForeignKey('operations.Task', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    fulfilled_by = models.ForeignKey(ProfileConfirmation, null=True, blank=True, on_delete=models.PROTECT, related_name='+')


class CandidateList(UUIDModel, TimestampedModel, ArchivableModel):
    name = models.CharField(max_length=120)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    shared = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner', 'name'], name='recruiting_list_owner_name')]
        ordering = ['name', 'pk']


class CandidateListEntry(UUIDModel, TimestampedModel):
    candidate_list = models.ForeignKey(CandidateList, on_delete=models.PROTECT, related_name='entries')
    participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='+')
    source_participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='+')
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    removed_at = models.DateTimeField(null=True, blank=True)
    removal_reason = models.CharField(max_length=500, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['candidate_list', 'participant'], condition=Q(removed_at__isnull=True), name='recruiting_list_active_person')]


class CampaignRecruitingPolicy(TimestampedModel):
    campaign = models.OneToOneField('customers.Campaign', on_delete=models.PROTECT, related_name='recruiting_policy')
    exclusivity_days = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(3650)])
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    revision = models.PositiveIntegerField(default=0)


class CampaignSource(UUIDModel, TimestampedModel):
    campaign = models.ForeignKey('customers.Campaign', on_delete=models.PROTECT, related_name='candidate_sources')
    participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='campaign_sources')
    source_participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='+')
    sourced_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    exclusive = models.BooleanField(default=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    release_reason = models.CharField(max_length=500, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['campaign', 'source_participant'], name='recruiting_campaign_original_source')]


class IdentityConsolidation(UUIDModel, TimestampedModel):
    review = models.OneToOneField('events.ParticipantMatchReview', on_delete=models.PROTECT, related_name='consolidation')
    source = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='+')
    target = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='+')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500)
    fingerprint = models.CharField(max_length=64)
    collision_history = models.JSONField(default=list)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_review_required = models.BooleanField(default=False)
    reversal_note = models.CharField(max_length=500, blank=True)
    class Meta:
        permissions = [('manage_recruiting', 'Manage authorized recruiting opportunities and candidates')]


class WorkforceRequest(UUIDModel, TimestampedModel, ArchivableModel):
    organization = models.ForeignKey('customers.CustomerOrganization', on_delete=models.PROTECT, related_name='workforce_requests')
    name = models.CharField(max_length=160)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    revision = models.PositiveIntegerField(default=0)


class WorkforceRequestPosition(UUIDModel, TimestampedModel):
    request = models.ForeignKey(WorkforceRequest, on_delete=models.PROTECT, related_name='positions')
    need = models.OneToOneField('customers.WorkforceNeed', on_delete=models.PROTECT, related_name='request_position')
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
