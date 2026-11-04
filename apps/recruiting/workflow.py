"""Explicit staff workflow; Tasks and audit remain the established systems."""
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models import Max
from django.utils import timezone
from apps.accounts.roles import manager
from .access import fresh_staff, policy_transaction, reason, person
from .foundation import audit
from .selection import opportunity_for, opportunities_for, candidate_gates, select_candidate
from .models import (RecruitingStage, CandidateOpportunity, OpportunityCollaboration,
    OpportunityPublication, CandidateInterest, CandidateOutreach, InterestReview, RecruitingTaskLink)


def current_revision(item, expected):
    if item.revision != expected:raise ValidationError('This record changed. Reload it before saving.')


def candidate_for(actor, candidate_id, *, write=False, lock=False):
    actor=fresh_staff(actor)
    query=CandidateOpportunity.objects.select_related('participant','opportunity__need__organization','stage','assignee')
    if lock:query=query.select_for_update(of=('self',))
    row=query.get(pk=candidate_id)
    opportunity_for(actor,row.opportunity_id,write=write)
    return row


def transition(*,actor,candidate_id,stage_key,expected_revision,why,confirm_hired=False):
    with policy_transaction():
        actor=fresh_staff(actor)
        row=candidate_for(actor,candidate_id,write=True,lock=True)
        current_revision(row,expected_revision)
        stage=RecruitingStage.objects.select_for_update().filter(pk=stage_key,is_active=True).first()
        if not stage:raise ValidationError('Choose a currently active recruiting stage.')
        if not row.active:raise ValidationError('This recruiting record is historical; retain its outcome.')
        if row.stage_id==stage.key:return row
        if stage.semantic in {'active','hired'}:
            problems=candidate_gates(person(row.participant_id,lock=True),row.opportunity.need.organization_id)
            if problems:raise ValidationError(problems)
        if stage.semantic=='hired' and not confirm_hired:
            raise ValidationError('BTW must explicitly confirm the actual hiring outcome.')
        previous=row.stage_id
        row.stage=stage;row.revision+=1
        row.active=stage.semantic=='active';row.outcome='' if row.active else stage.semantic
        row.save()
        audit(actor,'candidate.stage_changed',row,data={'from':previous,'to':stage.key,'outcome':row.outcome,'btw_hiring_confirmation':stage.semantic=='hired'},why=reason(why))
        return row


def configure_stage(*,actor,key,label,position,is_active,expected_revision=None,why):
    from django.core.validators import validate_slug
    validate_slug(key)
    if not label.strip() or len(label)>100 or len(key)>80 or not 0<=position<=32767:raise ValidationError('Enter a stable key, label and valid order.')
    with policy_transaction():
        actor=fresh_staff(actor,administrator=True)
        row=RecruitingStage.objects.select_for_update().filter(pk=key).first()
        if row:current_revision(row,expected_revision)
        if key=='identified' and not is_active:raise ValidationError('The initial Identified stage must remain active.')
        if not row:row=RecruitingStage(key=key,semantic=key if key in {'hired','not_selected','withdrawn'} else 'active')
        row.label=label.strip();row.position=position;row.is_active=is_active;row.revision+=1;row.full_clean();row.save()
        audit(actor,'stage.configured',row,data={'key':key,'label':label,'position':position,'active':is_active,'semantic':row.semantic},why=reason(why))
        return row


def delegate(*,actor,opportunity_id,user_id,mode,expected_revision,why):
    with policy_transaction():
        actor=fresh_staff(actor)
        item=opportunity_for(actor,opportunity_id,write=True,lock=True);current_revision(item,expected_revision)
        if not (manager(actor) or item.owner_id==actor.pk):raise PermissionDenied('Only the owner or a Manager can delegate recruiting access.')
        user=fresh_staff(get_user_model().objects.get(pk=user_id))
        if mode=='owner':item.owner=user
        elif mode in {'view','manage','revoke'}:
            rows=OpportunityCollaboration.objects.select_for_update().filter(opportunity=item,user=user,revoked_at__isnull=True)
            rows.update(revoked_at=timezone.now())
            if mode!='revoke':OpportunityCollaboration.objects.create(opportunity=item,user=user,can_manage=mode=='manage',granted_by=actor)
        else:raise ValidationError('Choose owner, view, manage or revoke.')
        item.revision+=1;item.save()
        audit(actor,'opportunity.delegated',item,data={'user':str(user.pk),'mode':mode},why=reason(why))
        return item


