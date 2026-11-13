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
    kind = models.CharField(max_length=16, choices=[('employment', 'Employment'), ('availability', 'Availability'), ('trade', 'Trade')])
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
    # NULL retains the pre-existing classification/boolean contract. An explicit
    # blank records current Unknown without replaying historical profile text.
    CURRENT_EMPLOYMENT = [('', 'Unknown'), ('full_time', 'Full time'), ('part_time', 'Part time'),
        ('self_employed', 'Self employed'), ('contract', 'Contract'), ('unemployed', 'Unemployed')]
    current_employment_status = models.CharField(max_length=20, choices=CURRENT_EMPLOYMENT, null=True, blank=True, default=None)
    relocation_preference = models.CharField(max_length=5, choices=[('', 'Unknown'), ('yes', 'Yes'), ('maybe', 'Maybe'), ('no', 'No')], null=True, blank=True, default=None)
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


class RecruitingStage(TimestampedModel):
    key = models.SlugField(max_length=80, primary_key=True)
    label = models.CharField(max_length=100)
    position = models.PositiveSmallIntegerField(default=0)
    semantic = models.CharField(max_length=16, default='active', choices=[('active', 'Active'), ('hired', 'Hired'), ('not_selected', 'Not selected'), ('withdrawn', 'Withdrawn')])
    is_active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ['position', 'key']
        constraints = [models.CheckConstraint(condition=(Q(key='hired',semantic='hired') | Q(key='not_selected',semantic='not_selected') | Q(key='withdrawn',semantic='withdrawn') | (~Q(key__in=['hired','not_selected','withdrawn']) & Q(semantic='active'))), name='recruiting_stage_outcome_semantics')]


class RecruitingOpportunity(UUIDModel, TimestampedModel, ArchivableModel):
    need = models.OneToOneField('customers.WorkforceNeed', on_delete=models.PROTECT, related_name='recruiting_opportunity')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='owned_recruiting_opportunities')
    criteria = models.ForeignKey('MatchCriteriaVersion', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    publication = models.ForeignKey('OpportunityPublication', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    publication_state = models.CharField(max_length=16, default='unpublished', choices=[('unpublished', 'Unpublished'), ('published', 'Published'), ('closed', 'Closed to new interest')])
    revision = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ['-updated_at', 'pk']
    def __str__(self):
        return str(self.need)


class OpportunityCollaboration(UUIDModel, TimestampedModel):
    opportunity = models.ForeignKey(RecruitingOpportunity, on_delete=models.PROTECT, related_name='collaborations')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    can_manage = models.BooleanField(default=False)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    revoked_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['opportunity', 'user'], condition=Q(revoked_at__isnull=True), name='recruiting_active_collaborator')]


class MatchCriteriaVersion(UUIDModel, TimestampedModel):
    opportunity = models.ForeignKey(RecruitingOpportunity, on_delete=models.PROTECT, related_name='criteria_versions')
    need_revision = models.ForeignKey('customers.WorkforceNeedRevision', on_delete=models.PROTECT, related_name='matching_criteria')
    number = models.PositiveIntegerField()
    rules = models.JSONField(default=list)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['opportunity', 'number'], name='recruiting_criteria_version')]
    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Matching criteria history is immutable. Create a new version.')
        return super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('Retain matching criteria history.')


class CandidateOpportunity(UUIDModel, TimestampedModel):
    opportunity = models.ForeignKey(RecruitingOpportunity, on_delete=models.PROTECT, related_name='candidates')
    participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='recruiting_candidates')
    source_participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='+')
    selected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assigned_recruiting_candidates')
    stage = models.ForeignKey(RecruitingStage, on_delete=models.PROTECT, default='identified')
    active = models.BooleanField(default=True)
    outcome = models.CharField(max_length=24, blank=True)
    revision = models.PositiveIntegerField(default=0)
    selection_criteria = models.ForeignKey(MatchCriteriaVersion, null=True, blank=True, on_delete=models.PROTECT)
    selected_need_revision = models.ForeignKey('customers.WorkforceNeedRevision', on_delete=models.PROTECT, related_name='selected_recruiting_candidates')
    selection_rationale = models.CharField(max_length=500)
    merged_into = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='historical_alias_records')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['opportunity', 'participant'], condition=Q(active=True), name='recruiting_active_candidate_position'),
            models.CheckConstraint(condition=Q(active=True,outcome='') | Q(active=False,outcome__in=['hired','not_selected','withdrawn','identity_collision']), name='recruiting_candidate_outcome_state')]


