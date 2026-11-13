import uuid
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q,Count,Exists,OuterRef,CharField,Value
from django.db.models.functions import Cast,Replace
from django.utils import timezone
from django.shortcuts import redirect,get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.accounts.roles import manager
from apps.operations.task_access import tasks_for
from .views import staff,page
from .access import fresh_staff,person
from .foundation import readiness,audit,group_positions
from .selection import opportunities_for,opportunity_for,conflict_set,candidate_gates
from .models import (CandidateOpportunity,CandidateInterest,RecruitingStage,WorkforceRequest,IdentityConsolidation,EmployerCandidateFeedback,RecruitingTaskLink)
from .workflow_forms import (StageChangeForm,ReassignmentForm,CollaborationForm,PublicationForm,
    OutreachForm,ReviewForm,FollowupForm,StageConfigurationForm)
from . import workflow


def can_manage(actor,item):
    try:opportunity_for(actor,item.pk,write=True)
    except PermissionDenied:return False
    return True


@staff
@require_http_methods(['GET'])
def overview(request):
    from .read_models import staff_queues
    queues=staff_queues(request.user)
    items,current,candidates,responses,pending_responses,overdue,outreach=(queues[k] for k in
        ('items','current','candidates','responses','pending_responses','overdue','outreach'))
    return page(request,'recruiting/overview.html',page_title='Recruiting',
        opportunity_count=items.count(),interest_count=queues['incoming'].count(),
        active_count=CandidateOpportunity.objects.filter(opportunity__in=items,active=True).count(),opportunities=items[:8],
        open_position_count=current.count(),engagement_count=current.filter(candidates__active=True).distinct().count(),
        request_count=WorkforceRequest.objects.filter(archived_at__isnull=True,positions__need__recruiting_opportunity__in=current).distinct().count(),
        stage_counts=candidates.values('stage__key','stage__label','stage__position').annotate(total=Count('pk')).order_by('stage__position','stage__key'),
        outreach_count=outreach.count(),outreach=outreach[:5],overdue_count=overdue.count(),overdue_tasks=overdue.order_by('due_date','pk')[:5],
        interview_count=pending_responses.filter(kind='interview').count(),pending_response_count=pending_responses.count(),
        pending_responses=pending_responses.order_by('-created_at')[:8],
        employer_responses=responses.order_by('-created_at')[:8])


@staff
@require_http_methods(['GET'])
def campaigns(request):
    from apps.customers.models import Campaign
    from apps.customers.models import CustomerOrganization
    from django.db.models import Q
    items=Campaign.objects.all() if manager(request.recruiting_actor) else Campaign.objects.filter(workforce_needs__recruiting_opportunity__in=opportunities_for(request.user)).distinct()
    term,status=request.GET.get('q','').strip(),request.GET.get('status','')
    if term:items=items.filter(Q(name__icontains=term)|Q(organization__name__icontains=term))
    if status in Campaign.Status.values:items=items.filter(status=status)
    can_manage=manager(request.recruiting_actor)
    customers=CustomerOrganization.objects.exclude(status='archived').order_by('name') if can_manage and request.user.has_perm('customers.change_customerorganization') else CustomerOrganization.objects.none()
    return page(request,'recruiting/campaigns.html',page_title='Campaigns',campaigns=items.select_related('organization').order_by('-created_at'),
        q=term,status=status,statuses=Campaign.Status.choices,customers=customers,
        can_manage=can_manage,requests=WorkforceRequest.objects.all() if can_manage else WorkforceRequest.objects.none())


@staff
@require_http_methods(['GET'])
def campaign_detail(request, campaign_id):
    from .foundation import visible_campaigns
    item = get_object_or_404(visible_campaigns(request.user).select_related('organization'), pk=campaign_id)
    positions = opportunities_for(request.user).filter(need__campaign=item, archived_at__isnull=True)
    return page(request, 'recruiting/campaign_detail.html', page_title=item.name,
        campaign=item, positions=positions, can_manage=manager(request.recruiting_actor))


