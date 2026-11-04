"""Candidate selection is an explicit staff decision with non-overridable policy gates."""
import hashlib
import json
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Max
from django.utils import timezone
from apps.accounts.roles import manager
from apps.participants.identity import identity_members, canonical_id
from apps.participants.models import Participant, ConsentAcceptance
from .access import fresh_staff, policy_transaction, person, reason
from .foundation import audit, readiness, exclusive_sources
from .models import (RecruitingOpportunity, OpportunityCollaboration, MatchCriteriaVersion,
    CandidateOpportunity, RecruitingStage, ConsentWithdrawal, CandidateInterest)
from .matching import validate_rules


def opportunities_for(actor):
    actor=fresh_staff(actor)
    qs=RecruitingOpportunity.objects.select_related('need__organization','need__effective_revision','owner','criteria')
    if manager(actor):return qs
    return qs.filter(Q(owner=actor)|Q(collaborations__user=actor,collaborations__revoked_at__isnull=True)).distinct()


def opportunity_for(actor,opportunity_id,*,write=False,lock=False):
    actor=fresh_staff(actor)
    query=opportunities_for(actor)
    if lock:query=query.select_for_update(of=('self',))
    item=query.filter(pk=opportunity_id,archived_at__isnull=True).first()
    if not item:raise PermissionDenied('This recruiting opportunity is unavailable to your account.')
    if write and not (manager(actor) or item.owner_id==actor.pk or OpportunityCollaboration.objects.filter(opportunity=item,user=actor,revoked_at__isnull=True,can_manage=True).exists()):
        raise PermissionDenied('This recruiting opportunity is view-only for your account.')
    return item


def create_opportunity(*,actor,need_id,owner_id):
    from django.contrib.auth import get_user_model
    from apps.customers.models import WorkforceNeed
    with policy_transaction():
        actor=fresh_staff(actor)
        if not manager(actor):raise PermissionDenied('A Manager sets up and delegates recruiting opportunities.')
        owner=fresh_staff(get_user_model().objects.get(pk=owner_id))
        need=WorkforceNeed.objects.select_for_update(of=('self',)).select_related('effective_revision').get(pk=need_id)
        if need.status!='active' or not need.effective_revision_id or need.effective_revision.state!='approved':
            raise ValidationError('Choose an approved active position.')
        item,created=RecruitingOpportunity.objects.get_or_create(need=need,defaults={'owner':owner})
        if created:audit(actor,'opportunity.created',item,data={'owner':str(owner.pk),'position':str(need.pk)})
        return item


def save_criteria(*,actor,opportunity_id,rules,expected_revision,why):
    rules=validate_rules(rules)
    with policy_transaction():
        actor=fresh_staff(actor)
        from apps.participants.qualification_models import QualificationCatalogEntry
        for rule in rules:
            if rule.get('catalog'):
                if not actor.has_perm('participants.view_participantqualification'):
                    raise PermissionDenied('Qualification criteria require evidence-view permission.')
                entry=QualificationCatalogEntry.objects.filter(stable_id=rule['catalog'],is_active=True).first()
                if not entry or (rule['kind']=='skill' and entry.kind!='skill') or (rule['kind']=='certification' and entry.kind not in {'certification','license'}):
                    raise ValidationError('Use an active catalog entry of the appropriate skill/credential kind.')
            if rule['kind']=='assessment':
                permission='assessments.view_quizattempt' if rule['assessment_type']=='quiz' else 'simulators.view_simulatorcapture'
                if not actor.has_perm(permission):raise PermissionDenied('Assessment criteria require permission to review that evidence.')
        item=opportunity_for(actor,opportunity_id,write=True,lock=True)
        if item.revision!=expected_revision:raise ValidationError('The opportunity changed. Reload the approved position and criteria.')
        need=item.need
        if need.status!='active' or not need.effective_revision_id or need.effective_revision.state!='approved':
            raise ValidationError('Matching requires an approved active position revision.')
        number=(item.criteria_versions.aggregate(n=Max('number'))['n'] or 0)+1
        version=MatchCriteriaVersion.objects.create(opportunity=item,need_revision=need.effective_revision,number=number,
            rules=rules,created_by=actor,reason=reason(why))
        item.criteria=version;item.revision+=1;item.save(update_fields=['criteria','revision','updated_at'])
        audit(actor,'criteria.versioned',version,data={'number':number,'approved_revision':str(need.effective_revision_id)},why=version.reason)
        return version


def applicable_consent(item):
    from apps.participants.intake_submission import _legal
    ids=identity_members(item.pk)
    last_withdrawal=ConsentWithdrawal.objects.filter(participant_id__in=ids).order_by('-created_at','-pk').first()
    for acceptance in ConsentAcceptance.objects.filter(participant_id__in=ids,submission__participant_id__in=ids,
            submission__status='submitted').select_related('document','submission').order_by('-accepted_at','-pk'):
        if last_withdrawal and acceptance.accepted_at<=last_withdrawal.created_at:
            continue
        recorded_hash=acceptance.metadata.get('content_hash')
        recorded_version=acceptance.metadata.get('document_version',acceptance.document.version)
        if not recorded_hash or recorded_version!=acceptance.document.version:
            continue
        try:_legal(acceptance.document,expected_hash=recorded_hash)
        except ValidationError:continue
        return acceptance
    return None