def reassign(*,actor,candidate_id,user_id,expected_revision,why):
    with policy_transaction():
        actor=fresh_staff(actor);row=candidate_for(actor,candidate_id,write=True,lock=True);current_revision(row,expected_revision)
        user=fresh_staff(get_user_model().objects.get(pk=user_id))
        if not opportunities_for(user).filter(pk=row.opportunity_id).exists():raise ValidationError('Choose staff with current access to this opportunity.')
        old=row.assignee_id;row.assignee=user;row.revision+=1;row.save()
        audit(actor,'candidate.reassigned',row,data={'old':str(old),'new':str(user.pk)},why=reason(why))
        return row


def publish(*,actor,opportunity_id,expected_revision,action,details=None,expires_at=None,expected_need_revision=None,why):
    """Only reviewed allowlisted participant copy; no automatic private-term dump."""
    with policy_transaction():
        actor=fresh_staff(actor);item=opportunity_for(actor,opportunity_id,write=True,lock=True);current_revision(item,expected_revision)
        if action=='publish':
            revision=item.need.effective_revision
            if str(item.need.effective_revision_id)!=str(expected_need_revision):raise ValidationError('The approved position changed. Review it before publishing job details.')
            if item.need.status!='active' or not revision or revision.state!='approved':raise ValidationError('Publish only an approved active position.')
            if expires_at and expires_at<=timezone.now():raise ValidationError('Expiration must be in the future.')
            allowed={'title','trade','summary','location','employment_type','schedule','pay','qualifications','travel'}
            if not isinstance(details,dict) or set(details)-allowed:raise ValidationError('Use only reviewed participant-facing job fields.')
            clean={key:str(value).strip() for key,value in details.items()}
            if not clean.get('title') or not clean.get('summary') or any(len(v)>3000 for v in clean.values()):raise ValidationError('Provide a title, summary and concise approved details.')
            version=OpportunityPublication.objects.create(opportunity=item,need_revision=revision,
                number=(item.publications.aggregate(n=Max('number'))['n'] or 0)+1,
                safe_details=clean,expires_at=expires_at,published_by=actor)
            item.publication=version;item.publication_state='published'
        elif action in {'unpublish','close'}:item.publication_state='unpublished' if action=='unpublish' else 'closed'
        else:raise ValidationError('Choose publish, unpublish or close.')
        item.revision+=1;item.save()
        audit(actor,'job.'+action,item,data={'version':str(item.publication_id) if item.publication_id else None,'state':item.publication_state},why=reason(why))
        return item


def attach_task(*,actor,opportunity_id,task_id,candidate_id=None,interest_id=None):
    from apps.operations.models import Task
    from apps.operations.task_access import require_task_access
    with policy_transaction():
        actor=fresh_staff(actor);item=opportunity_for(actor,opportunity_id,write=True,lock=True)
        task=Task.objects.select_for_update().get(pk=task_id);require_task_access(actor,task)
        candidate=candidate_for(actor,candidate_id,write=True) if candidate_id else None
        interest=CandidateInterest.objects.get(pk=interest_id,opportunity=item) if interest_id else None
        if candidate and candidate.opportunity_id!=item.pk:raise PermissionDenied
        row,created=RecruitingTaskLink.objects.get_or_create(task=task,opportunity=item,
            defaults={'candidate':candidate,'interest':interest,'added_by':actor})
        if created:audit(actor,'task.linked',row,data={'task':str(task.pk),'opportunity':str(item.pk)})
        return row


