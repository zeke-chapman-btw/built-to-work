"""Administrator-only, read-only review of a private synthetic customer fixture."""
from contextlib import contextmanager
from datetime import timedelta
import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.roles import lock_role_groups
from apps.core.audit import record_audit
from apps.participants.portal_preview import enabled, SESSION_KEY as PARTICIPANT_KEY
from . import services as service, invitations
from .collaboration import publish_feedback
from .models import CustomerIdentity, CustomerMembership, CustomerAccessToken, WorkforceNeedRevision

SESSION_KEY = 'btw_customer_portal_preview'
PREVIEW_LAST_NAME = 'Admin Customer Preview'


def require_admin(user):
    if not getattr(user, 'is_authenticated', False):
        raise PermissionDenied('Administrator sign-in is required.')
    fresh = get_user_model().objects.filter(pk=user.pk, is_active=True, is_staff=True, is_superuser=True).first()
    if not fresh or fresh.get_session_auth_hash() != user.get_session_auth_hash():
        raise PermissionDenied('Customer Portal Preview is available to current BTW administrators only.')
    return fresh


def fixture_email(administrator):
    return f'customer-preview-{administrator.pk}@example.test'


def fixture_name(administrator):
    return f'SYNTHETIC — Admin Customer Preview {administrator.pk}'


def validate_fixture(identity, administrator):
    require_admin(administrator)
    if not enabled() or identity.email != fixture_email(administrator):
        raise PermissionDenied('This customer preview is unavailable.')
    members = list(CustomerMembership.objects.filter(identity=identity).select_related('organization'))
    user = identity.user
    if (len(members) != 1 or identity.first_name != 'SYNTHETIC'
            or identity.last_name != PREVIEW_LAST_NAME or not identity.activated_at
            or not user.is_active or user.is_staff or user.is_superuser or user.email
            or not user.has_usable_password() or user.groups.exists() or user.user_permissions.exists()):
        raise PermissionDenied('The synthetic customer fixture needs administrator review.')
    member = members[0]
    org = member.organization
    if (org.name != fixture_name(administrator) or org.status != 'active' or not org.provisioned
            or org.primary_lead_id != member.pk or member.role != 'lead'
            or member.state != 'enabled' or not member.accepted_at):
        raise PermissionDenied('The synthetic company fixture needs administrator review.')
    # Never turn an edited fixture into a route to unrelated customer identities.
    if (org.memberships.exclude(identity__email__endswith='@example.test').exists()
            or org.campaigns.exclude(name__startswith='SYNTHETIC').exists()
            or WorkforceNeedRevision.objects.filter(organization=org).exclude(title__startswith='SYNTHETIC').exists()
            or org.contacts.exclude(email__endswith='@example.test').exists()):
        raise PermissionDenied('Only synthetic customer data is available in this preview.')
    from apps.participants.models import ParticipantAccount
    from apps.staffing.models import StaffProfile
    if ParticipantAccount.objects.filter(user=user).exists() or StaffProfile.objects.filter(user=user).exists():
        raise PermissionDenied('The synthetic identity needs administrator review.')
    return member