class OpportunityPublication(UUIDModel, TimestampedModel):
    opportunity = models.ForeignKey(RecruitingOpportunity, on_delete=models.PROTECT, related_name='publications')
    need_revision = models.ForeignKey('customers.WorkforceNeedRevision', on_delete=models.PROTECT)
    number = models.PositiveIntegerField()
    safe_details = models.JSONField(default=dict)
    expires_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['opportunity', 'number'], name='recruiting_publication_version')]
    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Job publication history is immutable. Publish a new reviewed version.')
        return super().save(*args, **kwargs)


class CandidateInterest(UUIDModel, TimestampedModel):
    opportunity = models.ForeignKey(RecruitingOpportunity, on_delete=models.PROTECT, related_name='interests')
    publication = models.ForeignKey(OpportunityPublication, on_delete=models.PROTECT, related_name='interests')
    participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='job_interests')
    source_participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='+')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    source = models.CharField(max_length=24, default='participant_portal', editable=False)
    active = models.BooleanField(default=True)
    status = models.CharField(max_length=24, default='received', choices=[('received', 'Interest Received'), ('review', 'Under Review'), ('information', 'Additional Information Needed'), ('not_selected', 'No Longer Under Consideration'), ('withdrawn', 'Withdrawn')])
    revision = models.PositiveIntegerField(default=0)
    candidate = models.ForeignKey(CandidateOpportunity, null=True, blank=True, on_delete=models.PROTECT, related_name='originating_interests')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['opportunity', 'participant'], condition=Q(active=True), name='recruiting_active_participant_interest'),
            models.CheckConstraint(condition=Q(status__in=['received','review','information','not_selected','withdrawn']), name='recruiting_interest_status_valid')]


class ConsentWithdrawal(UUIDModel, TimestampedModel):
    """Restriction event over existing consent evidence; never a second acceptance form."""
    participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='employer_sharing_withdrawals')
    acceptance = models.ForeignKey('participants.ConsentAcceptance', null=True, blank=True, on_delete=models.PROTECT, related_name='recruiting_withdrawals')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500)


class CandidateOutreach(UUIDModel, TimestampedModel):
    candidate = models.ForeignKey(CandidateOpportunity, on_delete=models.PROTECT, related_name='outreach')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    method = models.CharField(max_length=16, choices=[('phone','Phone'),('email','Email'),('text','Text'),('in_person','In person'),('other','Other')])
    contacted_at = models.DateTimeField()
    outcome = models.CharField(max_length=160)
    response = models.CharField(max_length=500, blank=True)
    notes = models.TextField(blank=True, max_length=3000)
    task = models.ForeignKey('operations.Task', null=True, blank=True, on_delete=models.PROTECT)
    operation_id = models.UUIDField(unique=True)


class InterestReview(UUIDModel, TimestampedModel):
    interest = models.ForeignKey(CandidateInterest, on_delete=models.PROTECT, related_name='reviews')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=24)
    note = models.CharField(max_length=1500)
    task = models.ForeignKey('operations.Task', null=True, blank=True, on_delete=models.PROTECT)
    candidate = models.ForeignKey(CandidateOpportunity, null=True, blank=True, on_delete=models.PROTECT)
    operation_id = models.UUIDField(unique=True)


class RecruitingTaskLink(UUIDModel, TimestampedModel):
    task = models.ForeignKey('operations.Task', on_delete=models.PROTECT, related_name='recruiting_links')
    opportunity = models.ForeignKey(RecruitingOpportunity, on_delete=models.PROTECT, related_name='task_links')
    candidate = models.ForeignKey(CandidateOpportunity, null=True, blank=True, on_delete=models.PROTECT, related_name='task_links')
    interest = models.ForeignKey(CandidateInterest, null=True, blank=True, on_delete=models.PROTECT, related_name='task_links')
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['task','opportunity'], name='recruiting_task_opportunity')]


