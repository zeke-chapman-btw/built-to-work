"""Credential-proven realm resolution; never link identities by their email."""
import secrets
import time
from urllib.parse import unquote, urlsplit
from django import forms
from django.contrib.auth import get_user_model, login
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Count, Max
from django.db.models.functions import Upper
from django.urls import reverse, resolve, Resolver404
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_variables
from .staff_services import rate_limit, audit

PENDING = 'btw_unified_credential_choice_v1'
TTL = 300
GENERIC = 'Unable to sign in. Check your details or try again later.'
BACKENDS = {'staff': 'django.contrib.auth.backends.ModelBackend',
    'customer': 'apps.customers.auth.CustomerAuthenticationBackend',
    'participant': 'apps.participants.auth.ParticipantAuthenticationBackend'}


class LoginForm(forms.Form):
    username = forms.CharField(label='Email or username', max_length=254,
        widget=forms.TextInput(attrs={'autocomplete': 'username', 'autocapitalize': 'none', 'spellcheck': 'false', 'autofocus': True}))
    password = forms.CharField(max_length=4096, strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))


def submitted_data(request):
    data = request.POST.copy()
    # Compatibility input names do not carry authentication authority.
    values = {data.get(k, '').strip() for k in ('username', 'identifier', 'email') if data.get(k, '').strip()}
    data['username'] = next(iter(values)) if len(values) == 1 else ''
    return data


def allowed_attempt(request, identifier):
    from . import login_views
    from apps.customers import portal_auth as customer_auth
    from apps.participants.portal_accounts import throttle
    key = identifier.strip().casefold()
    ip = request.META.get('REMOTE_ADDR', '')
    # Evaluate every counter: no account-type parameter can reset the budget.
    checks = [rate_limit('unified-login-ip', ip, 30), rate_limit('unified-login-id', key, 10),
        login_views.rate_limit('login-ip', ip, 30), login_views.rate_limit('login-user', key, 10),
        customer_auth.rate_limit('customer-login-ip', ip, 30), customer_auth.rate_limit('customer-login-email', key, 10),
        throttle('login-ip', ip, limit=40), throttle('login-email', key, limit=12)]
    return all(checks)


def eligible(realm, user):
    if not user:
        return False
    if realm == 'staff':
        from .login_views import StaffLoginForm
        from apps.customers.models import CustomerIdentity
        try:
            StaffLoginForm().confirm_login_allowed(user)
            return not CustomerIdentity.objects.filter(user=user).exists()
        except forms.ValidationError:
            return False
    if realm == 'customer':
        from apps.customers.portal_policy import identity_for, eligible_memberships
        try:
            identity_for(user)
            return eligible_memberships(user).exists()
        except PermissionDenied:
            return False
    from apps.participants.portal_accounts import active_account
    return bool(active_account(user))


@sensitive_variables()
def authenticate_all(request, identifier, password):
    from apps.customers.auth import CustomerAuthenticationBackend
    from apps.participants.auth import ParticipantAuthenticationBackend
    User = get_user_model()
    # Username compatibility is exact; email lookup is case-insensitive. Never first-match email.
    rows = list(User.objects.filter(Q(username=identifier) | Q(email__iexact=identifier), is_staff=True).order_by('pk'))
    peak = User.objects.filter(is_staff=True).exclude(email='').annotate(address=Upper('email')).values('address').annotate(total=Count('pk')).aggregate(peak=Max('total'))['peak'] or 0
    budget = max(2, peak + 1)  # Includes a possible separate username match.
    found = []
    # The work envelope depends on the whole staff directory, not the supplied identifier.
    # Check every matching identity; no duplicate-address limit or first-match fallback.
    for index in range(max(budget, len(rows))):
        row = rows[index] if index < len(rows) else None
        if row and row.has_usable_password():
            user = ModelBackend().authenticate(request, username=row.username, password=password)
            identity_ok = rate_limit('unified-login-account', str(row.pk), 10)
            if user and identity_ok and eligible('staff', user):
                found.append(('staff', user))
        else:
            User().set_password(password)
    for realm, backend, key in [('customer', CustomerAuthenticationBackend(), 'customer_email'),
                                ('participant', ParticipantAuthenticationBackend(), 'participant_email')]:
        try:
            user = backend.authenticate(request, password=password, **{key: identifier})
        except (ValidationError, ValueError):
            # Invalid email syntax is a generic failure, including username-only sign-in.
            User().set_password(password)
            user = None
        if user:
            identity_ok = rate_limit('unified-login-account', str(user.pk), 10)
            if identity_ok and eligible(realm, user):
                found.append((realm, user))
    return [{'realm': realm, 'user': str(user.pk), 'proof': user.get_session_auth_hash()} for realm, user in found]


@sensitive_variables()
def fresh_identity(item):
    from apps.customers.auth import CustomerAuthenticationBackend
    from apps.participants.auth import ParticipantAuthenticationBackend
    realm = item.get('realm')
    backend = {'staff': ModelBackend, 'customer': CustomerAuthenticationBackend,
               'participant': ParticipantAuthenticationBackend}.get(realm)
    if not backend:
        return None
    user = backend().get_user(item.get('user'))
    return user if user and constant_time_compare(item.get('proof', ''), user.get_session_auth_hash()) and eligible(realm, user) else None