@staff
@require_http_methods(['GET'])
def pipeline(request):
    opportunities=opportunities_for(request.user).filter(archived_at__isnull=True)
    rows=CandidateOpportunity.objects.filter(opportunity__in=opportunities).select_related('participant','opportunity__need__effective_revision','stage','assignee')
    q=request.GET.get('q','').strip()[:200]
    if q:rows=rows.filter(Q(participant__first_name__icontains=q)|Q(participant__last_name__icontains=q)|Q(opportunity__need__effective_revision__title__icontains=q))
    if request.GET.get('opportunity'):
        try:position_id=uuid.UUID(request.GET['opportunity'])
        except (ValueError,TypeError):position_id=None
        rows=rows.filter(opportunity_id=position_id)
    if request.GET.get('state')!='history':rows=rows.filter(active=True)
    rows=rows.order_by('stage__position','participant__last_name','pk')
    view='board' if request.GET.get('view')=='board' else 'list'
    # Bound each column; the complete filtered list remains available.
    columns=[{'stage':s,'count':rows.filter(stage=s).count(),'rows':rows.filter(stage=s)[:15]} for s in RecruitingStage.objects.filter(Q(is_active=True)|Q(candidateopportunity__in=rows)).distinct()] if view=='board' else []
    query=request.GET.copy();query.pop('page',None)
    view_query=query.copy();view_query.pop('view',None)
    return page(request,'recruiting/pipeline.html',page_title='Pipelines',rows=Paginator(rows,30).get_page(request.GET.get('page')),query_without_page=query.urlencode(),
        columns=columns,view=view,q=q,opportunities=opportunities,filters=request.GET,view_query=view_query.urlencode())


@staff
@require_http_methods(['GET','POST'])
def candidate_detail(request,candidate_id):
    row=workflow.candidate_for(request.user,candidate_id)
    editable=can_manage(request.user,row.opportunity)
    edit=request.GET.get('edit','')
    operation=request.POST.get('operation') or (edit if request.method=='POST' else None)
    initial={'expected_revision':row.revision}
    stage=StageChangeForm(request.POST if operation=='stage' else None,initial={**initial,'stage':row.stage_id})
    reassignment=ReassignmentForm(request.POST if operation=='reassign' else None,initial={**initial,'user':row.assignee_id})
    from django.utils import timezone as dj_tz
    outreach=OutreachForm(request.POST if operation=='outreach' else None,actor=request.recruiting_actor,
        initial={**initial,'contacted_at':dj_tz.localtime().replace(second=0,microsecond=0,tzinfo=None)})
    forms_by_action={'stage':stage,'reassign':reassignment,'outreach':outreach}
    detail_url=reverse('recruiting:candidate-detail',args=[row.pk])
    if request.method=='POST':
        if not editable:raise PermissionDenied
        form=forms_by_action.get(operation)
        if not form:raise PermissionDenied
        if form.is_valid():
            d=form.cleaned_data
            try:
                if operation=='stage':workflow.transition(actor=request.user,candidate_id=row.pk,stage_key=d['stage'].pk,expected_revision=d['expected_revision'],why=d['reason'],confirm_hired=d['confirm_hired'])
                elif operation=='reassign':workflow.reassign(actor=request.user,candidate_id=row.pk,user_id=d['user'],expected_revision=d['expected_revision'],why=d['reason'])
                else:workflow.record_outreach(actor=request.user,candidate_id=row.pk,expected_revision=d['expected_revision'],operation_id=d['operation_id'],data={**d,'task':d['task'].pk if d['task'] else None})
            except ValidationError as exc:form.add_error(None,exc)
            else:
                messages.success(request,'Saved.')
                from apps.internal_ui.record_editors import editor_success
                return editor_success(request,detail_url)
    # Stage, assignment and contact log open as pop-up editors ("?edit=stage|reassign|outreach").
    if edit in forms_by_action and (request.method=='GET' or operation==edit):
        if not editable:raise PermissionDenied
        from apps.internal_ui.record_editors import editor_response
        titles={'stage':'Change stage','reassign':"Change who's assigned",'outreach':'Log a contact'}
        return editor_response(request,template='recruiting/editor_page.html',panel='internal/ui/record_form_panel.html',context={
            'form':forms_by_action[edit],'page_title':titles[edit],'editor_context':f'{row.participant} · {row.opportunity.need.title}',
            'back':detail_url,'save_label':'Save changes','fields_template':'internal/ui/section_fields.html',
            'full_page_editor':request.headers.get('X-BTW-Editor')!='1'})
    permitted_tasks=tasks_for(request.recruiting_actor)
    outreach_rows=list(row.outreach.select_related('actor','task').order_by('-contacted_at','-pk')[:30])
    task_ids=set(permitted_tasks.values_list('pk',flat=True))
    for event in outreach_rows:event.visible_task=event.task if event.task_id in task_ids else None
    from apps.core.models import AuditLog
    from django.contrib.contenttypes.models import ContentType
    history=AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(row),object_id=str(row.pk),action__startswith='recruiting.').order_by('-timestamp')[:30]
    return page(request,'recruiting/candidate_detail.html',page_title=str(row.participant)+' · '+row.opportunity.need.title,
        candidate=row,editable=editable,stage_form=stage,reassignment_form=reassignment,outreach_form=outreach,
        outreach=outreach_rows,history=history,policy_issues=candidate_gates(person(row.participant_id),row.opportunity.need.organization_id),
        task_links=row.task_links.filter(task__in=permitted_tasks).select_related('task'),
        origin_interests=row.originating_interests.order_by('-created_at')[:10],
        employer_feedback=EmployerCandidateFeedback.objects.filter(disclosure__candidate=row).order_by('-created_at')[:10])


