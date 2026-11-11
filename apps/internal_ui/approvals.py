"""Read-only category adapters; all decisions stay in their existing domain views."""
from urllib.parse import urlencode
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q, F, Exists, OuterRef, Prefetch
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from apps.accounts.roles import manager, has_role
from apps.crm.access import fresh_staff
from apps.customers.access import is_manager
from apps.recruiting.selection import opportunities_for
from apps.participants.verification_services import can_view_qualification_approvals

CATEGORIES = (('events', 'Events'), ('verification', 'Verification'),
              ('customer', 'Customer approval'), ('jobs', 'Job postings'))


def access_flags(user):
    """Navigation affordances only; actual reads refresh staff/domain authority."""
    staff = bool(getattr(user, 'is_active', False) and getattr(user, 'is_staff', False))
    recruiting = staff and user.has_perm('participants.view_participant') and (manager(user) or has_role(user, 'recruiter') or user.has_perm('recruiting.manage_recruiting'))
    customers = staff and manager(user) and (user.is_superuser or user.has_perm('customers.manage_customers'))
    return {'events': staff and user.has_perms(('staffing.manage_event_staffing', 'events.view_event')),
            'verification': staff and can_view_qualification_approvals(user),
            'customer': recruiting, 'jobs': customers or recruiting}


def can_open(user):
    return any(access_flags(user).values())


def url(request, **changes):
    params = {k: request.GET[k] for k in ('tab', 'state', 'q', 'page') if request.GET.get(k)}
    for key, value in changes.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = str(value)
    return reverse('internal:workspace', args=['approvals']) + ('?' + urlencode(params) if params else '')


def _row(title, context, status, *, date=None, date_label='', actor=None, actor_label='Recorded by', secondary_date=None, secondary_date_label='', link='', action='Review', detail='', tone='neutral'):
    return dict(title=title, context=context, status=status, date=date, date_label=date_label,
                actor=actor, actor_label=actor_label, secondary_date=secondary_date, secondary_date_label=secondary_date_label, link=link, action=action, detail=detail, tone=tone)


def _page(rows, request):
    return Paginator(rows, 25).get_page(request.GET.get('page'))


def _events(actor, request, state, query):
    from apps.staffing import services
    from apps.staffing.models import AvailabilityRequest
    services.require_manager(actor)
    choices = [('pending','Pending'), ('approved','Approved'), ('declined','Declined'), ('all','All requests')]
    state = state if state in dict(choices) else 'pending'
    base = AvailabilityRequest.objects.select_related('staff', 'event', 'decided_by')
    rows = base if state == 'all' else base.filter(status=state)
    if query:
        rows = rows.filter(Q(event__name__icontains=query) | Q(staff__first_name__icontains=query) | Q(staff__last_name__icontains=query))
    page = _page(rows.order_by('event__start_at', '-created_at', '-pk'), request)
    items = []
    for record in page:
        days = ', '.join(str(day) for day in record.requested_dates)
        detail = ('Requested days: ' + days if days else 'Requested days not recorded.')
        if record.decided_at:
            detail += ' Decision: ' + record.decision_reason if record.decision_reason else ''
        items.append(_row(str(record.staff), record.event.name, record.get_status_display(),
            date=record.created_at, date_label='Requested', actor=record.decided_by, actor_label='Decision by', secondary_date=record.decided_at, secondary_date_label='Decided',
            detail=detail, link=reverse('staffing:event', args=[record.event_id]), action='Review request' if record.status == 'pending' else 'View staffing',
            tone='warning' if record.status == 'pending' else 'success' if record.status == 'approved' else 'neutral'))
    return dict(page=page, rows=items, state=state, states=choices,
        description='BTW staff requests to work an event. Approval confirms staffing, not attendance or pay.',
        empty='No staff work requests match this view.', source_url=reverse('staffing:queue'), source_label='Existing staff request queue')


