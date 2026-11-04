from django.contrib import messages
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.customers.portal_views import customer_view,context
from apps.customers.models import CustomerMembership
from .views import staff,page
from .access import fresh_staff,person
from .workflow import candidate_for
from .models import CandidateDisclosure,EmployerCandidateFeedback,DisclosureMemberPermission
from .employer_forms import (DisclosureForm,RevokeForm,EmployerFeedbackForm,ConfirmHireForm,MemberPermissionForm,WithdrawalForm,FeedbackReviewForm)
from . import employer


def directory_content(request):
    data=employer.list_candidates(request.user,request.customer_member.organization_id)
    return render(request,'recruiting/customer/candidates.html',context(request,'candidates',
        candidates=Paginator(data,20).get_page(request.GET.get('page'))))


@customer_view
@require_http_methods(['GET','POST'])
def customer_candidate(request,record_id):
    organization=request.customer_member.organization_id
    data=employer.detail(request.user,organization,record_id)
    form=EmployerFeedbackForm(request.POST or None)
    if request.method=='POST':
        if getattr(request,'customer_preview',False):raise PermissionDenied('Customer Administrator Preview is view-only.')
        if form.is_valid():
            d=form.cleaned_data
            try:employer.feedback(actor=request.user,organization_id=organization,disclosure_id=record_id,kind=d['kind'],message=d['message'],operation_id=d['operation_id'])
            except ValidationError as exc:form.add_error(None,exc)
            else:messages.success(request,'Your response was sent to BTW. Official recruiting decisions remain with BTW.');return redirect('customer_portal:recruiting-candidate',record_id=record_id)
    return render(request,'recruiting/customer/candidate.html',context(request,'candidates',candidate=data,feedback_form=form))


@staff
@require_http_methods(['GET','POST'])
def disclosure(request,candidate_id):
    candidate=candidate_for(request.user,candidate_id,write=True)
    form=DisclosureForm(request.POST if request.POST.get('operation') in {'preview','release'} else None,
        actor=request.recruiting_actor,
        initial={'expected_revision':candidate.revision,'expected_need_revision':candidate.opportunity.need.effective_revision_id,'categories':['summary','region','experience']})
    revoke_form=RevokeForm(request.POST if request.POST.get('operation')=='revoke' else None)
    hire_form=ConfirmHireForm(request.POST if request.POST.get('operation')=='confirm_hire' else None)
    feedback_review=FeedbackReviewForm(request.POST if request.POST.get('operation')=='review_feedback' else None)
    preview=None
    if request.method=='POST':
        operation=request.POST.get('operation')
        target=form if operation in {'preview','release'} else revoke_form if operation=='revoke' else hire_form if operation=='confirm_hire' else feedback_review if operation=='review_feedback' else None
        if not target:raise PermissionDenied
        if target.is_valid():
            d=target.cleaned_data
            try:
                if operation=='preview':
                    if d['expected_revision']!=candidate.revision:raise ValidationError('Reload the current recruiting record.')
                    if str(d['expected_need_revision'])!=str(candidate.opportunity.need.effective_revision_id):raise ValidationError('Review the current approved position before previewing this release.')
                    preview=employer.build_projection(request.recruiting_actor,person(candidate.participant_id),d['categories'])
                    form.data=form.data.copy();form.data['expected_projection']=employer.digest(preview)
                elif operation=='release':
                    employer.release(actor=request.user,candidate_id=candidate.pk,categories=d['categories'],purpose=d['purpose'],expected_revision=d['expected_revision'],expected_need_revision=d['expected_need_revision'],expected_projection=d['expected_projection'],confirmed=d['confirmed'])
                elif operation=='revoke':
                    get_object_or_404(CandidateDisclosure,pk=d['disclosure'],candidate=candidate)
                    employer.revoke(actor=request.user,disclosure_id=d['disclosure'],expected_revision=d['expected_revision'],why=d['reason'])
                elif operation=='review_feedback':
                    get_object_or_404(EmployerCandidateFeedback,pk=d['feedback'],disclosure__candidate=candidate)
                    employer.review_feedback(actor=request.user,feedback_id=d['feedback'],why=d['reason'])
                else:
                    get_object_or_404(EmployerCandidateFeedback,pk=d['feedback'],disclosure__candidate=candidate)
                    employer.confirm_hire(actor=request.user,feedback_id=d['feedback'],expected_candidate_revision=d['expected_candidate_revision'],why=d['reason'])
            except ValidationError as exc:target.add_error(None,exc)
            else:
                if operation!='preview':messages.success(request,'Employer disclosure decision recorded.');return redirect('recruiting:disclosure',candidate_id=candidate.pk)
    grants=[]
    response_ids=[str(pk) for pk in EmployerCandidateFeedback.objects.filter(disclosure__candidate=candidate).values_list('pk',flat=True)]
    reviewed=set(employer.feedback_reviews().filter(object_id__in=response_ids).values_list('object_id',flat=True))
    for row in employer.grant_query().filter(candidate=candidate).order_by('-released_at'):
        feedback=list(row.feedback.select_related('membership__identity','confirmed_by').order_by('-created_at')[:30])
        for event in feedback:event.staff_reviewed=str(event.pk) in reviewed
        grants.append({'record':row,'available':employer.valid_disclosure(row),'projection':employer.staff_projection(request.recruiting_actor,row.approved_projection),
            'revoke_form':revoke_form if revoke_form.is_bound and str(revoke_form.data.get('disclosure'))==str(row.pk) else RevokeForm(initial={'disclosure':row.pk,'expected_revision':row.revision}),
            'members':CustomerMembership.objects.filter(organization_id=row.organization_id,state='enabled',accepted_at__isnull=False).select_related('identity'),
            'feedback':feedback})
    return page(request,'recruiting/disclosure.html',page_title='Employer Candidate Release',candidate=candidate,form=form,preview=preview,grants=grants,hire_form=hire_form,feedback_review_form=feedback_review)