@staff
@require_http_methods(['GET','POST'])
def opportunity_settings(request,opportunity_id):
    item=opportunity_for(request.user,opportunity_id,write=True)
    operation=request.POST.get('operation')
    initial={'expected_revision':item.revision,'expected_need_revision':item.need.effective_revision_id}
    if item.publication:initial.update(item.publication.safe_details);initial['expires_at']=item.publication.expires_at
    else:initial.update({'title':item.need.effective_revision.title,'trade':item.need.effective_revision.trade})
    publication=PublicationForm(request.POST if operation=='publication' else None,initial=initial)
    collaboration=CollaborationForm(request.POST if operation=='delegate' else None,initial=initial)
    if request.method=='POST':
        form=publication if operation=='publication' else collaboration
        if form.is_valid():
            d=form.cleaned_data
            try:
                if operation=='publication':workflow.publish(actor=request.user,opportunity_id=item.pk,expected_revision=d['expected_revision'],expected_need_revision=d['expected_need_revision'],action=d['action'],details=form.details(),expires_at=d['expires_at'],why=d['reason'])
                elif operation=='delegate':workflow.delegate(actor=request.user,opportunity_id=item.pk,user_id=d['user'],mode=d['mode'],expected_revision=d['expected_revision'],why=d['reason'])
                else:raise PermissionDenied
            except ValidationError as exc:form.add_error(None,exc)
            else:return redirect('recruiting:opportunity-settings',opportunity_id=item.pk)
    return page(request,'recruiting/opportunity_settings.html',page_title=item.need.title,opportunity=item,
        approved_terms=item.need.effective_revision,publication_form=publication,collaboration_form=collaboration,
        can_delegate=manager(request.recruiting_actor) or item.owner_id==request.user.pk,
        collaborations=item.collaborations.filter(revoked_at__isnull=True).select_related('user'),publications=item.publications.order_by('-number')[:10])


@staff
@require_http_methods(['GET'])
def interests(request):
    rows=CandidateInterest.objects.filter(opportunity__in=opportunities_for(request.user),active=True).select_related('participant','opportunity__need__effective_revision','publication').order_by('-created_at')
    if request.GET.get('status'):rows=rows.filter(status=request.GET['status'])
    query=request.GET.copy();query.pop('page',None)
    return page(request,'recruiting/interests.html',page_title='Incoming interest',rows=Paginator(rows,30).get_page(request.GET.get('page')),query_without_page=query.urlencode())


@staff
@require_http_methods(['GET','POST'])
def interest_review(request,interest_id):
    row=get_object_or_404(CandidateInterest.objects.select_related('participant','opportunity__need__organization','publication'),pk=interest_id)
    item=opportunity_for(request.user,row.opportunity_id)
    participant=person(row.participant_id)
    conflicts=conflict_set(request.user,participant,excluding=item.pk)
    form=ReviewForm(request.POST or None,actor=request.recruiting_actor,initial={'expected_revision':row.revision,'status':row.status,'expected_conflicts':conflicts['fingerprint'],'expected_need_revision':item.need.effective_revision_id})
    editable=can_manage(request.user,item)
    if request.method=='POST':
        if not editable:raise PermissionDenied
        if form.is_valid():
            d=form.cleaned_data
            try:workflow.review_interest(actor=request.user,interest_id=row.pk,status=d['status'],note=d['reason'],participant_message=d['participant_message'],expected_revision=d['expected_revision'],operation_id=d['operation_id'],task_id=d['task'].pk if d['task'] else None,select=d['select_candidate'],expected_conflicts=d['expected_conflicts'],expected_need_revision=d['expected_need_revision'],acknowledged=d['acknowledge_conflicts'])
            except ValidationError as exc:form.add_error(None,exc)
            else:messages.success(request,'Interest review recorded. Employer disclosure remains a separate BTW decision.');return redirect('recruiting:interest-review',interest_id=row.pk)
    return page(request,'recruiting/interest_review.html',page_title='Review Job Interest',interest=row,
        participant=participant,form=form,editable=editable,readiness=readiness(participant),conflicts=conflicts,
        policy_issues=candidate_gates(participant,item.need.organization_id),reviews=row.reviews.select_related('actor').order_by('-created_at')[:30])


