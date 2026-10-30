"""Shared Customer Portal. Read projections and locked mutations remain separate."""
from functools import wraps
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError, ObjectDoesNotExist
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from . import portal_forms as f, services as s, invitations as invites, projections as p
from .collaboration import campaign_submission
from .portal_policy import identity_for, eligible_memberships, require_membership
from .models import WorkforceNeed as Need, WorkforceNeedRevision as Revision, Campaign, CustomerMembership as Membership, TERM_FIELD_NAMES

TABS = [('overview', 'Overview'), ('company', 'Company Information'), ('needs', 'Workforce Needs'),
        ('campaigns', 'Campaigns'), ('users', 'Users & Membership'), ('access', 'Access & Plan'), ('history', 'History & Feedback')]


def customer_view(view):
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(reverse('accounts:login') + '?account_type=customer')
        if request.session.get('_auth_user_backend') != 'apps.customers.auth.CustomerAuthenticationBackend':
            raise PermissionDenied('Use Customer sign in for this workspace.')
        identity_for(request.user)
        companies = list(eligible_memberships(request.user))
        selected = request.session.get('customer_organization')
        if selected is None and companies:
            selected = str(companies[0].organization_id)
            request.session['customer_organization'] = selected
        request.customer_companies = [{'id': str(m.organization_id), 'name': m.organization.name} for m in companies]
        try:
            request.customer_member = require_membership(request.user, selected) if selected else None
        except (PermissionDenied, ValidationError):
            request.customer_member = None
        if not request.customer_member and view.__name__ != 'switch_company':
            response = render(request, 'customers/portal/no_access.html', {'companies': request.customer_companies}, status=403)
        else:
            response = view(request, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        response['Referrer-Policy'] = 'same-origin'
        return response
    return guarded


def context(request, tab='overview', **extra):
    m = request.customer_member
    return {'company': {'id': m.organization_id, 'name': m.organization.name}, 'companies': request.customer_companies,
            'active_tab': tab, 'tabs': TABS, 'is_lead': m.role == 'lead',
            'can_needs': (m.role == 'lead' or m.manage_needs) and m.organization.access_plan.workforce_need_creation is True,
            'can_campaigns': m.role == 'lead' or m.manage_campaigns, **extra}


def organization(request):
    return require_membership(request.user, request.customer_member.organization_id).organization


@customer_view
@require_POST
def switch_company(request):
    form = f.SwitchForm(request.POST)
    if not form.is_valid():
        raise PermissionDenied('Choose an authorized company.')
    require_membership(request.user, form.cleaned_data['organization'])
    request.session['customer_organization'] = str(form.cleaned_data['organization'])
    return redirect('customer_portal:home')


@customer_view
@require_http_methods(['GET'])
def workspace(request, tab='overview'):
    if tab not in dict(TABS):
        raise Http404
    org = organization(request)
    data = context(request, tab)
    if tab in {'overview', 'needs'}:
        rows = p.needs(request.user, org.pk)
        data['needs'] = rows[:5] if tab == 'overview' else Paginator(rows, 20).get_page(request.GET.get('page'))
    if tab in {'overview', 'campaigns'}:
        rows = p.campaigns(request.user, org.pk)
        data['campaigns'] = rows[:5] if tab == 'overview' else Paginator(rows, 20).get_page(request.GET.get('page'))
    if tab == 'overview':
        data['attention'] = [{'label': n['title'] + ' · ' + n['review'], 'url': reverse('customer_portal:need', args=[n['id']])} for n in p.needs(request.user, org.pk) if n['review_state'] in {'draft', 'returned', 'submitted', 'review'}][:5]
        data['attention'] += [{'label': c['name'] + ' · ' + c['review'], 'url': reverse('customer_portal:campaign', args=[c['id']])} for c in p.campaigns(request.user, org.pk) if c['review_state'] in {'draft', 'submitted', 'review', 'returned'}][:5]
        data['recent'] = p.history(request.user, org.pk)[:5]
    if tab == 'company':
        data['details'] = {key: getattr(org, key) for key in ('name', 'legal_name', 'industry', 'website', 'business_email', 'phone', 'description')}
        data['locations'] = list(org.locations.filter(is_active=True).values('name', 'kind', 'address_line1', 'address_line2', 'city', 'state', 'postal_code', 'country'))
        data['contacts'] = list(org.contacts.filter(is_active=True).values('first_name', 'last_name', 'title', 'email', 'phone'))
    if tab == 'users':
        rows = []
        for m in org.memberships.exclude(state='removed').select_related('identity'):
            token = m.invitations.filter(purpose='invite').order_by('-created_at').first()
            token_state = ('Accepted' if m.accepted_at else 'Not invited')
            if token and not m.accepted_at:
                from django.utils import timezone
                token_state = ('Revoked' if token.revoked_at else 'Expired' if token.expires_at <= timezone.now() else token.get_delivery_display())
            rows.append({'id': m.pk, 'name': f'{m.identity.first_name} {m.identity.last_name}'.strip() if m.accepted_at else 'Invited ' + m.get_role_display(), 'email': m.identity.email,
                'role': m.get_role_display(), 'member_role': m.role == 'member', 'state': m.get_state_display(),
                'accepted': bool(m.accepted_at), 'invitation': token_state, 'manage_needs': m.manage_needs, 'manage_campaigns': m.manage_campaigns})
        data['members'] = rows
    if tab == 'access':
        plan = org.access_plan
        data['allowances'] = [{'label': 'Leads', 'used': s.seat_usage(org)['lead'], 'limit': plan.lead_limit}, {'label': 'Members', 'used': s.seat_usage(org)['member'], 'limit': plan.member_limit}]
        data['need_service'] = plan.workforce_need_creation is True
    if tab == 'history':
        data['recent'], data['feedback'] = p.history(request.user, org.pk), p.feedback(request.user, org.pk)
    return render(request, 'customers/portal/workspace.html', data)


def add_error(form, error):
    if isinstance(error, ObjectDoesNotExist):
        form.add_error(None, 'This record is no longer available. Reload before continuing.')
    elif hasattr(error, 'message_dict'):
        for key, value in error.message_dict.items():
            form.add_error(key if key in form.fields else None, value)
    else:
        form.add_error(None, error.messages)


def form_page(request, title, form, *, tab, notice='', save=None, success=None):
    if request.method == 'POST' and form.is_valid():
        try:
            obj = save(form.cleaned_data)
        except (ValidationError, ObjectDoesNotExist) as error:
            add_error(form, error)
        else:
            messages.success(request, 'Your change was saved.' )
            return redirect(success(obj) if success else reverse('customer_portal:workspace', args=[tab]))
    return render(request, 'customers/portal/form.html', context(request, tab, title=title, form=form, notice=notice))


@customer_view
@require_http_methods(['GET'])
def need_detail(request, record_id):
    org = organization(request)
    obj = get_object_or_404(Need.objects.select_related('effective_revision', 'working_revision'), organization=org, pk=record_id)
    revisions = [{'number': r.number, 'state': r.get_state_display()} for r in obj.revisions.order_by('-number')]
    latest = obj.revisions.order_by('-number').first()
    return render(request, 'customers/portal/need.html', context(request, 'needs', need=p.need_row(obj),
        editable=obj.status not in {'closed', 'cancelled'} and (not obj.working_revision or obj.working_revision.state == 'draft'),
        pending_state=obj.working_revision.state if obj.working_revision else '',
        effective=p.terms(obj.effective_revision), proposed=p.terms(obj.working_revision), previous=p.terms(latest) if not obj.working_revision_id and latest and latest.state == 'returned' else None, revisions=revisions,
        feedback=p.feedback(request.user, org.pk, need_id=obj.pk), recent=p.history(request.user, org.pk, need_id=obj.pk)))


@customer_view
@require_http_methods(['GET', 'POST'])
def need_editor(request, record_id=None):
    org = organization(request)
    require_membership(request.user, org.pk, action='needs')
    need = get_object_or_404(Need, pk=record_id, organization=org) if record_id else None
    working = need.working_revision if need else None
    if need and (need.status in {'closed', 'cancelled'} or (working and working.state != 'draft')):
        raise PermissionDenied('This revision cannot be edited. Withdraw it or wait for BTW review.')
    source = need.revisions.order_by('-number').first() if need else None
    instance = working or Revision(organization=org, **({key: getattr(source, key) for key in TERM_FIELD_NAMES} if source else {}))
    form = f.NeedForm(request.POST if request.method == 'POST' else None, actor=request.user, org_id=org.pk, instance=instance,
        initial={'org_revision': org.revision, 'record_revision': need.revision if need else None, 'working_id': working.pk if working else None})
    return form_page(request, 'Edit Workforce Need draft' if need else 'New Workforce Need', form, tab='needs',
        notice='Save an incomplete draft at any time. Submit separately when every required hiring detail is ready. Approved details remain effective while proposed changes are reviewed.',
        save=lambda d: s.save_hiring_draft(actor=request.user, org_id=org.pk, expected_org_revision=d['org_revision'], need_id=record_id,
            expected_need_revision=d['record_revision'], expected_working_id=d['working_id'], data={k:d[k] for k in f.EDITABLE_TERM_FIELDS}, why='Customer saved hiring-term draft.'),
        success=lambda obj: reverse('customer_portal:need', args=[obj.pk]))


@customer_view
@require_http_methods(['GET', 'POST'])
def need_action(request, record_id, action):
    if action not in {'submit', 'withdraw'}:
        raise PermissionDenied('BTW retains approval and closure authority.')
    org = organization(request)
    require_membership(request.user, org.pk, action='needs')
    obj = get_object_or_404(Need, pk=record_id, organization=org)
    form = f.ActionForm(request.POST if request.method == 'POST' else None, initial={'org_revision': org.revision, 'record_revision': obj.revision, 'working_id': obj.working_revision_id})
    return form_page(request, action.title() + ' Workforce Need', form, tab='needs', notice='Confirm this action on the current working revision. Submission does not approve your Need.',
        save=lambda d: s.revision_action(actor=request.user, org_id=org.pk, need_id=obj.pk, expected_org_revision=d['org_revision'], expected_need_revision=d['record_revision'], revision_id=d['working_id'], action=action, why='Customer explicitly ' + action + ' hiring revision.'),
        success=lambda obj: reverse('customer_portal:need', args=[obj.pk]))


@customer_view
@require_http_methods(['GET'])
def campaign_detail(request, record_id):
    org = organization(request)
    obj = get_object_or_404(Campaign, pk=record_id, organization=org)
    return render(request, 'customers/portal/campaign.html', context(request, 'campaigns', campaign=p.campaign_row(obj), review_state=obj.review_state,
        editable=obj.status not in {'completed', 'cancelled'} and obj.review_state not in {'submitted', 'review'},
        needs=[p.need_row(n) for n in obj.workforce_needs.select_related('effective_revision', 'working_revision')],
        feedback=p.feedback(request.user, org.pk, campaign_id=obj.pk), recent=p.history(request.user, org.pk, campaign_id=obj.pk)))


@customer_view
@require_http_methods(['GET', 'POST'])
def campaign_editor(request, record_id=None):
    org = organization(request)
    require_membership(request.user, org.pk, action='campaigns')
    obj = get_object_or_404(Campaign, organization=org, pk=record_id) if record_id else Campaign(organization=org)
    if record_id and (obj.status in {'completed', 'cancelled'} or obj.review_state in {'submitted', 'review'}):
        raise PermissionDenied('This planning version is frozen. Withdraw the submission or wait for BTW review.')
    form = f.CampaignForm(request.POST if request.method == 'POST' else None, actor=request.user, org_id=org.pk, instance=obj,
        initial={'org_revision': org.revision, 'record_revision': obj.revision if record_id else None})
    return form_page(request, 'Edit Campaign planning' if record_id else 'New Campaign', form, tab='campaigns',
        notice='Saving does not submit or approve this Campaign. Material planning changes invalidate approval and place an active Campaign on hold.',
        save=lambda d: s.save_campaign(actor=request.user, org_id=org.pk, expected_org_revision=d['org_revision'], campaign_id=record_id, expected_revision=d['record_revision'], data={k:d[k] for k in s.CAMPAIGN_FIELDS}, why='Customer saved Campaign planning.'),
        success=lambda obj: reverse('customer_portal:campaign', args=[obj.pk]))


@customer_view
@require_http_methods(['GET', 'POST'])
def campaign_action(request, record_id, action):
    if action not in {'submit', 'withdraw'}:
        raise PermissionDenied('Only BTW may review, approve or activate Campaigns.')
    org = organization(request)
    require_membership(request.user, org.pk, action='campaigns')
    obj = get_object_or_404(Campaign, pk=record_id, organization=org)
    form = f.ActionForm(request.POST if request.method == 'POST' else None, initial={'org_revision': org.revision, 'record_revision': obj.revision})
    return form_page(request, action.title() + ' Campaign', form, tab='campaigns', notice='Confirm the action on the current planning version. Only BTW can approve or activate a Campaign.',
        save=lambda d: campaign_submission(actor=request.user, org_id=org.pk, expected_org_revision=d['org_revision'], campaign_id=obj.pk, expected_revision=d['record_revision'], action=action),
        success=lambda obj: reverse('customer_portal:campaign', args=[obj.pk]))


@customer_view
@require_http_methods(['GET', 'POST'])
def member_invite(request):
    org = organization(request)
    require_membership(request.user, org.pk, action='members')
    form = f.InviteForm(request.POST if request.method == 'POST' else None, initial={'org_revision': org.revision})
    return form_page(request, 'Invite a Member', form, tab='users', notice='A pending invitation reserves one Member seat. Access starts only when the invited person accepts. New Members start with read access only.',
        save=lambda d: invites.invite_member(actor=request.user, org_id=org.pk, expected_org_revision=d['org_revision'], email=d['email'], first_name=d['first_name'], last_name=d['last_name'], origin=request.build_absolute_uri('/').rstrip('/')))


@customer_view
@require_http_methods(['GET', 'POST'])
def member_editor(request, record_id):
    org = organization(request)
    require_membership(request.user, org.pk, action='members')
    obj = get_object_or_404(Membership, pk=record_id, organization=org, role='member')
    form = f.MemberForm(request.POST if request.method == 'POST' else None, initial={'org_revision': org.revision, 'record_revision': obj.revision, 'state': obj.state, 'manage_needs': obj.manage_needs, 'manage_campaigns': obj.manage_campaigns})
    return form_page(request, 'Manage Member', form, tab='users', notice='These permissions are independent. They do not grant BTW approval authority or access to another company.',
        save=lambda d: invites.manage_member(actor=request.user, org_id=org.pk, expected_org_revision=d['org_revision'], membership_id=obj.pk, expected_revision=d['record_revision'], state=d['state'], manage_needs=d['manage_needs'], manage_campaigns=d['manage_campaigns']))


@customer_view
@require_http_methods(['GET', 'POST'])
def invitation_action(request, record_id, action):
    org = organization(request)
    require_membership(request.user, org.pk, action='members')
    obj = get_object_or_404(Membership, pk=record_id, organization=org, role='member')
    if action not in {'resend', 'revoke'}:
        raise Http404
    form = f.ActionForm(request.POST if request.method == 'POST' else None, initial={'org_revision': org.revision, 'record_revision': obj.revision})
    def save(d):
        args = dict(actor=request.user, org_id=org.pk, expected_org_revision=d['org_revision'], membership_id=obj.pk, expected_revision=d['record_revision'])
        return invites.revoke_invitation(**args) if action == 'revoke' else invites.invite_membership(**args, origin=request.build_absolute_uri('/').rstrip('/'))
    return form_page(request, action.title() + ' Member invitation', form, tab='users',
        notice='Expired, revoked and replaced invitations retain their prepared seat. Suspend or remove the membership explicitly to release it. Resend invalidates every older invitation for this membership.', save=save)