class CandidateDisclosure(UUIDModel, TimestampedModel):
    candidate = models.ForeignKey(CandidateOpportunity, on_delete=models.PROTECT, related_name='disclosures')
    participant = models.ForeignKey('participants.Participant', on_delete=models.PROTECT, related_name='+')
    organization = models.ForeignKey('customers.CustomerOrganization', on_delete=models.PROTECT, related_name='candidate_disclosures')
    need = models.ForeignKey('customers.WorkforceNeed', on_delete=models.PROTECT, related_name='candidate_disclosures')
    need_revision = models.ForeignKey('customers.WorkforceNeedRevision', on_delete=models.PROTECT, related_name='+')
    workforce_request = models.ForeignKey(WorkforceRequest, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    acceptance = models.ForeignKey('participants.ConsentAcceptance', on_delete=models.PROTECT, related_name='candidate_disclosures')
    consent_hash = models.CharField(max_length=64)
    categories = models.JSONField(default=list)
    approved_projection = models.JSONField(default=dict)
    projection_fingerprint = models.CharField(max_length=64)
    purpose = models.CharField(max_length=500)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    released_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    revocation_reason = models.CharField(max_length=500, blank=True)
    revision = models.PositiveIntegerField(default=0)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['candidate','organization','need'],condition=Q(revoked_at__isnull=True),name='recruiting_current_disclosure')]
    def save(self,*args,**kwargs):
        from django.core.exceptions import ValidationError
        if self.pk:
            old=type(self).objects.filter(pk=self.pk).first()
            protected=('candidate_id','participant_id','organization_id','need_id','need_revision_id','workforce_request_id','acceptance_id','consent_hash','categories','approved_projection','projection_fingerprint','purpose','released_by_id','released_at')
            if old and any(getattr(old,k)!=getattr(self,k) for k in protected):raise ValidationError('Disclosure approval is immutable. Revoke and explicitly approve a new release.')
        return super().save(*args,**kwargs)


class DisclosureMemberPermission(UUIDModel, TimestampedModel):
    disclosure = models.ForeignKey(CandidateDisclosure, on_delete=models.PROTECT, related_name='member_permissions')
    membership = models.ForeignKey('customers.CustomerMembership', on_delete=models.PROTECT, related_name='+')
    can_respond = models.BooleanField(default=False)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500)
    revision = models.PositiveIntegerField(default=0)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['disclosure','membership'],name='recruiting_disclosure_member')]


class EmployerCandidateFeedback(UUIDModel, TimestampedModel):
    disclosure = models.ForeignKey(CandidateDisclosure, on_delete=models.PROTECT, related_name='feedback')
    membership = models.ForeignKey('customers.CustomerMembership', on_delete=models.PROTECT, related_name='+')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=24,choices=[('interested','Interested'),('interview','Request Interview'),('not_interested','Not Interested'),('report_hired','Report Hired'),('feedback','Feedback')])
    message = models.CharField(max_length=2000,blank=True)
    operation_id = models.UUIDField(unique=True)
    payload_fingerprint = models.CharField(max_length=64)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    confirmed_at = models.DateTimeField(null=True,blank=True)
    confirmation_reason = models.CharField(max_length=500,blank=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(kind__in=['interested','interview','not_interested','report_hired','feedback']),name='recruiting_employer_feedback_kind')]
    def save(self,*args,**kwargs):
        from django.core.exceptions import ValidationError
        if self.pk:
            old=type(self).objects.filter(pk=self.pk).first()
            if old and any(getattr(old,k)!=getattr(self,k) for k in ('disclosure_id','membership_id','actor_id','kind','message','operation_id','payload_fingerprint')):
                raise ValidationError('Retain the original employer response. Record a new attributed response instead.')
        return super().save(*args,**kwargs)