@staff
@require_http_methods(['GET','POST'])
def followup(request,opportunity_id):
    item=opportunity_for(request.user,opportunity_id,write=True)
    candidate_id=request.GET.get('candidate') or None;interest_id=request.GET.get('interest') or None
    if candidate_id:workflow.candidate_for(request.user,candidate_id,write=True)
    if interest_id:get_object_or_404(CandidateInterest,pk=interest_id,opportunity=item)
    form=FollowupForm(request.POST or None,actor=request.recruiting_actor,initial={'title':'Recruiting follow-up · '+item.need.title})
    if request.method=='POST' and form.is_valid():
        try:task,validated=workflow.create_followup(actor=request.user,opportunity_id=item.pk,data=request.POST,
            operation_id=form.cleaned_data['operation_id'],candidate_id=candidate_id,interest_id=interest_id)
        except ValidationError as exc:form.add_error(None,exc)
        else:
            if task:return redirect('operations:task',task_id=task.pk)
            form=validated
    return page(request,'recruiting/form.html',page_title='Create recruiting follow-up Task',form=form,button='Create Task')


@staff
@require_http_methods(['GET','POST'])
def stages(request,key=None):
    fresh_staff(request.user,administrator=True)
    row=get_object_or_404(RecruitingStage,pk=key) if key else None
    form=StageConfigurationForm(request.POST or None,initial={'expected_revision':row.revision if row else None,
        'key':row.pk if row else '', 'label':row.label if row else '', 'position':row.position if row else 0,'is_active':row.is_active if row else True})
    if row:form.fields['key'].disabled=True;form.fields['key'].widget=forms.HiddenInput()
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data
        try:workflow.configure_stage(actor=request.user,key=d['key'],label=d['label'],position=d['position'],is_active=d['is_active'],expected_revision=d['expected_revision'],why=d['reason'])
        except ValidationError as exc:form.add_error(None,exc)
        else:
            from .ui_context import catalog_url
            if request.headers.get('X-BTW-Editor')=='1':
                from apps.internal_ui.record_editors import editor_success
                return editor_success(request, catalog_url(request, 'recruiting:stages'))
            return redirect(catalog_url(request, 'recruiting:stages'))
    if request.headers.get('X-BTW-Editor')=='1':
        from apps.internal_ui.record_editors import editor_response
        from .ui_context import catalog_url
        return editor_response(request, template='recruiting/stages.html', panel='internal/ui/record_form_panel.html', context={
            'form':form,'page_title':'Edit stage' if row else 'New stage','editor_context':row.label if row else '','back':catalog_url(request,'recruiting:stages'),'save_label':'Save changes',
            'fields_template':'internal/ui/section_fields.html','editor_return_label':'stages','full_page_editor':False})
    return page(request,'recruiting/stages.html',page_title='Stages',stages=RecruitingStage.objects.all(),form=form)


class RequestForm(forms.Form):
    organization=forms.ModelChoiceField(queryset=None,label='Customer')
    name=forms.CharField(max_length=160,label='Group name')
    positions=forms.ModelMultipleChoiceField(queryset=None,widget=forms.CheckboxSelectMultiple,help_text='Pick positions from the same customer.')
    def __init__(self,*args,**kwargs):
        from apps.customers.models import CustomerOrganization,WorkforceNeed
        super().__init__(*args,**kwargs)
        self.fields['organization'].queryset=CustomerOrganization.objects.exclude(status='archived')
        self.fields['positions'].queryset=WorkforceNeed.objects.filter(request_position__isnull=True).exclude(status__in=['closed','cancelled'])


@staff
@require_http_methods(['GET','POST'])
def request_group(request):
    if not manager(request.recruiting_actor):raise PermissionDenied
    form=RequestForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data
        try:group_positions(actor=request.user,organization_id=d['organization'].pk,name=d['name'],need_ids=[p.pk for p in d['positions']])
        except ValidationError as exc:form.add_error(None,exc)
        else:
            from apps.internal_ui.record_editors import editor_success
            return editor_success(request,reverse('recruiting:campaigns'))
    from apps.internal_ui.record_editors import editor_response
    return editor_response(request,template='recruiting/editor_page.html',panel='internal/ui/record_form_panel.html',context={
        'form':form,'page_title':'Group positions','back':reverse('recruiting:campaigns'),'save_label':'Group positions',
        'fields_template':'internal/ui/section_fields.html','full_page_editor':request.headers.get('X-BTW-Editor')!='1'})


@staff
@require_http_methods(['GET','POST'])
def identity_history(request):
    fresh_staff(request.user,administrator=True)
    if request.method=='POST':
        from .foundation import reverse_consolidation
        try:row=reverse_consolidation(actor=request.user,consolidation_id=request.POST.get('consolidation'),why=request.POST.get('reason'))
        except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
        else:messages.info(request,'Reversal requires attributed review of subsequent activity; no records were discarded.' if row.reversal_review_required else 'The safe reviewed identity link was reversed. Original records remain intact.')
        return redirect('recruiting:identity-history')
    return page(request,'recruiting/identity_history.html',page_title='Merged records',
        rows=IdentityConsolidation.objects.select_related('source','target','actor').order_by('-created_at')[:50])