def create_followup(*,actor,opportunity_id,data,operation_id,candidate_id=None,interest_id=None):
    from apps.operations.services import save_task
    from apps.operations.models import Task
    with policy_transaction():
        actor=fresh_staff(actor);item=opportunity_for(actor,opportunity_id,write=True,lock=True)
        existing=Task.objects.filter(pk=operation_id).first()
        if existing:
            link=RecruitingTaskLink.objects.filter(task=existing,opportunity=item,candidate_id=candidate_id,interest_id=interest_id).first()
            if not link:raise ValidationError('This action identifier has already been used.')
            from apps.operations.task_access import require_task_access
            require_task_access(actor,existing)
            return existing,None
        task,form=save_task(actor=actor,data=data,creation_id=operation_id)
        if not task:return None,form
        attach_task(actor=actor,opportunity_id=item.pk,task_id=task.pk,candidate_id=candidate_id,interest_id=interest_id)
        return task,form


def record_outreach(*,actor,candidate_id,data,operation_id,expected_revision):
    from apps.operations.models import Task
    from apps.operations.task_access import require_task_access
    with policy_transaction():
        actor=fresh_staff(actor);row=candidate_for(actor,candidate_id,write=True,lock=True)
        prior=CandidateOutreach.objects.filter(operation_id=operation_id).first()
        if prior:
            if prior.candidate_id!=row.pk or prior.actor_id!=actor.pk:raise PermissionDenied
            return prior
        current_revision(row,expected_revision)
        task=Task.objects.get(pk=data['task']) if data.get('task') else None
        if task:require_task_access(actor,task);attach_task(actor=actor,opportunity_id=row.opportunity_id,task_id=task.pk,candidate_id=row.pk)
        stamp=data['contacted_at']
        if stamp>timezone.now():raise ValidationError('Record actual contact, not a future outreach date.')
        event=CandidateOutreach(candidate=row,actor=actor,method=data['method'],contacted_at=stamp,
            outcome=data['outcome'],response=data.get('response',''),notes=data.get('notes',''),task=task,operation_id=operation_id)
        event.full_clean();event.save();row.revision+=1;row.save()
        audit(actor,'outreach.recorded',event,data={'candidate':str(row.pk),'method':event.method,'contacted_at':stamp.isoformat(),'task':str(task.pk) if task else None})
        return event


def review_interest(*,actor,interest_id,status,note,expected_revision,operation_id,task_id=None,select=False,expected_conflicts='',expected_need_revision=None,acknowledged=False):
    from apps.operations.models import Task
    from apps.operations.task_access import require_task_access
    with policy_transaction():
        actor=fresh_staff(actor)
        row=CandidateInterest.objects.select_for_update().get(pk=interest_id)
        opportunity_for(actor,row.opportunity_id,write=True,lock=True)
        prior=InterestReview.objects.filter(operation_id=operation_id).first()
        if prior:
            if prior.interest_id!=row.pk or prior.actor_id!=actor.pk:raise PermissionDenied
            return prior
        current_revision(row,expected_revision)
        if not row.active:raise ValidationError('This interest is historical.')
        if status not in {'received','review','information','not_selected'}:raise ValidationError('Choose a participant-safe review status.')
        if select and status=='not_selected':raise ValidationError('Selecting a pipeline candidate cannot be combined with No Longer Under Consideration.')
        task=Task.objects.get(pk=task_id) if task_id else None
        if task:require_task_access(actor,task);attach_task(actor=actor,opportunity_id=row.opportunity_id,task_id=task.pk,interest_id=row.pk)
        candidate=None
        if select:
            candidate=select_candidate(actor=actor,opportunity_id=row.opportunity_id,participant_id=row.participant_id,
                expected_conflicts=expected_conflicts,expected_need_revision=expected_need_revision,acknowledged=acknowledged,why=note)
            row.candidate=candidate
        row.status=status;row.revision+=1;row.save()
        event=InterestReview.objects.create(interest=row,actor=actor,status=status,note=reason(note),task=task,
            candidate=candidate,operation_id=operation_id)
        audit(actor,'interest.reviewed',event,data={'interest':str(row.pk),'status':status,'selected':bool(candidate),'task':str(task.pk) if task else None},why=note)
        return event