def safe_destination(request, value, realm, user):
    """Validate URL/realm first; existing record/membership guards remain authoritative."""
    if not value or not value.startswith('/') or value.startswith('//') or not url_has_allowed_host_and_scheme(value, {request.get_host()}, require_https=request.is_secure()):
        return None
    path = unquote(urlsplit(value).path)
    if '\\' in path or any(p in ('.', '..') for p in path.split('/')):
        return None
    try:
        match = resolve(path)
    except Resolver404:
        return None
    if realm == 'customer':
        if match.namespace != 'customer_portal' or '/customer/auth/' in path or path.startswith('/customer/staff/'):
            return None
        from apps.customers.portal_policy import eligible_memberships, require_membership
        from apps.customers.models import WorkforceNeed, Campaign, CustomerMembership
        members = list(eligible_memberships(user))
        if not members:
            return None
        org_id = members[0].organization_id
        if match.kwargs.get('record_id'):
            model = WorkforceNeed if match.url_name.startswith('need') else Campaign if match.url_name.startswith('campaign') else CustomerMembership if match.url_name.startswith('member') else None
            if match.url_name=='recruiting-candidate':
                from apps.recruiting.models import CandidateDisclosure
                model=CandidateDisclosure
            obj = model.objects.filter(pk=match.kwargs['record_id']).first() if model else None
            if not obj:
                return None
            org_id = obj.organization_id
        action = 'needs' if match.url_name in {'need-create', 'need-edit'} else 'campaigns' if match.url_name in {'campaign-create', 'campaign-edit'} else 'members' if match.url_name in {'invite', 'member-edit'} else None
        if match.url_name.endswith('action') or match.url_name == 'switch':
            return None
        try:
            require_membership(user, org_id, action=action)
        except (PermissionDenied, ValidationError):
            return None
        request.session['customer_organization'] = str(org_id)
        return value
    if realm == 'participant':
        if match.namespace=='recruiting' and match.url_name in {'portal-job','portal-preferences'}:
            from apps.recruiting.access import fresh_participant
            try:fresh_participant(user)
            except PermissionDenied:return None
            return value
        if match.namespace != 'participants' or not (path == reverse('participants:profile') or path.startswith('/participants/portal/')):
            return None
        # Own-profile and list destinations are scope-filtered by existing portal views.
        if match.kwargs or any(x in path for x in ('/preview/', '/account/email/', '/auth/')):
            return None
        return value
    from .login_views import safe_next
    from .roles import manager
    if path.startswith('/customer/') or path.startswith('/admin/') or '/portal/' in path:
        return None
    if path.startswith(('/accounts/staff/', '/workdays/compensation/', '/reports/categories/')) and not manager(user):
        return None
    return safe_next(request, value)


@sensitive_variables()
def establish(request, item, destination=''):
    from .roles import lock_role_groups
    with transaction.atomic():
        lock_role_groups()
        get_user_model().objects.select_for_update().filter(pk=item['user']).first()
        user = fresh_identity(item)
        if not user:
            return None
        # A choice proves exactly one realm; no previous portal/organization context survives.
        request.session.pop(PENDING, None)
        request.session.pop('customer_organization', None)
        login(request, user, backend=BACKENDS[item['realm']])
        if item['realm'] == 'staff':
            from .login_views import home
            audit(user, 'signed_in', user, reason='Account holder signed in through unified BTW login.')
            default = home(user)
        elif item['realm'] == 'customer':
            from apps.core.audit import record_audit
            from apps.customers.portal_policy import identity_for
            record_audit(actor=user, action='customer.signed_in', instance=identity_for(user), source='customer_authentication', reason='Customer signed in through unified BTW login.')
            default = reverse('customer_portal:home')
        else:
            default = reverse('participants:profile')
        return safe_destination(request, destination, item['realm'], user) or default


def begin_choice(request, identities, destination):
    request.session.cycle_key()
    request.session[PENDING] = {'created': time.time(), 'nonce': secrets.token_urlsafe(32),
                                'identities': identities, 'next': destination}


def pending_choice(request):
    data = request.session.get(PENDING)
    if not isinstance(data, dict) or not 0 <= time.time() - data.get('created', 0) <= TTL:
        request.session.pop(PENDING, None)
        return None
    return data


def choose(request, nonce, value):
    pending = pending_choice(request)
    request.session.pop(PENDING, None)
    if not pending or not constant_time_compare(str(nonce), pending['nonce']):
        return None
    try:
        item = pending['identities'][int(value)]
        if str(int(value)) != str(value) or int(value) < 0:
            return None
    except (ValueError, TypeError, IndexError):
        return None
    # Database-locked one-use guard also rejects concurrent replay of an old session snapshot.
    if not rate_limit('unified-choice-used', pending['nonce'], 1, minutes=15):
        return None
    return establish(request, item, pending.get('next', ''))
