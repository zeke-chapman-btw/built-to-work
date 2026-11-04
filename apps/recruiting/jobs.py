"""Participant-owned interest in explicitly published, approved position versions."""
from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models import Q, F
from django.utils import timezone
from apps.participants.identity import identity_members
from .access import fresh_participant, policy_transaction, person
from .foundation import audit, readiness
from .models import RecruitingOpportunity, CandidateInterest


def account_for(actor):
    from apps.participants.portal_preview import current_fixture,preview_account
    if current_fixture(actor):return preview_account(actor)
    return fresh_participant(actor,allow_identity_review=True)[1]


def published():
    return RecruitingOpportunity.objects.filter(archived_at__isnull=True,
        publication_state__in=['published','closed'],publication__isnull=False,
        need__status='active',need__effective_revision__state='approved',
        publication__need_revision_id=F('need__effective_revision_id')
        ).filter(Q(publication__expires_at__isnull=True)|Q(publication__expires_at__gt=timezone.now())).select_related('publication')


def projection(item):
    # Explicitly reviewed public copy only. Never include the Need/organization model.
    return {'id':str(item.pk),'version':str(item.publication_id),'details':dict(item.publication.safe_details),
        'accepting':item.publication_state=='published','expires_at':item.publication.expires_at}


def browse(actor,query='',trade='',location='',employment_type=''):
    account_for(actor)
    query=query.strip()[:200].casefold();trade=trade.strip()[:160].casefold();location=location.strip()[:200].casefold()
    result=[]
    for item in published():
        job=projection(item);d=job['details']
        if query and query not in ' '.join(d.values()).casefold():continue
        if trade and trade not in d.get('trade','').casefold():continue
        if location and location not in d.get('location','').casefold():continue
        if employment_type and employment_type.casefold() not in d.get('employment_type','').casefold():continue
        result.append(job)
    return sorted(result,key=lambda j:(j['details']['title'].casefold(),j['id']))


def own_interests(actor):
    account=account_for(actor)
    return CandidateInterest.objects.filter(participant_id__in=identity_members(account.participant_id)
        ).select_related('publication').order_by('-created_at','-pk')


def interest_projection(row):
    return {'id':str(row.pk),'job':dict(row.publication.safe_details),'opportunity_id':str(row.opportunity_id),
        'status':row.get_status_display(),'submitted_at':row.created_at,'active':row.active}


def express_interest(*,actor,opportunity_id,publication_id):
    with policy_transaction():
        from apps.participants.portal_preview import current_fixture
        if current_fixture(actor):raise ValidationError('Job interest submission requires a dedicated synthetic Participant account; Administrator Preview cannot associate its fixture with live recruiting positions.')
        actor,account=fresh_participant(actor,lock=True,allow_identity_review=True)
        item=published().select_for_update(of=('self',)).filter(pk=opportunity_id).first()
        if not item:raise ValidationError('This opportunity is no longer available.')
        if str(item.publication_id)!=str(publication_id):raise ValidationError('The opportunity changed. Review the current details before expressing interest.')
        candidate=person(account.participant_id,lock=True)
        existing=CandidateInterest.objects.filter(opportunity=item,participant_id__in=identity_members(candidate.pk),active=True).first()
        if existing:return existing
        if item.publication_state!='published':raise ValidationError('This opportunity is closed to new interest.')
        row=CandidateInterest.objects.create(opportunity=item,publication=item.publication,participant=candidate,
            source_participant=account.participant,submitted_by=actor)
        audit(actor,'interest.received',row,data={'opportunity':str(item.pk),'publication':str(item.publication_id),
            'source':'participant_portal','intake_ready':readiness(candidate)['eligible']})
        return row


def withdraw_interest(*,actor,interest_id):
    with policy_transaction():
        from apps.participants.portal_preview import current_fixture
        if current_fixture(actor):raise PermissionDenied('Use a dedicated synthetic Participant account for recruiting actions.')
        actor,account=fresh_participant(actor,lock=True,allow_identity_review=True)
        row=CandidateInterest.objects.select_for_update().filter(pk=interest_id,participant_id__in=identity_members(account.participant_id)).first()
        if not row:raise PermissionDenied
        if row.active:
            row.active=False;row.status='withdrawn';row.revision+=1;row.save()
            audit(actor,'interest.withdrawn',row,data={'interest':str(row.pk)})
        return row