def candidate_gates(item,organization_id):
    problems=list(readiness(item)['issues'])
    if not applicable_consent(item):problems.append('Applicable recorded consent is missing, withdrawn or requires review. No acceptance is inferred.')
    if exclusive_sources(item,organization_id):problems.append('Campaign exclusivity restricts this employer. Review configuration, expiration or Administrator release.')
    return problems


def conflict_set(actor,item,*,excluding=None):
    records=CandidateOpportunity.objects.filter(participant_id__in=identity_members(item.pk),active=True).select_related('opportunity__need__organization','stage').order_by('pk')
    if excluding:records=records.exclude(opportunity_id=excluding)
    raw=[(str(r.pk),str(r.opportunity_id),r.revision,r.stage_id) for r in records]
    visible_ids=set(opportunities_for(actor).values_list('pk',flat=True))
    visible=[{'candidate':r,'organization':r.opportunity.need.organization.name,'stage':r.stage.label} for r in records if r.opportunity_id in visible_ids]
    return {'fingerprint':hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest(),
        'has_conflicts':bool(raw),'visible':visible,'restricted_activity':any(r.opportunity_id not in visible_ids for r in records)}


def select_candidate(*,actor,opportunity_id,participant_id,expected_conflicts,acknowledged,why,assignee_id=None,criteria_id=None):
    from django.contrib.auth import get_user_model
    with policy_transaction():
        actor=fresh_staff(actor)
        opportunity=opportunity_for(actor,opportunity_id,write=True,lock=True)
        item=person(participant_id,lock=True)
        # Canonicalization and the duplicate check share the same reviewed identity lock.
        existing=CandidateOpportunity.objects.filter(opportunity=opportunity,participant_id__in=identity_members(item.pk),active=True).first()
        if opportunity.need.status!='active' or not opportunity.need.effective_revision_id or opportunity.need.effective_revision.state!='approved':raise ValidationError('This position is no longer accepting recruiting candidates.')
        if criteria_id and str(opportunity.criteria_id)!=str(criteria_id):raise ValidationError('Matching criteria changed. Review the current criteria.')
        current_criteria=opportunity.criteria if opportunity.criteria_id and opportunity.criteria.need_revision_id==opportunity.need.effective_revision_id else None
        if criteria_id and not current_criteria:raise ValidationError('These matching criteria refer to a previous approved revision. Review the current position.')
        problems=candidate_gates(item,opportunity.need.organization_id)
        if problems:raise ValidationError(problems)
        if existing:return existing
        conflicts=conflict_set(actor,item,excluding=opportunity.pk)
        if conflicts['fingerprint']!=expected_conflicts or (conflicts['has_conflicts'] and not acknowledged):
            raise ValidationError('Recruiting activity changed or needs acknowledgement. Review current engagements before adding this candidate.')
        assignee=fresh_staff(get_user_model().objects.get(pk=assignee_id or opportunity.owner_id))
        if not opportunities_for(assignee).filter(pk=opportunity.pk).exists():raise ValidationError('Choose an assignee with access to this opportunity.')
        stage=RecruitingStage.objects.filter(pk='identified',is_active=True).first()
        if not stage:raise ValidationError('Configure an active initial recruiting stage before selection.')
        row=CandidateOpportunity.objects.create(opportunity=opportunity,participant=item,source_participant_id=participant_id,
            selected_by=actor,assignee=assignee,stage=stage,selection_criteria=current_criteria,
            selected_need_revision=opportunity.need.effective_revision,selection_rationale=reason(why))
        audit(actor,'candidate.selected',row,data={'opportunity':str(opportunity.pk),'participant':item.pk,
            'criteria':str(current_criteria.pk) if current_criteria else None,'approved_revision':str(opportunity.need.effective_revision_id),
            'acknowledged_conflicts':conflicts['has_conflicts']},why=row.selection_rationale)
        return row


def reconcile_consolidation(source,target,actor):
    """Called under the shared identity lock; preserve collisions as historical records."""
    result=[]
    for row in CandidateOpportunity.objects.select_for_update().filter(participant=source).order_by('pk'):
        winner=CandidateOpportunity.objects.filter(opportunity=row.opportunity,participant=target,active=True).first() if row.active else None
        if winner:
            row.active=False;row.outcome='identity_collision';row.merged_into=winner
            result.append({'candidate':str(row.pk),'retained_under':str(winner.pk),'action':'identity_collision'})
        else:
            row.participant=target
            result.append({'candidate':str(row.pk),'action':'canonical_reference_changed'})
        row.revision+=1;row.save()
        audit(actor,'candidate.identity_reconciled',row,data=result[-1])
    for interest in CandidateInterest.objects.select_for_update().filter(participant=source).order_by('pk'):
        if interest.active and CandidateInterest.objects.filter(opportunity=interest.opportunity,participant=target,active=True).exists():
            interest.active=False
        else:interest.participant=target
        interest.revision+=1;interest.save()
        result.append({'interest':str(interest.pk),'action':'history_retained'})
    return result