def _verification(actor, request, state, query):
    from apps.participants.qualification_models import ParticipantQualification
    from apps.participants.participant_document_models import ParticipantDocument
    from apps.participants.verification_models import ParticipantVerificationRecord
    from apps.participants.verification_services import qualification_verification_summary, document_verification_summary
    if not can_view_qualification_approvals(actor):
        raise PermissionDenied
    choices = [('worklist','Review worklist'), ('verified','Verified'), ('rejected','Rejected'), ('all','All current evidence')]
    state = state if state in dict(choices) else 'worklist'
    reviews = ParticipantVerificationRecord.objects.select_related('reviewer', 'evidence_document', 'document').order_by('-created_at', '-pk')
    qualifications = ParticipantQualification.objects.filter(is_active=True).select_related('participant','catalog_entry')
    documents = ParticipantDocument.objects.filter(state=ParticipantDocument.State.ACTIVE).select_related('participant','category')
    if query:
        match = Q(participant__first_name__icontains=query) | Q(participant__last_name__icontains=query)
        qualifications = qualifications.filter(match)
        documents = documents.filter(match)
    targets = []
    for kind, records, summarize in [('qualification', qualifications, qualification_verification_summary), ('document', documents, document_verification_summary)]:
        for record in records.prefetch_related(Prefetch('verification_records', queryset=reviews, to_attr='_workspace_verifications')):
            summary = summarize(record)
            pending = summary['status'] in (('unverified','pending','self_reported','expired') if kind == 'qualification' else ('pending','expired'))
            if state == 'worklist' and not pending or state in ('verified','rejected') and summary['status'] != state:
                continue
            targets.append((kind, record, summary))
    targets.sort(key=lambda item:(str(item[1].participant).casefold(), item[0], str(item[1].pk)))
    page = _page(targets, request)
    items = [_row(str(record.participant), kind.title() + ' · ' + str(record), summary['label'],
        date=summary.get('reviewed_at') or record.created_at, date_label='Last reviewed' if summary.get('reviewed_at') else 'Added',
        actor=summary.get('reviewer'), actor_label='Reviewed by', detail='Existing evidence and append-only decision history remain in the review.',
        link=reverse('participants:staff-qualification-approval-detail', args=[kind,record.pk]), action='Review evidence & history',
        tone='success' if summary['status']=='verified' else 'warning' if summary['status'] in ('pending','expired') else 'neutral')
        for kind, record, summary in page]
    return dict(page=page, rows=items, state=state, states=choices,
        description='Qualification and document evidence. This is a review worklist; not every item is a submitted request.',
        empty='No evidence matches this state and search.', source_url=reverse('participants:staff-qualification-approvals'), source_label='Existing verification queue')


def _opportunities(actor):
    from apps.recruiting.models import OpportunityCollaboration
    allowed = opportunities_for(actor).filter(archived_at__isnull=True)
    return allowed.annotate(_delegated_manage=Exists(OpportunityCollaboration.objects.filter(
        opportunity_id=OuterRef('pk'), user=actor, revoked_at__isnull=True, can_manage=True)))


def _can_manage(actor, opportunity):
    return manager(actor) or opportunity.owner_id == actor.pk or opportunity._delegated_manage


def _customer(actor, request, state, query):
    from apps.recruiting.models import CandidateOpportunity, CandidateDisclosure
    allowed = _opportunities(actor)
    ids = allowed.order_by().values('pk')
    choices = [('selected','Selected / not released'), ('history','Release history'), ('revoked','Revoked releases')]
    state = state if state in dict(choices) else 'selected'
    if state == 'selected':
        records = CandidateOpportunity.objects.filter(opportunity_id__in=ids, active=True).annotate(
            _released=Exists(CandidateDisclosure.objects.filter(candidate_id=OuterRef('pk'),revoked_at__isnull=True))).filter(_released=False)
        if query:
            records = records.filter(Q(participant__first_name__icontains=query)|Q(participant__last_name__icontains=query)|Q(opportunity__need__organization__name__icontains=query))
        records = records.select_related('participant','selected_by','opportunity__need__organization','opportunity__need__effective_revision','opportunity__need__working_revision')
        page = _page(records.order_by('-created_at','-pk'), request)
        scope = {r.pk:r for r in allowed.filter(pk__in={r.opportunity_id for r in page})}
        items = []
        for record in page:
            can_manage = _can_manage(actor, scope[record.opportunity_id])
            items.append(_row(str(record.participant), str(record.opportunity.need) + ' · ' + str(record.opportunity.need.organization),
                'Selected / not released', date=record.created_at, date_label='Selected', actor=record.selected_by, actor_label='Selected by',
                detail='Selection is not an approval request or consent. Release requires the existing reviewed preview and confirmation.',
                link=reverse('recruiting:disclosure' if can_manage else 'recruiting:candidate-detail', args=[record.pk]),
                action='Review release' if can_manage else 'View candidate'))
    else:
        records = CandidateDisclosure.objects.filter(candidate__opportunity_id__in=ids).defer('approved_projection')
        if state == 'revoked': records = records.filter(revoked_at__isnull=False)
        if query: records = records.filter(Q(participant__first_name__icontains=query)|Q(participant__last_name__icontains=query)|Q(organization__name__icontains=query))
        records = records.select_related('participant','organization','released_by','revoked_by','candidate__opportunity__need__effective_revision','candidate__opportunity__need__working_revision')
        page = _page(records.order_by('-released_at','-pk'), request)
        scope = {r.pk:r for r in allowed.filter(pk__in={r.candidate.opportunity_id for r in page})}
        items = []
        for record in page:
            can_manage = _can_manage(actor,scope[record.candidate.opportunity_id])
            items.append(_row(str(record.participant), str(record.organization) + ' · ' + str(record.candidate.opportunity.need),
                'Revoked' if record.revoked_at else 'Release recorded', date=record.revoked_at or record.released_at,
                date_label='Revoked' if record.revoked_at else 'Released', actor=record.revoked_by if record.revoked_at else record.released_by, actor_label='Revoked by' if record.revoked_at else 'Released by',
                detail='Retained release history. Recorded release is not proof of current employer access; source consent and projection checks still apply.',
                link=reverse('recruiting:disclosure' if can_manage else 'recruiting:candidate-detail',args=[record.candidate_id]),action='Review release history' if can_manage else 'View candidate'))
    return dict(page=page,rows=items,state=state,states=choices,description='Scoped candidate release review and history. No candidate information is released from this workspace.',empty='No candidate release records match this view.')