@staff
@require_http_methods(['GET','POST'])
def member_permission(request,disclosure_id,membership_id):
    grant=get_object_or_404(CandidateDisclosure,pk=disclosure_id)
    candidate_for(request.user,grant.candidate_id,write=True)
    membership=get_object_or_404(CustomerMembership,pk=membership_id,organization_id=grant.organization_id,state='enabled',accepted_at__isnull=False)
    record=DisclosureMemberPermission.objects.filter(disclosure=grant,membership=membership).first()
    form=MemberPermissionForm(request.POST or None,initial={'expected_revision':record.revision if record else 0,'can_respond':record.can_respond if record else False})
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data
        try:employer.member_permission(actor=request.user,disclosure_id=grant.pk,membership_id=membership.pk,can_respond=d['can_respond'],expected_revision=d['expected_revision'],why=d['reason'])
        except ValidationError as exc:form.add_error(None,exc)
        else:return redirect('recruiting:disclosure',candidate_id=grant.candidate_id)
    return page(request,'recruiting/form.html',page_title='Candidate response permission · '+str(membership.identity),form=form,button='Save scoped permission')


@staff
@require_http_methods(['GET','POST'])
def withdrawal(request,participant_id):
    fresh_staff(request.user,administrator=True)
    participant=person(participant_id)
    form=WithdrawalForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:employer.record_withdrawal(actor=request.user,participant_id=participant.pk,why=form.cleaned_data['reason'])
        except ValidationError as exc:form.add_error(None,exc)
        else:messages.success(request,'The existing withdrawal was recorded; active employer releases were revoked.');return redirect('recruiting:candidate',participant_id=participant.pk)
    return page(request,'recruiting/form.html',page_title='Record existing sharing withdrawal · '+str(participant),form=form,button='Record withdrawal')
