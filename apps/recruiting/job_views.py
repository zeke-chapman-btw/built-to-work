from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.views.decorators.http import require_http_methods
from apps.participants.portal_views import participant_view
from . import jobs
from .access import fresh_participant


class InterestForm(forms.Form):
    publication=forms.UUIDField(widget=forms.HiddenInput)


def directory_content(request):
    # Preview identity contexts are synthetic; fresh service checks still apply.
    rows=jobs.browse(request.user,query=request.GET.get('q',''),trade=request.GET.get('trade',''),
        location=request.GET.get('location',''),employment_type=request.GET.get('employment_type',''))
    query=request.GET.copy();query.pop('page',None)
    view='interests' if request.GET.get('view')=='interests' else 'jobs'
    own=jobs.own_interests(request.user)
    history=Paginator(own,20).get_page(request.GET.get('interest_page')) if view=='interests' else None
    return render(request,'recruiting/portal/jobs.html',{'section':'jobs','jobs':Paginator(rows,20).get_page(request.GET.get('page')),'query_without_page':query.urlencode(),
        'interests':[jobs.interest_projection(r) for r in (history if history else own[:5])],'interest_page':history,'view':view,'filters':request.GET})


@participant_view(allow_identity_review=True)
@require_http_methods(['GET','POST'])
def detail(request,opportunity_id):
    jobs.account_for(request.user)
    item=jobs.published().filter(pk=opportunity_id).first()
    if not item:raise Http404
    job=jobs.projection(item)
    form=InterestForm(request.POST or None,initial={'publication':job['version']})
    if request.method=='POST' and form.is_valid():
        try:jobs.express_interest(actor=request.user,opportunity_id=item.pk,publication_id=form.cleaned_data['publication'])
        except ValidationError as exc:form.add_error(None,exc)
        else:
            messages.success(request,'Your interest was received. BTW will review your information; no employer submission has been made.')
            return redirect('recruiting:portal-job',opportunity_id=item.pk)
    interest=jobs.own_interests(request.user).filter(opportunity_id=item.pk,active=True).first()
    interest_data=jobs.interest_projection(interest) if interest else None
    if interest_data:interest_data['job_updated']=interest.publication_id!=item.publication_id
    from .foundation import readiness
    from apps.participants.portal_accounts import active_account
    return render(request,'recruiting/portal/job.html',{'section':'jobs','job':job,'form':form,
        'interest':interest_data,
        'intake_ready':readiness(request.portal_account.participant)['eligible'],
        'profile_editable':not getattr(request, 'portal_preview', None) and active_account(request.user) is not None})


@participant_view(allow_identity_review=True)
@require_http_methods(['GET'])
def interest_detail(request, interest_id):
    row = get_object_or_404(jobs.own_interests(request.user), pk=interest_id)
    from apps.participants.portal_accounts import active_account
    from apps.participants.identity import identity_members
    from .models import ProfileUpdateRequest
    from .foundation import freshness
    return render(request, 'recruiting/portal/interest.html', {'section': 'jobs',
        'interest': jobs.interest_projection(row),
        'current_job': jobs.published().filter(pk=row.opportunity_id).first(),
        'freshness': freshness(request.portal_account.participant),
        'profile_editable': not getattr(request, 'portal_preview', None) and active_account(request.user) is not None,
        'update_requests': ProfileUpdateRequest.objects.filter(participant_id__in=identity_members(request.portal_account.participant_id),
            fulfilled_by__isnull=True).order_by('-created_at', '-pk')[:5]})


@participant_view(allow_identity_review=True)
@require_http_methods(['POST'])
def withdraw(request,interest_id):
    jobs.withdraw_interest(actor=request.user,interest_id=interest_id)
    messages.success(request,'Your interest was withdrawn. BTW can review any existing recruiting activity separately.')
    return redirect('participants:portal-jobs')