@transaction.atomic
def fixture_for(administrator):
    administrator = require_admin(administrator)
    if not enabled():
        raise PermissionDenied('Portal Preview is disabled outside the approved preview environment.')
    lock_role_groups()
    get_user_model().objects.select_for_update().get(pk=administrator.pk)
    identity = CustomerIdentity.objects.select_related('user').filter(email=fixture_email(administrator)).first()
    if identity:
        return validate_fixture(identity, administrator)

    why = 'Initialize private synthetic administrator customer preview; no external invitation or account credentials shared.'
    org = service.save_organization(actor=administrator, data={
        'name': fixture_name(administrator), 'industry': 'Synthetic preview only',
        'description': 'Private read-only Customer Portal review fixture. Not a real customer.'}, why=why)
    plan = org.access_plan
    service.save_access_plan(actor=administrator, org_id=org.pk, expected_org_revision=org.revision,
        expected_revision=plan.revision, data={'lead_limit': 1, 'member_limit': 1, 'workforce_need_creation': True}, why=why)
    org.refresh_from_db()
    member = service.save_membership(actor=administrator, org_id=org.pk, expected_org_revision=org.revision,
        role='lead', designate_primary=True, identity_data={
            'first_name': 'SYNTHETIC', 'last_name': PREVIEW_LAST_NAME, 'email': fixture_email(administrator)}, why=why)
    org.refresh_from_db()
    org = service.save_organization(actor=administrator, org_id=org.pk, expected_revision=org.revision,
        data={'status': 'active'}, why=why)

    # This is a clearly synthetic fixture, not real customer onboarding. Exercise
    # the existing acceptance guards without issuing mail or exposing a password.
    identity = member.identity
    raw = secrets.token_urlsafe(48)
    CustomerAccessToken.objects.create(identity=identity, membership=member, purpose='invite',
        token_digest=invitations.digest(raw), email=identity.email,
        security_revision=identity.user.security_revision, expires_at=timezone.now() + timedelta(hours=24),
        delivery='captured')
    invitations.accept_invitation(raw=raw, password=secrets.token_urlsafe(48))
    org.refresh_from_db()
    service.save_membership(actor=administrator, org_id=org.pk, expected_org_revision=org.revision,
        role='member', identity_data={'first_name': 'SYNTHETIC', 'last_name': 'Pending preview Member',
            'email': f'customer-preview-member-{administrator.pk}@example.test'}, why=why)
    org.refresh_from_db()
    need = service.save_hiring_draft(actor=administrator, org_id=org.pk,
        expected_org_revision=org.revision, data={'title': 'SYNTHETIC Welder', 'trade': 'Welding',
            'description': 'Synthetic hiring draft for portal review.', 'worker_quantity': 2,
            'employment_type': 'full_time', 'location_mode': 'custom', 'location_name': 'SYNTHETIC workshop',
            'city': 'Example City', 'location_state': 'GA', 'country': 'United States'}, why=why)
    org.refresh_from_db(); need.refresh_from_db()
    publish_feedback(actor=administrator, org_id=org.pk, expected_org_revision=org.revision,
        kind='need', record_id=need.pk, expected_revision=need.revision,
        body='SYNTHETIC feedback: review how customer-visible comments appear here.', action='feedback')
    org.refresh_from_db()
    service.save_campaign(actor=administrator, org_id=org.pk, expected_org_revision=org.revision,
        data={'name': 'SYNTHETIC Regional Hiring', 'description': 'Private synthetic Campaign draft.',
            'objectives': 'Review the customer-facing planning layout.'}, why=why)
    identity.refresh_from_db()
    member = validate_fixture(identity, administrator)
    record_audit(actor=administrator, action='customer.preview_fixture_created', instance=member.organization,
        source='admin_portal_preview', new_data={'synthetic': True, 'read_only': True}, reason=why)
    return member


def selected_fixture(request):
    administrator = require_admin(request.user)
    selected = request.session.get(SESSION_KEY)
    if not enabled() or not isinstance(selected, dict) or selected.get('administrator') != str(administrator.pk):
        raise PermissionDenied('This customer preview is unavailable. Return to Admin and open it again.')
    try:
        age = timezone.now().timestamp() - float(selected['started'])
        identity = CustomerIdentity.objects.select_related('user').get(pk=selected['identity'], email=fixture_email(administrator))
    except (KeyError, TypeError, ValueError, ValidationError, CustomerIdentity.DoesNotExist):
        raise PermissionDenied('Invalid customer preview session.')
    if not 0 <= age <= 3600:
        raise PermissionDenied('This preview expired. Return to Admin and open it again.')
    return validate_fixture(identity, administrator)


@contextmanager
def review_context(request):
    if not request.session.get(SESSION_KEY):
        yield
        return
    member = selected_fixture(request)
    if request.method not in {'GET', 'HEAD'}:
        raise PermissionDenied('Customer Portal Preview is view-only. Return to Admin to manage customer records.')
    original = request.user
    request.user = member.identity.user
    request.customer_preview = True
    # Selection is local to this synthetic review; the authenticated session is
    # never replaced with the synthetic user or customer authentication backend.
    request.session['customer_organization'] = str(member.organization_id)
    try:
        yield
    finally:
        request.user = original
        request.customer_preview = False


@never_cache
@login_required(login_url='accounts:login')
@require_POST
def enter(request):
    member = fixture_for(request.user)
    request.session.pop(PARTICIPANT_KEY, None)
    request.session[SESSION_KEY] = {'administrator': str(request.user.pk),
        'identity': str(member.identity_id), 'started': timezone.now().timestamp()}
    record_audit(actor=request.user, action='customer.preview_entered', instance=member.organization,
        source='admin_portal_preview', new_data={'synthetic': True, 'read_only': True})
    return redirect('customer_portal:home')


@never_cache
@login_required(login_url='accounts:login')
@require_POST
def exit(request):
    administrator = require_admin(request.user)
    request.session.pop(SESSION_KEY, None)
    request.session.pop('customer_organization', None)
    record_audit(actor=administrator, action='customer.preview_exited', instance=administrator,
        source='admin_portal_preview', new_data={'synthetic': True})
    return redirect('internal:portal-preview')
