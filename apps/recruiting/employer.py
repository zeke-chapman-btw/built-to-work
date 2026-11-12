"""Explicit scoped releases; Customer templates receive allowlisted dictionaries only."""
from apps.core.scores import whole_percent
import hashlib,json
from django.core.exceptions import ValidationError,PermissionDenied,ObjectDoesNotExist
from django.utils import timezone
from apps.participants.identity import identity_members,canonical_id
from apps.participants.models import ParticipantCareerProfile,ParticipantQualification
from apps.participants.verification_services import qualification_verification_summary
from apps.customers.portal_policy import require_membership
from .access import fresh_staff,policy_transaction,person,reason
from .foundation import audit
from .selection import applicable_consent,candidate_gates,opportunity_for
from .workflow import candidate_for,current_revision,transition
from .models import (CandidateDisclosure,DisclosureMemberPermission,EmployerCandidateFeedback,
    WorkforceRequestPosition,ConsentWithdrawal,CandidateOpportunity)

CATEGORIES=[('identity','Professional name'),('summary','Professional summary and skills'),
    ('region','General city/state'),('experience','Reported overall experience'),
    ('qualifications','Qualification names and effective verification'),('assessments','Accepted official assessment summaries')]


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def staff_projection(actor,projection):
    actor=fresh_staff(actor)
    data=json.loads(json.dumps(projection))
    if not actor.has_perm('participants.view_participantqualification'):data.pop('qualifications',None)
    if not actor.has_perms(['assessments.view_quizattempt','simulators.view_simulatorcapture']):data.pop('assessments',None)
    return data


def origin_withdrawn(candidate):
    latest=candidate.originating_interests.order_by('-created_at','-pk').first()
    return bool(latest and latest.status=='withdrawn')


def build_projection(actor,item,categories):
    actor=fresh_staff(actor)
    if not categories or set(categories)-dict(CATEGORIES).keys():raise ValidationError('Approve specific information categories.')
    career=ParticipantCareerProfile.objects.filter(participant=item).first()
    data={}
    if 'identity' in categories:data['name']=' '.join(filter(None,[item.preferred_name or item.first_name,item.last_name]))
    if 'summary' in categories:data['summary']={'interests':career.career_interests if career else '', 'skills':career.skills_interests if career else ''}
    if 'region' in categories:data['region']=', '.join(filter(None,[item.city,item.state]))
    if 'experience' in categories:data['experience']=str(career.years_of_experience)+' years (self-reported overall)' if career and career.years_of_experience is not None else 'Not recorded'
    if 'qualifications' in categories:
        if not actor.has_perm('participants.view_participantqualification'):raise PermissionDenied('Qualification release requires current evidence permission.')
        data['qualifications']=[]
        rows=ParticipantQualification.objects.filter(participant_id__in=identity_members(item.pk),is_active=True).select_related('catalog_entry').prefetch_related('verification_records__reviewer','verification_records__document','verification_records__evidence_document').order_by('pk')
        for row in rows:
            status=qualification_verification_summary(row)
            data['qualifications'].append({'name':str(row),'verification':status['label']})
    if 'assessments' in categories:
        from apps.event_reports.activity import activity_sources
        if not actor.has_perms(['assessments.view_quizattempt','simulators.view_simulatorcapture']):raise PermissionDenied('Assessment release requires current official-results permissions.')
        source=activity_sources();ids=identity_members(item.pk);results=[]
        for attempt in source['finished_quizzes'].filter(participant_id__in=ids).prefetch_related('sections__responses','sections__category').order_by('pk'):
            sections=list(attempt.sections.all())
            if len(sections)!=2 or any(s.status!='complete' or not s.completed_at or not s.questions_snapshot for s in sections):continue
            for section in sections:
                count=section.correct_count;total=len(section.questions_snapshot)
                if not 0<=count<=total:continue
                results.append({'type':'Quiz','label':section.category_name_snapshot or section.category.name,
                    'result':f'{count}/{total} correct','version':digest(section.questions_snapshot)[:12],
                    'date':attempt.completed_at.isoformat()})
        for capture in source['captures'].filter(participant_id__in=ids,total_score__isnull=False,total_score__gte=0,total_score__lte=100).select_related('profile').order_by('pk'):
            if not capture.profile_id or not capture.profile_version:continue
            results.append({'type':'Simulator','label':capture.profile.name,'result':whole_percent(capture.total_score),
                'version':capture.profile_version,'date':capture.captured_at.isoformat()})
        data['assessments']=results
    return data


