"""Attributable recruiting extensions; no replacement participant/intake database."""
import hashlib
import json
from datetime import date, timedelta
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.utils import timezone
from apps.accounts.roles import manager
from apps.core.audit import record_audit
from apps.participants.models import Participant, ParticipantCareerProfile, RegistrationSubmission, ConsentAcceptance
from apps.participants.identity import canonical_id, identity_members, fresh_identity_context
from apps.participants.workspace_models import SavedParticipantView
from .access import fresh_staff, fresh_participant, policy_transaction, person, reason
from .models import (RecruitingProfile, Classification, ProfileConfirmation, ProfileUpdateRequest,
    CandidateList, CandidateListEntry, CampaignSource, CampaignRecruitingPolicy, IdentityConsolidation,
    WorkforceRequest, WorkforceRequestPosition)


def audit(actor, action, instance, *, data=None, why='', before=None):
    return record_audit(actor=actor, action='recruiting.' + action, instance=instance,
        source='build15_recruiting', old_data=before, new_data=data or {}, reason=why)


def profile_fingerprint(item):
    career = ParticipantCareerProfile.objects.filter(participant=item).first()
    personal = ('first_name', 'last_name', 'preferred_name', 'contact_email', 'contact_phone', 'city', 'state', 'postal_code')
    career_fields = ('career_interests', 'skills_interests', 'years_of_experience', 'work_availability',
        'willing_to_travel', 'preferred_locations', 'preferred_work_type', 'employment_status', 'employment_interest', 'education_training')
    payload = {'person': {k: getattr(item, k) for k in personal},
        'career': {k: getattr(career, k) for k in career_fields} if career else {}}
    extra = RecruitingProfile.objects.filter(participant=item).first()
    payload['recruiting'] = {k: getattr(extra, k) if extra else None for k in ('employment_classification_id', 'availability_classification_id', 'willing_to_relocate')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def readiness(item):
    """Revalidate recorded trusted intake. Profile coverage and portal activation are not intake."""
    from apps.participants.portal_accounts import identity_clear
    from apps.participants.intake_contracts import validate_answers
    from apps.participants.intake_lifecycle import verify_published
    if item.kind != 'person' or item.archived_at:
        return {'eligible': False, 'issues': ['Only an active person can enter recruiting. Historical records remain retained.'], 'submission': None}
    item = person(item.pk)
    issues = []
    if not identity_clear(item):
        issues.append('Identity review is required.')
    from apps.events.models import ParticipantIdentityLink
    for member_id in identity_members(item.pk):
        linked = ParticipantIdentityLink.objects.filter(source_participant_id=member_id, is_active=True, review__status='resolved')
        if linked.exists() and canonical_id(member_id) == member_id:
            issues.append('Conflicting or circular reviewed identity links require review.')
            break
    submission = RegistrationSubmission.objects.filter(participant_id__in=identity_members(item.pk),
        status='submitted', intake_operation_id__isnull=False).select_related('form_version__form').order_by('-submitted_at', '-pk').first()
    if not submission:
        issues.append('Completed required intake is not recorded.')
    else:
        try:
            version = verify_published(submission.form_version, allow_retired=True)
            metadata = submission.intake_metadata
            if metadata.get('contract_hash') != version.content_hash:
                raise ValidationError('Pinned intake contract does not match.')
            occurred_on = date.fromisoformat(metadata['occurred_on'])
            validate_answers(version.snapshot, submission.answers, occurred_on=occurred_on)
            if not ConsentAcceptance.objects.filter(submission=submission, participant_id__in=identity_members(item.pk)).exists():
                raise ValidationError('Required intake consent evidence is missing.')
        except (ValidationError, KeyError, TypeError, ValueError):
            issues.append('Recorded intake requires integrity or completion review.')
    return {'eligible': not issues, 'issues': issues, 'submission': submission}


def freshness(item):
    row = RecruitingProfile.objects.filter(participant=item).first()
    pending = ProfileUpdateRequest.objects.filter(participant_id__in=identity_members(item.pk), fulfilled_by__isnull=True).exists()
    return {'last_relevant_update': row.last_relevant_update_at if row else None,
        'last_confirmed': row.last_confirmed_at if row else None,
        'needs_confirmation': pending or not row or not row.last_confirmed_at or row.confirmed_fingerprint != profile_fingerprint(item),
        'requested': pending}


def record_lead(*, actor, participant, source):
    # Called inside the existing staff creation transaction after its duplicate guard.
    row, _ = RecruitingProfile.objects.get_or_create(participant=participant)
    row.is_lead, row.lead_source, row.recorded_by = True, reason(source), actor
    row.last_relevant_update_at = timezone.now()
    row.save()
    audit(actor, 'lead.recorded', row, data={'participant': participant.pk, 'source': row.lead_source})
    return row


def save_preferences(*, actor, participant_id, data, expected_revision, why, participant_owned=False):
    with policy_transaction():
        if participant_owned:
            actor, account = fresh_participant(actor, lock=True)
            if not account or account.participant_id != int(participant_id):
                raise PermissionDenied
            staff = actor
        else:
            staff = fresh_staff(actor)
        item = person(participant_id, lock=True)
        row, _ = RecruitingProfile.objects.get_or_create(participant=item)
        if row.revision != expected_revision:
            raise ValidationError('These recruiting preferences changed. Reload and review them.')
        # The rendered contract includes career facts too. Check before any write.
        extended = {'willing_to_travel', 'employment_interest'} & data.keys()
        if extended and data.get('fingerprint') != profile_fingerprint(item):
            raise ValidationError('Your profile changed. Reload, review the current values and try again. Your entered answers are retained here.')
        before = {'employment_classification': str(row.employment_classification_id or ''),
            'availability_classification': str(row.availability_classification_id or ''),
            'willing_to_relocate': row.willing_to_relocate}
        for field, kind in [('employment_classification', 'employment'), ('availability_classification', 'availability')]:
            choice = data.get(field)
            if choice:
                choice = Classification.objects.filter(pk=choice.pk).first()
                if not choice:
                    raise ValidationError('This classification is no longer available. Review the current choices.')
            if choice and (choice.kind != kind or (not choice.is_active and getattr(row, field + '_id') != choice.pk)):
                raise ValidationError('Choose an active classification of the correct type.')
            setattr(row, field, choice)
        if extended:
            from apps.participants.identity import update_participant_profile
            career = ParticipantCareerProfile.objects.select_for_update().filter(participant=item).first()
            updates = {key: data[key] for key in extended}
            if 'employment_interest' in updates:
                valid = dict(ParticipantCareerProfile._meta.get_field('employment_interest').choices)
                if updates['employment_interest'] not in valid:
                    raise ValidationError('Choose a recorded employment-interest option.')
            if 'willing_to_travel' in updates and updates['willing_to_travel'] not in (True, False, None):
                raise ValidationError('Choose Yes, No or Not recorded for travel.')
            before.update({key: getattr(career, key) if career else None for key in extended})
            update_participant_profile(participant=item, updates=updates)
        row.willing_to_relocate = data.get('willing_to_relocate')
        row.revision += 1
        row.last_relevant_update_at = timezone.now()
        row.save()
        audit(staff, 'preferences.updated', row, data={'participant': item.pk, 'revision': row.revision, 'employment_classification': str(row.employment_classification_id or ''), 'availability_classification': str(row.availability_classification_id or ''), 'willing_to_relocate': row.willing_to_relocate, **{key: data[key] for key in extended}}, before=before, why=reason(why))
        return row


def confirm_profile(*, actor, participant_id, expected_fingerprint, why='', participant_owned=False):
    with policy_transaction():
        if participant_owned:
            actor, account = fresh_participant(actor, lock=True)
            if not account or account.participant_id != int(participant_id):
                raise PermissionDenied
        else:
            actor = fresh_staff(actor)
            why = reason(why)
        item = person(participant_id, lock=True)
        fingerprint = profile_fingerprint(item)
        if fingerprint != expected_fingerprint:
            raise ValidationError('The profile changed. Review its current information before confirming.')
        confirmation = ProfileConfirmation.objects.create(participant=item, actor=actor, fingerprint=fingerprint,
            source='participant' if participant_owned else 'staff_assisted', reason=why)
        row, _ = RecruitingProfile.objects.get_or_create(participant=item)
        row.last_confirmed_at = timezone.now()
        row.confirmed_fingerprint = fingerprint
        row.save(update_fields=['last_confirmed_at', 'confirmed_fingerprint', 'updated_at'])
        ProfileUpdateRequest.objects.filter(participant_id__in=identity_members(item.pk), fulfilled_by__isnull=True).update(fulfilled_by=confirmation)
        audit(actor, 'profile.confirmed', confirmation, data={'participant': item.pk, 'source': confirmation.source})
        return confirmation


def request_update(*, actor, participant_id, message, task=None):
    with policy_transaction():
        actor = fresh_staff(actor)
        item = person(participant_id, lock=True)
        if task:
            from apps.operations.task_access import tasks_for
            if not tasks_for(actor).filter(pk=task.pk).exists():
                raise PermissionDenied
        row = ProfileUpdateRequest.objects.create(participant=item, requested_by=actor, message=reason(message), task=task)
        audit(actor, 'profile.update_requested', row, data={'participant': item.pk})
        return row


def visible_lists(actor):
    actor = fresh_staff(actor)
    return CandidateList.objects.filter(Q(owner=actor) | Q(shared=True))


def save_list(*, actor, name, shared=False, object_id=None, expected_revision=0, archive=False):
    with policy_transaction():
        actor = fresh_staff(actor)
        row = CandidateList.objects.select_for_update().get(pk=object_id) if object_id else CandidateList(owner=actor)
        if object_id and not (row.owner_id == actor.pk or manager(actor)):
            raise PermissionDenied
        if row.revision != expected_revision:
            raise ValidationError('This candidate list changed. Reload it.')
        row.name, row.shared = reason(name)[:120], shared
        row.archived_at = timezone.now() if archive else None
        row.revision += 1
        row.full_clean(); row.save()
        audit(actor, 'list.saved', row, data={'name': row.name, 'shared': row.shared, 'archived': bool(row.archived_at)})
        return row


def change_list_member(*, actor, list_id, participant_id, remove=False, why=''):
    with policy_transaction():
        actor = fresh_staff(actor)
        row = CandidateList.objects.select_for_update().get(pk=list_id, archived_at__isnull=True)
        if row.owner_id != actor.pk and not manager(actor):
            raise PermissionDenied('Shared lists may be read; their owner or Manager manages membership.')
        item = person(participant_id, lock=True)
        entry = row.entries.filter(participant=item, removed_at__isnull=True).first()
        if remove:
            if not entry:
                return None
            entry.removed_at, entry.removed_by, entry.removal_reason = timezone.now(), actor, reason(why)
            entry.save()
            audit(actor, 'list.member_removed', entry, why=entry.removal_reason)
        elif not entry:
            entry = CandidateListEntry.objects.create(candidate_list=row, participant=item,
                source_participant_id=participant_id, added_by=actor)
            audit(actor, 'list.member_added', entry, data={'participant': item.pk})
        return entry


def exclusive_sources(item, organization_id, *, at=None):
    at = at or timezone.now()
    blocked = []
    for source in CampaignSource.objects.filter(participant_id__in=identity_members(item.pk), exclusive=True,
            released_at__isnull=True).exclude(campaign__organization_id=organization_id).select_related('campaign__recruiting_policy'):
        policy = CampaignRecruitingPolicy.objects.filter(campaign=source.campaign).first()
        # No invented duration or planned-date completion. Missing setup stays visible and closed.
        if not policy or policy.completed_at is None or policy.exclusivity_days is None:
            blocked.append({'source': source, 'expires': None, 'setup_required': not policy or policy.exclusivity_days is None})
        else:
            expires = policy.completed_at + timedelta(days=policy.exclusivity_days)
            if at < expires:
                blocked.append({'source': source, 'expires': expires, 'setup_required': False})
    return blocked


def visible_campaigns(actor,*,write=False):
    from apps.customers.models import Campaign
    from .selection import opportunities_for
    from .models import OpportunityCollaboration
    actor=fresh_staff(actor)
    if manager(actor):return Campaign.objects.all()
    opportunities=opportunities_for(actor)
    if write:
        delegated=OpportunityCollaboration.objects.filter(user=actor,can_manage=True,revoked_at__isnull=True).values('opportunity_id')
        opportunities=opportunities.filter(Q(owner=actor)|Q(pk__in=delegated))
    return Campaign.objects.filter(workforce_needs__recruiting_opportunity__in=opportunities).distinct()


def source_campaign(*, actor, campaign_id, participant_id, exclusive=True,why=''):
    from apps.customers.models import Campaign
    with policy_transaction():
        actor = fresh_staff(actor)
        item = person(participant_id, lock=True)
        allowed=visible_campaigns(actor,write=True).order_by().values('pk')
        campaign = Campaign.objects.select_for_update().filter(pk=campaign_id,pk__in=allowed).first()
        if not campaign:raise PermissionDenied('This Campaign is outside your recruiting responsibility.')
        row, created = CampaignSource.objects.get_or_create(campaign=campaign, source_participant_id=participant_id,
            defaults={'participant': item, 'sourced_by': actor, 'exclusive': exclusive})
        if created:
            audit(actor, 'campaign.sourced', row, data={'participant': item.pk, 'exclusive': exclusive},why=reason(why) if why else 'Explicit staff-recorded Campaign sourcing.')
        return row


def configure_campaign(*, actor, campaign_id, days, expected_revision=0, actual_completion=None, why):
    from apps.customers.models import Campaign
    with policy_transaction():
        actor = fresh_staff(actor, administrator=True)
        campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
        row, _ = CampaignRecruitingPolicy.objects.get_or_create(campaign=campaign)
        if row.revision != expected_revision:
            raise ValidationError('Campaign recruiting policy changed. Reload it.')
        if actual_completion is not None:
            if campaign.status != 'completed' or actual_completion > timezone.now():
                raise ValidationError('Record an actual past completion only for a completed Campaign.')
            if row.completed_at is not None and actual_completion != row.completed_at:
                raise ValidationError('Actual completion is retained. Request an attributable correction review.')
            row.completed_at, row.completed_by = actual_completion, actor
        row.exclusivity_days = days
        row.revision += 1
        row.full_clean(); row.save()
        audit(actor, 'campaign.policy', row, data={'days': days, 'actual_completed_at': str(row.completed_at)}, why=reason(why))
        return row


def release_exclusivity(*, actor, source_id, why):
    with policy_transaction():
        actor = fresh_staff(actor, administrator=True)
        row = CampaignSource.objects.select_for_update().get(pk=source_id)
        if row.released_at:
            return row
        row.released_at, row.released_by, row.release_reason = timezone.now(), actor, reason(why)
        row.save()
        audit(actor, 'campaign.early_release', row, why=row.release_reason)
        return row


def consolidation_preview(source_id, target_id):
    from apps.events.models import ParticipantIdentityLink
    source, target = Participant.objects.get(pk=source_id), person(target_id)
    payload = {'source': source.pk, 'target': target.pk,
        'source_updated': source.updated_at.isoformat(), 'target_updated': target.updated_at.isoformat(),
        'links': list(ParticipantIdentityLink.objects.filter(Q(source_participant_id__in=[source.pk, target.pk]) |
            Q(candidate_participant_id__in=[source.pk, target.pk])).order_by('pk').values('pk', 'is_active', 'candidate_participant_id')),
        'source_has_portal': hasattr(source, 'account'),
        'source_values': {k: getattr(source, k) for k in ('first_name', 'last_name', 'contact_email', 'contact_phone', 'city', 'state')},
        'target_values': {k: getattr(target, k) for k in ('first_name', 'last_name', 'contact_email', 'contact_phone', 'city', 'state')}}
    if apps.is_installed('apps.recruiting'):
        try:
            cls = apps.get_model('recruiting', 'CandidateOpportunity')
            payload['recruiting'] = list(cls.objects.filter(participant_id__in=identity_members(source.pk) | identity_members(target.pk)).order_by('pk').values('pk', 'opportunity_id', 'revision', 'active'))
        except LookupError:
            payload['recruiting'] = []
    summaries=[];provenance={}
    catalogs=[('Event participation','events','EventRegistration'),('Qualifications','participants','ParticipantQualification'),
        ('Documents','participants','ParticipantDocument'),('Consent acceptances','participants','ConsentAcceptance'),
        ('Intake submissions','participants','RegistrationSubmission'),('Portal accounts','participants','ParticipantAccount'),
        ('Quiz attempts','assessments','QuizAttempt'),('Simulator captures','simulators','SimulatorCapture'),
        ('Candidate lists','recruiting','CandidateListEntry'),('Campaign sourcing','recruiting','CampaignSource'),
        ('Recruiting records','recruiting','CandidateOpportunity'),('Job interests','recruiting','CandidateInterest'),
        ('Employer releases','recruiting','CandidateDisclosure')]
    for label,app_name,model_name in catalogs:
        model=apps.get_model(app_name,model_name)
        fields={field.name for field in model._meta.fields}
        selected=['pk']+[field for field in ['updated_at','created_at','status','revision','is_active','active','accepted_at','completed_at','voided_at','revoked_at'] if field in fields]
        sides={name:list(model.objects.filter(participant_id__in=identity_members(item.pk)).order_by('pk').values(*selected)) for name,item in [('source',source),('target',target)]}
        summaries.append({'label':label,'source':len(sides['source']),'target':len(sides['target'])})
        provenance[label]=sides
    payload['history_summary']=summaries
    payload['history_provenance']=hashlib.sha256(json.dumps(provenance,sort_keys=True,default=str).encode()).hexdigest()
    payload['fingerprint'] = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return payload


def consolidate(*, actor, source_id, target_id, expected_fingerprint, confirmed, why):
    from apps.events.models import ParticipantMatchReview, ParticipantIdentityLink, ParticipantMatchDecision
    with policy_transaction():
        actor = fresh_staff(actor, administrator=True)
        why = reason(why)
        if not confirmed or int(source_id) == canonical_id(int(target_id)):
            raise ValidationError('Confirm two distinct reviewed identities.')
        list(Participant.objects.select_for_update().filter(pk__in=sorted({int(source_id), canonical_id(int(target_id))})).order_by('pk'))
        preview = consolidation_preview(source_id, target_id)
        if preview['fingerprint'] != expected_fingerprint:
            raise ValidationError('Identity facts or recruiting activity changed. Review the comparison again.')
        source, target = person(source_id, lock=True), person(target_id, lock=True)
        if source.pk != int(source_id) or source.pk == target.pk:
            raise ValidationError('The source identity has already been linked.')
        if preview['source_has_portal']:
            raise ValidationError('The source has an established portal account. Keep credentials separate and request account-ownership review; choose a survivor without transferring an account.')
        review = ParticipantMatchReview.objects.create(source_participant=source, candidate_participant=target,
            established_person_review=True, evidence={'policy': 'staff_confirmed_consolidation', 'fingerprint': expected_fingerprint},
            status='resolved', reviewed_by=actor, reviewed_at=timezone.now(), resolution_note=why[:240], created_at=timezone.now(), updated_at=timezone.now())
        ParticipantIdentityLink.objects.create(review=review, source_participant=source, candidate_participant=target)
        ParticipantMatchDecision.objects.create(review=review, action='same', actor=actor, reason=why,
            source_identity=source.identity_uuid, candidate_identity=target.identity_uuid)
        collision_history = []
        with fresh_identity_context():
            for entry in CandidateListEntry.objects.filter(participant=source, removed_at__isnull=True):
                if CandidateListEntry.objects.filter(candidate_list=entry.candidate_list, participant=target, removed_at__isnull=True).exists():
                    entry.removed_at, entry.removed_by = timezone.now(), actor
                    entry.removal_reason = 'Reviewed identity consolidation; membership retained on canonical person.'
                    collision_history.append({'list_entry': str(entry.pk), 'action': 'retained_historical_membership'})
                else:
                    entry.participant = target
                entry.save()
                if not entry.removed_at:
                    collision_history.append({'list_entry': str(entry.pk), 'action': 'canonical_reference_changed'})
            from .selection import reconcile_consolidation
            collision_history += reconcile_consolidation(source, target, actor)
        row = IdentityConsolidation.objects.create(review=review, source=source, target=target, actor=actor,
            reason=why, fingerprint=expected_fingerprint, collision_history=collision_history)
        audit(actor, 'identity.consolidated', row, data={'source': source.pk, 'target': target.pk, 'history_preserved': True}, why=why)
        return row


def reverse_consolidation(*, actor, consolidation_id, why):
    from apps.events.models import ParticipantIdentityLink, ParticipantMatchDecision
    with policy_transaction():
        actor = fresh_staff(actor, administrator=True)
        row = IdentityConsolidation.objects.select_for_update().select_related('review', 'source', 'target').get(pk=consolidation_id)
        why = reason(why)
        if row.reversed_at:
            return row
        # Splitting later shared evidence/decisions is not guessed. Retain an actionable review.
        from .models import CandidateOpportunity, CandidateInterest
        later_recruiting=CandidateOpportunity.objects.filter(participant=row.target,updated_at__gt=row.created_at).exists() or CandidateInterest.objects.filter(participant=row.target,updated_at__gt=row.created_at).exists()
        if row.collision_history or later_recruiting or row.target.updated_at > row.created_at or CandidateListEntry.objects.filter(participant=row.target, updated_at__gt=row.created_at).exists():
            row.reversal_review_required, row.reversal_note = True, why
            row.save()
            audit(actor, 'identity.reversal_review_required', row, why=why)
            return row
        link = ParticipantIdentityLink.objects.select_for_update().get(review=row.review)
        link.is_active, link.reversed_at, link.reversed_by = False, timezone.now(), actor
        link.save()
        row.review.status = 'rejected'; row.review.save(update_fields=['status'])
        row.reversed_at = timezone.now(); row.save()
        ParticipantMatchDecision.objects.create(review=row.review, action='reverse', actor=actor, reason=why,
            source_identity=row.source.identity_uuid, candidate_identity=row.target.identity_uuid)
        audit(actor, 'identity.reversed', row, why=why)
        return row


def group_positions(*, actor, organization_id, name, need_ids, request_id=None, expected_revision=0):
    from apps.customers.models import CustomerOrganization, WorkforceNeed
    with policy_transaction():
        actor = fresh_staff(actor)
        if not manager(actor):raise PermissionDenied('A Manager groups employer positions.')
        organization = CustomerOrganization.objects.select_for_update().get(pk=organization_id)
        row = WorkforceRequest.objects.select_for_update().get(pk=request_id, organization=organization) if request_id else WorkforceRequest(organization=organization, created_by=actor)
        if row.revision != expected_revision:
            raise ValidationError('This workforce request changed. Reload it.')
        needs = list(WorkforceNeed.objects.select_for_update().filter(pk__in=need_ids, organization=organization).order_by('pk'))
        if len(needs) != len(set(str(pk) for pk in need_ids)):
            raise ValidationError('All positions must belong to this company.')
        if WorkforceRequestPosition.objects.filter(need__in=needs).exclude(request=row if row.pk else None).exists():
            raise ValidationError('A position already belongs to a workforce request.')
        row.name, row.revision = reason(name)[:160], row.revision + 1
        row.full_clean(); row.save()
        for need in needs:
            WorkforceRequestPosition.objects.get_or_create(need=need, defaults={'request': row, 'added_by': actor})
        audit(actor, 'request.positions_grouped', row, data={'positions': [str(n.pk) for n in needs]})
        return row
