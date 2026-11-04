from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render,redirect
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
    return render(request,'recruiting/portal/jobs.html',{'section':'jobs','jobs':Paginator(rows,20).get_page(request.GET.get('page')),'query_without_page':query.urlencode(),
        'interests':[jobs.interest_projection(r) for r in jobs.own_interests(request.user)[:30]],'filters':request.GET})


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
    from .foundation import readiness
    return render(request,'recruiting/portal/job.html',{'section':'jobs','job':job,'form':form,
        'interest':jobs.interest_projection(interest) if interest else None,
        'intake_ready':readiness(request.portal_account.participant)['eligible']})


@participant_view(allow_identity_review=True)
@require_http_methods(['POST'])
def withdraw(request,interest_id):
    jobs.withdraw_interest(actor=request.user,interest_id=interest_id)
    messages.success(request,'Your interest was withdrawn. BTW can review any existing recruiting activity separately.')
    return redirect('participants:portal-jobs')