def revoke(*,actor,disclosure_id,expected_revision,why):
    with policy_transaction():
        actor=fresh_staff(actor);row=CandidateDisclosure.objects.select_for_update().get(pk=disclosure_id)
        candidate_for(actor,row.candidate_id,write=True);current_revision(row,expected_revision)
        if row.revoked_at:return row
        row.revoked_at=timezone.now();row.revoked_by=actor;row.revocation_reason=reason(why);row.revision+=1;row.save()
        audit(actor,'disclosure.revoked',row,data={'organization':str(row.organization_id),'position':str(row.need_id)},why=why)
        return row


def release(*,actor,candidate_id,categories,purpose,expected_revision,expected_need_revision,expected_projection,confirmed):
    with policy_transaction():
        actor=fresh_staff(actor);candidate=candidate_for(actor,candidate_id,write=True,lock=True);current_revision(candidate,expected_revision)
        item=person(candidate.participant_id,lock=True)
        if not candidate.active or candidate.merged_into_id:raise ValidationError('Approve an active, current candidate-position record.')
        if origin_withdrawn(candidate):raise ValidationError('The participant withdrew the originating interest. A renewed interest and staff review are required before another employer release.')
        opportunity=opportunity_for(actor,candidate.opportunity_id,write=True,lock=True)
        need=opportunity.need
        if str(need.effective_revision_id)!=str(expected_need_revision):raise ValidationError('The approved position changed. Review the current position before employer release.')
        if need.status!='active' or not need.effective_revision_id or need.effective_revision.state!='approved':raise ValidationError('Employer disclosure requires an approved active position.')
        if not confirmed:raise ValidationError('Explicitly confirm the reviewed employer-facing information.')
        problems=candidate_gates(item,need.organization_id)
        if problems:raise ValidationError(problems)
        organization=need.organization
        if organization.status!='active' or not organization.provisioned:raise ValidationError('The employer must have an active provisioned Customer organization.')
        if organization.access_plan.candidate_matching is False:raise ValidationError('The employer Access & Plan explicitly disables candidate matching. Resolve that configuration before disclosure.')
        acceptance=applicable_consent(item);projection=build_projection(actor,item,categories);purpose=reason(purpose)
        if digest(projection)!=expected_projection:raise ValidationError('Review the current employer-facing preview before approving this release. The selected categories or underlying information changed.')
        previous=list(CandidateDisclosure.objects.select_for_update().filter(candidate=candidate,revoked_at__isnull=True))
        if len(previous)==1:
            prior=previous[0]
            if prior.categories==sorted(set(categories)) and prior.purpose==purpose and prior.projection_fingerprint==expected_projection and valid_disclosure(grant_query().get(pk=prior.pk)):
                return prior
        for row in previous:revoke(actor=actor,disclosure_id=row.pk,expected_revision=row.revision,why='Superseded by a newly reviewed BTW disclosure approval.')
        grouping=WorkforceRequestPosition.objects.filter(need=need).first()
        row=CandidateDisclosure.objects.create(candidate=candidate,participant=item,organization=organization,need=need,
            need_revision=need.effective_revision,workforce_request=grouping.request if grouping else None,
            acceptance=acceptance,consent_hash=acceptance.metadata['content_hash'],categories=sorted(set(categories)),
            approved_projection=projection,projection_fingerprint=digest(projection),purpose=purpose,released_by=actor,released_at=timezone.now())
        audit(actor,'disclosure.approved',row,data={'organization':str(organization.pk),'position':str(need.pk),'candidate':str(candidate.pk),
            'categories':row.categories,'existing_acceptance':str(acceptance.pk),'projection_fingerprint':row.projection_fingerprint},why=purpose)
        return row


def valid_disclosure(row):
    """Called inside the shared policy transaction on every Customer read/write."""
    if row.revoked_at or row.candidate.merged_into_id or row.candidate.outcome in {'identity_collision','withdrawn','not_selected'}:return False
    if origin_withdrawn(row.candidate):return False
    if row.candidate.opportunity.need_id!=row.need_id or row.need.organization_id!=row.organization_id:return False
    if row.workforce_request_id and (row.workforce_request.organization_id!=row.organization_id or not WorkforceRequestPosition.objects.filter(request_id=row.workforce_request_id,need_id=row.need_id).exists()):return False
    if row.candidate.participant_id!=row.participant_id or canonical_id(row.participant_id)!=row.participant_id:return False
    if row.need.status not in {'active','hold','closed'} or row.need.effective_revision_id!=row.need_revision_id or row.need_revision.state!='approved':return False
    try:
        if row.organization.access_plan.candidate_matching is False:return False
        item=person(row.participant_id)
        if candidate_gates(item,row.organization_id):return False
        acceptance=applicable_consent(item)
        if not acceptance or acceptance.pk!=row.acceptance_id or acceptance.metadata.get('content_hash')!=row.consent_hash:return False
        # New profile facts or changed/expired evidence require staff re-approval,
        # rather than silently disclosing unreviewed information or stale verification.
        projection=build_projection(row.released_by,item,row.categories)
        return digest(projection)==row.projection_fingerprint and digest(row.approved_projection)==row.projection_fingerprint
    except (PermissionDenied,ValidationError,ObjectDoesNotExist):return False


