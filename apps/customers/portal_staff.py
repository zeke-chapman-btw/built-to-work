"""Staff-only entry points for invitations and deliberately public feedback."""
from django.contrib import messages
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_http_methods
from .views import staff_view, org_for, context, add_errors
from .models import CustomerMembership as Membership, WorkforceNeed, Campaign
from .portal_forms import FeedbackForm, ActionForm
from . import invitations as invites
from .collaboration import publish_feedback


@staff_view
@require_http_methods(['GET'])
def members(request, org_id):
    org = org_for(request, org_id)
    rows = []
    for m in org.memberships.select_related('identity'):
        token = m.invitations.filter(purpose='invite').order_by('-created_at').first()
        rows.append({'member': m, 'token': token})
    return render(request, 'customers/portal/staff_members.html', context(request, org, 'users', invitation_rows=rows))


@staff_view
@require_http_methods(['GET', 'POST'])
def invitation_action(request, org_id, record_id, action):
    org = org_for(request, org_id)
    obj = get_object_or_404(Membership, organization=org, pk=record_id)
    if action not in {'resend', 'revoke'}:
        from django.http import Http404
        raise Http404
    form = ActionForm(request.POST if request.method == 'POST' else None, initial={'org_revision': org.revision, 'record_revision': obj.revision})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        common = dict(actor=request.user, org_id=org.pk, expected_org_revision=d['org_revision'], membership_id=obj.pk, expected_revision=d['record_revision'])
        try:
            invites.revoke_invitation(**common) if action == 'revoke' else invites.invite_membership(**common, origin=request.build_absolute_uri('/').rstrip('/'))
        except (ValidationError, ObjectDoesNotExist) as error:
            form.add_error(None, error.messages if isinstance(error, ValidationError) else 'This membership is no longer available.')
        else:
            messages.success(request, 'Invitation action completed. Review its delivery status; test capture is not external delivery.')
            return redirect('customer_portal:staff-members', org.pk)
    return render(request, 'customers/portal/staff_form.html', context(request, org, 'users', title=action.title() + ' customer invitation', form=form,
        notice='This prepared membership already reserves its seat. Expiration, revocation and replacement do not release it. Suspend or remove the membership explicitly to release the seat. Primary Lead protections still apply.'))


@staff_view
@require_http_methods(['GET', 'POST'])
def feedback(request, org_id, kind, record_id):
    org = org_for(request, org_id)
    if kind not in {'need', 'campaign'}:
        from django.http import Http404
        raise Http404
    obj = get_object_or_404(WorkforceNeed if kind == 'need' else Campaign, organization=org, pk=record_id)
    form = FeedbackForm(request.POST if request.method == 'POST' else None, initial={'org_revision': org.revision, 'record_revision': obj.revision})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            publish_feedback(actor=request.user, org_id=org.pk, expected_org_revision=d['org_revision'], kind=kind, record_id=obj.pk, expected_revision=d['record_revision'], body=d['body'], action=d['action'])
        except (ValidationError, ObjectDoesNotExist) as error:
            form.add_error(None, error.messages if isinstance(error, ValidationError) else 'This record is no longer available.')
        else:
            messages.success(request, 'Customer-visible feedback published.')
            return redirect('customers:need' if kind == 'need' else 'customers:campaign', org.pk, obj.pk)
    return render(request, 'customers/portal/staff_form.html', context(request, org, 'needs' if kind == 'need' else 'campaigns', title='Customer-visible review feedback', form=form,
        notice='Publish only text appropriate for every authorized customer user of this company. This is separate from private internal reasons and notes. Published feedback is retained in history.'))
