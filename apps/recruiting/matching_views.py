from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods
from apps.accounts.roles import manager
from .views import staff, page
from .access import person
from .models import RecruitingOpportunity
from .forms import OpportunityForm, CriteriaForm, CandidateSelectionForm
from . import selection, matching
from .foundation import readiness


@staff
@require_http_methods(['GET','POST'])
def matching_directory(request):
    form=OpportunityForm(request.POST or None)
    if request.method=='POST':
        if not manager(request.recruiting_actor):raise PermissionDenied
        if form.is_valid():
            try:item=selection.create_opportunity(actor=request.user,need_id=form.cleaned_data['need'].pk,owner_id=form.cleaned_data['owner'].pk)
            except ValidationError as exc:form.add_error(None,exc)
            else:return redirect('recruiting:matching',opportunity_id=item.pk)
    return page(request,'recruiting/matching_directory.html',page_title='Candidate Matching',
        opportunities=selection.opportunities_for(request.user),form=form,can_create=manager(request.recruiting_actor))


@staff
@require_http_methods(['GET','POST'])
def criteria_editor(request,opportunity_id):
    item=selection.opportunity_for(request.user,opportunity_id,write=True)
    initial={'expected_revision':item.revision,'expected_need_revision':item.need.effective_revision_id}
    form=CriteriaForm(request.POST or None,actor=request.recruiting_actor,initial=initial)
    if request.method=='POST' and form.is_valid():
        try:selection.save_criteria(actor=request.user,opportunity_id=item.pk,rules=form.rules(),
            expected_revision=form.cleaned_data['expected_revision'],expected_need_revision=form.cleaned_data['expected_need_revision'],why=form.cleaned_data['reason'])
        except ValidationError as exc:form.add_error(None,exc)
        else:return redirect('recruiting:matching',opportunity_id=item.pk)
    return page(request,'recruiting/criteria.html',page_title='Matching criteria · '+item.need.title,form=form,
        opportunity=item,approved_terms=item.need.effective_revision,current_rules=item.criteria.rules if item.criteria else [])


@staff
@require_http_methods(['GET'])
def matching_results(request,opportunity_id):
    item=selection.opportunity_for(request.user,opportunity_id)
    problem='';form=None;rows=[];not_ready=None
    try:form,rows,not_ready=matching.recommendations(request.user,item,request.GET)
    except ValidationError as exc:problem='; '.join(exc.messages)
    paginated=Paginator(rows,25).get_page(request.GET.get('page'))
    query=request.GET.copy();query.pop('page',None)
    return page(request,'recruiting/matching.html',page_title='Matching · '+item.need.title,opportunity=item,
        results=paginated,form=form,problem=problem,not_ready=not_ready,ranked_total=len(rows),query_without_page=query.urlencode())


@staff
@require_http_methods(['GET','POST'])
def select_candidate(request,opportunity_id,participant_id):
    item=selection.opportunity_for(request.user,opportunity_id,write=True)
    candidate=person(participant_id)
    conflicts=selection.conflict_set(request.user,candidate,excluding=item.pk)
    current_score=matching.score_candidate(request.recruiting_actor,candidate,item.criteria) if item.criteria and item.criteria.need_revision_id==item.need.effective_revision_id else None
    form=CandidateSelectionForm(request.POST or None,initial={'expected_conflicts':conflicts['fingerprint'],'expected_need_revision':item.need.effective_revision_id,
        'criteria_id':item.criteria_id if item.criteria and item.criteria.need_revision_id==item.need.effective_revision_id else None})
    if request.method=='POST' and form.is_valid():
        try:selection.select_candidate(actor=request.user,opportunity_id=item.pk,participant_id=candidate.pk,
            expected_conflicts=form.cleaned_data['expected_conflicts'],expected_need_revision=form.cleaned_data['expected_need_revision'],acknowledged=form.cleaned_data['acknowledge_conflicts'],
            why=form.cleaned_data['reason'],criteria_id=form.cleaned_data['criteria_id'])
        except ValidationError as exc:form.add_error(None,exc)
        else:
            messages.success(request,'Candidate explicitly selected for this position. No employer disclosure was created.')
            return redirect('recruiting:matching',opportunity_id=item.pk)
    return page(request,'recruiting/select_candidate.html',page_title='Review candidate selection',opportunity=item,
        participant=candidate,form=form,conflicts=conflicts,score=current_score,readiness=readiness(candidate),
        policy_issues=selection.candidate_gates(candidate,item.need.organization_id))