def grant_query():
    return CandidateDisclosure.objects.select_related('candidate__opportunity__need','participant','organization__access_plan','need__effective_revision','need_revision','acceptance','released_by','workforce_request')


def may_respond(member,row):
    return member.role=='lead' or DisclosureMemberPermission.objects.filter(disclosure=row,membership=member,can_respond=True).exists()


def customer_grant(actor,organization_id,disclosure_id,*,write=False):
    member=require_membership(actor,organization_id,lock=True)
    row=grant_query().select_for_update(of=('self',)).filter(pk=disclosure_id,organization_id=organization_id).first()
    if not row or not valid_disclosure(row):raise PermissionDenied('This candidate release is unavailable or requires BTW review.')
    if write and not may_respond(member,row):raise PermissionDenied('This membership has view-only candidate access.')
    return member,row


def safe_projection(row,member):
    latest = row.feedback.order_by('-created_at', '-pk').first()
    confirmed = row.feedback.filter(kind='report_hired', confirmed_at__isnull=False).exists()
    labels = {'interested': 'Company expressed interest', 'interview': 'Company requested an interview',
        'not_interested': 'Company reported not interested', 'report_hired': 'Company reported a hire · awaiting BTW confirmation',
        'feedback': 'Company feedback recorded'}
    progress = 'BTW confirmed the reported hire' if confirmed else labels.get(latest.kind, 'Company response recorded') if latest else 'Released for your review'
    return {'id':str(row.pk),'candidate':json.loads(json.dumps(row.approved_projection)),'position':row.need_revision.title,
        'request':row.workforce_request.name if row.workforce_request_id else '',
        'released_at':row.released_at,'can_respond':may_respond(member,row),
        'position_id':str(row.need_id),
        'progress':progress, 'latest_response':({'label':latest.get_kind_display(), 'date':latest.created_at} if latest else None),
        'btw_confirmed_hire':confirmed,
        'next_action':('Review and respond' if not latest else 'View or record a company response') if may_respond(member,row) else 'View candidate · response permission required'}


def list_candidates(actor,organization_id):
    with policy_transaction():
        member=require_membership(actor,organization_id,lock=True)
        rows=[]
        for row in grant_query().filter(organization_id=organization_id,revoked_at__isnull=True).order_by('-released_at','pk'):
            if valid_disclosure(row):rows.append(safe_projection(row,member))
        return rows


def detail(actor,organization_id,disclosure_id):
    with policy_transaction():
        member,row=customer_grant(actor,organization_id,disclosure_id)
        projection=safe_projection(row,member)
        projection['feedback']=[{'kind':event.get_kind_display(),'message':event.message,'date':event.created_at,
            'btw_confirmed':bool(event.confirmed_at)} for event in row.feedback.order_by('-created_at')[:30]]
        audit(member.identity.user,'disclosure.viewed',row,data={'organization':str(row.organization_id),'position':str(row.need_id),'membership':str(member.pk)})
        return projection


def feedback(*,actor,organization_id,disclosure_id,kind,message,operation_id):
    if kind not in dict(EmployerCandidateFeedback._meta.get_field('kind').choices):raise ValidationError('Choose a supported employer response.')
    message=(message or '').strip()
    if len(message)>2000:raise ValidationError('Keep feedback within 2,000 characters.')
    if kind=='feedback' and not message:raise ValidationError('Enter feedback for BTW.')
    with policy_transaction():
        member,row=customer_grant(actor,organization_id,disclosure_id,write=True)
        fingerprint=digest({'grant':str(row.pk),'membership':str(member.pk),'kind':kind,'message':message})
        prior=EmployerCandidateFeedback.objects.filter(operation_id=operation_id).first()
        if prior:
            if prior.payload_fingerprint!=fingerprint or prior.actor_id!=actor.pk:raise ValidationError('This response identifier was already used for a different action.')
            return prior
        event=EmployerCandidateFeedback.objects.create(disclosure=row,membership=member,actor=member.identity.user,
            kind=kind,message=message,operation_id=operation_id,payload_fingerprint=fingerprint)
        audit(member.identity.user,'employer.response',event,data={'disclosure':str(row.pk),'organization':str(row.organization_id),'kind':kind,'membership':str(member.pk),'official_stage_changed':False})
        return event