def _jobs(actor, request, state, query):
    from apps.customers.models import WorkforceNeedRevision
    from apps.recruiting.jobs import published
    flags = access_flags(actor)
    customers = is_manager(actor)
    choices = ([('positions','Position review'),('position_history','Position revision history')] if customers else []) + ([('publication','Publication review'),('publication_history','Publication history / all states')] if flags['customer'] else [])
    state = state if state in dict(choices) else choices[0][0]
    if state.startswith('position'):
        records = WorkforceNeedRevision.objects.select_related('organization','need','submitted_by','reviewed_by','approved_by')
        if state == 'positions': records = records.filter(state__in=('submitted','review'))
        else: records = records.exclude(state='draft')
        if query: records = records.filter(Q(title__icontains=query)|Q(organization__name__icontains=query))
        page = _page(records.order_by('-submitted_at','-pk'),request)
        items = [_row(record.title or 'Untitled position', str(record.organization) + ' · Revision ' + str(record.number),
            record.get_state_display(), date=record.approved_at or record.submitted_at,
            date_label='Approved' if record.approved_at else 'Submitted', actor=record.approved_by or record.reviewed_by, actor_label='Approved by' if record.approved_by else 'Reviewed by',
            detail=('Decision: '+record.decision_reason) if record.decision_reason else 'Review stays in the existing position revision workflow.',
            link=reverse('customers:need',args=[record.organization_id,record.need_id]),action='Review position' if record.state in ('submitted','review') else 'View revision history',
            tone='success' if record.state=='approved' else 'warning' if record.state in ('submitted','review') else 'neutral')for record in page]
    else:
        allowed = _opportunities(actor)
        if state == 'publication':
            # Existing current-publication eligibility owns the classification.
            # Closed/current copies are history, not pending approval requests.
            allowed = allowed.exclude(publication_state='closed').exclude(pk__in=published().values('pk'))
        if query: allowed = allowed.filter(Q(need__effective_revision__title__icontains=query)|Q(need__organization__name__icontains=query))
        allowed = allowed.select_related('publication__published_by','need__effective_revision','need__working_revision','need__organization','owner')
        page = _page(allowed.order_by('-updated_at','-pk'),request)
        current = set(published().filter(pk__in=[r.pk for r in page]).values_list('pk',flat=True))
        items=[]
        for record in page:
            status = record.get_publication_state_display()
            if record.publication_state == 'published' and record.pk not in current:status='Publication requires review'
            can_manage=_can_manage(actor,record)
            items.append(_row(str(record.need),str(record.need.organization),status,
                date=record.publication.created_at if record.publication_id else record.created_at,
                date_label='Publication version created' if record.publication_id else 'Opportunity created', actor=record.publication.published_by if record.publication_id else record.owner, actor_label='Published by' if record.publication_id else 'Owner',
                detail='Publication is separate from position review. Current approved version, reviewed public copy and expiry are checked by the source workflow.',
                link=reverse('recruiting:opportunity-settings' if can_manage else 'recruiting:matching',args=[record.pk]),action='Review publication' if can_manage else 'View opportunity'))
    return dict(page=page,rows=items,state=state,states=choices,description='Position revision review and publication are separate. Only submitted or in-review position revisions are pending approvals.',empty='No position or publication records match this view.')


@require_GET
def workspace(request):
    from apps.internal_ui.views import _staff_context
    actor = fresh_staff(request.user)
    flags = access_flags(actor)
    available = [(key,label) for key,label in CATEGORIES if flags[key]]
    if not available: raise PermissionDenied
    selected = request.GET.get('tab',available[0][0])
    if selected not in dict(CATEGORIES):raise Http404
    if not flags[selected]:raise PermissionDenied
    query = request.GET.get('q','').strip()[:120]
    adapter = {'events':_events,'verification':_verification,'customer':_customer,'jobs':_jobs}[selected]
    data=adapter(actor,request,request.GET.get('state',''),query)
    data.update(primary_state=data['states'][0][0], current_state_label=dict(data['states'])[data['state']])
    data.update(approval_tabs=[{'label':label,'selected':key==selected,'url':url(request,tab=key,state=None,page=None)}for key,label in available],
        approval_states=[{'label':label,'selected':key==data['state'],'url':url(request,tab=selected,state=key,page=None)}for key,label in data['states']],
        query=query,selected=selected,previous_page_url=url(request,page=data['page'].previous_page_number())if data['page'].has_previous()else'',
        next_page_url=url(request,page=data['page'].next_page_number())if data['page'].has_next()else'',clear_url=url(request,q=None,page=None))
    return render(request,'internal/approvals.html',_staff_context(request,page_title='Approvals',page_description='Existing review workflows, together in one workspace.',**data))