def member_permission(*,actor,disclosure_id,membership_id,can_respond,expected_revision,why):
    from apps.customers.models import CustomerMembership
    with policy_transaction():
        actor=fresh_staff(actor);row=CandidateDisclosure.objects.select_for_update().get(pk=disclosure_id)
        candidate_for(actor,row.candidate_id,write=True)
        if row.revoked_at:raise ValidationError('This disclosure is revoked.')
        membership=CustomerMembership.objects.select_for_update().filter(pk=membership_id,organization_id=row.organization_id,state='enabled',accepted_at__isnull=False).first()
        if not membership:raise PermissionDenied('Choose a current accepted membership in this employer organization.')
        permission=DisclosureMemberPermission.objects.select_for_update().filter(disclosure=row,membership=membership).first()
        if permission:current_revision(permission,expected_revision)
        elif expected_revision!=0:raise ValidationError('Reload current membership permissions.')
        if not permission:permission=DisclosureMemberPermission(disclosure=row,membership=membership)
        permission.can_respond=can_respond;permission.granted_by=actor;permission.reason=reason(why);permission.revision+=1;permission.save()
        audit(actor,'employer.membership_permission',permission,data={'disclosure':str(row.pk),'membership':str(membership.pk),'can_respond':can_respond},why=why)
        return permission


def confirm_hire(*,actor,feedback_id,expected_candidate_revision,why):
    with policy_transaction():
        actor=fresh_staff(actor);event=EmployerCandidateFeedback.objects.select_for_update().select_related('disclosure').get(pk=feedback_id)
        candidate=candidate_for(actor,event.disclosure.candidate_id,write=True,lock=True)
        if event.kind!='report_hired':raise ValidationError('Confirm an actual employer Report Hired response.')
        if event.confirmed_at:return candidate
        if not valid_disclosure(grant_query().get(pk=event.disclosure_id)):raise ValidationError('This release requires review before confirming the reported outcome.')
        if candidate.outcome=='hired':current_revision(candidate,expected_candidate_revision)
        else:candidate=transition(actor=actor,candidate_id=candidate.pk,stage_key='hired',expected_revision=expected_candidate_revision,why=why,confirm_hired=True)
        event.confirmed_by=actor;event.confirmed_at=timezone.now();event.confirmation_reason=reason(why);event.save()
        audit(actor,'employer.hire_confirmed',event,data={'candidate':str(candidate.pk),'official_outcome':'hired'},why=why)
        return candidate


def feedback_reviews():
    """Existing immutable audit events are the authoritative staff review record."""
    from apps.core.models import AuditLog
    from django.contrib.contenttypes.models import ContentType
    return AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(EmployerCandidateFeedback),
        action__in=['recruiting.employer.feedback_reviewed','recruiting.employer.hire_confirmed'])


def review_feedback(*,actor,feedback_id,why):
    with policy_transaction():
        actor=fresh_staff(actor)
        event=EmployerCandidateFeedback.objects.select_for_update().select_related('disclosure').get(pk=feedback_id)
        candidate_for(actor,event.disclosure.candidate_id,write=True)
        review=feedback_reviews().filter(object_id=str(event.pk)).first()
        if review:return review
        return audit(actor,'employer.feedback_reviewed',event,
            data={'candidate':str(event.disclosure.candidate_id),'kind':event.kind,'stage_changed':False},why=reason(why))


def record_withdrawal(*,actor,participant_id,why):
    with policy_transaction():
        actor=fresh_staff(actor,administrator=True);item=person(participant_id,lock=True)
        row=ConsentWithdrawal.objects.create(participant=item,acceptance=applicable_consent(item),actor=actor,reason=reason(why))
        for grant in CandidateDisclosure.objects.select_for_update().filter(participant_id__in=identity_members(item.pk),revoked_at__isnull=True):
            grant.revoked_at=timezone.now();grant.revoked_by=actor;grant.revocation_reason='Recorded sharing withdrawal.';grant.revision+=1;grant.save()
            audit(actor,'disclosure.withdrawal_revoked',grant,why=why)
        audit(actor,'consent.withdrawal_recorded',row,data={'participant':item.pk,'new_acceptance_created':False},why=why)
        return row


def revoke_for_identity(actor,candidate_ids):
    for grant in CandidateDisclosure.objects.select_for_update().filter(candidate_id__in=candidate_ids,revoked_at__isnull=True):
        grant.revoked_at=timezone.now();grant.revoked_by=actor;grant.revocation_reason='Reviewed identity changed; explicit BTW re-approval is required.';grant.revision+=1;grant.save()
        audit(actor,'disclosure.identity_revoked',grant,data={'candidate':str(grant.candidate_id)})


def revoke_for_interest(actor,candidate_id):
    for grant in CandidateDisclosure.objects.select_for_update().filter(candidate_id=candidate_id,revoked_at__isnull=True):
        grant.revoked_at=timezone.now();grant.revoked_by=actor;grant.revocation_reason='Participant withdrew the originating job interest; renewed interest and BTW review are required.';grant.revision+=1;grant.save()
        audit(actor,'disclosure.interest_withdrawn',grant,data={'candidate':str(candidate_id)})
