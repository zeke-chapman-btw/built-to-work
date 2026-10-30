"""Expiring, single-use customer links and serialized membership reservations."""
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlsplit
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from apps.accounts.roles import lock_role_groups
from . import services as s
from .access import require_manager
from .models import CustomerIdentity, CustomerMembership as Membership, CustomerAccessToken as Token
from .portal_policy import is_customer, identity_for, require_membership


def digest(raw):
    return hashlib.sha256(str(raw).encode()).hexdigest()


def _origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {'https', 'http'} or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {'', '/'}
            or (parsed.scheme != 'https' and parsed.hostname not in {'localhost', '127.0.0.1', 'testserver'})):
        raise ValidationError('A trusted application origin is required.')
    return parsed.scheme + '://' + parsed.netloc


def _member_authority(actor, org_id, expected_org_revision):
    if is_customer(actor):
        member = require_membership(actor, org_id, action='members', lock=True)
        s.expected(member.organization, expected_org_revision, 'company')
        return member.identity.user, member.organization, True
    actor, org = s.locked_org(actor, org_id, expected_org_revision)
    return actor, org, False


def _target(actor, org_id, expected_org_revision, membership_id):
    actor, org, customer = _member_authority(actor, org_id, expected_org_revision)
    item = Membership.objects.select_for_update().select_related('identity__user').get(pk=membership_id, organization=org)
    if customer and item.role != 'member':
        raise PermissionDenied('Only BTW may manage Leads.')
    return actor, org, item


def _deliver(token_id, raw, origin):
    item = Token.objects.select_related('membership__organization', 'identity').get(pk=token_id)
    path = reverse('customer_portal:accept' if item.purpose == 'invite' else 'customer_portal:reset-confirm', args=[raw])
    subject = 'Your Built to Work company invitation' if item.purpose == 'invite' else 'Recover your Built to Work customer access'
    text = ('Use this private single-use link within 24 hours:\n' + origin + path
            + '\nIf you did not expect this message, ignore it or contact BTW. Never share this link.')
    try:
        sent = send_mail(subject, text, None, [item.email], fail_silently=False)
        status = 'captured' if settings.EMAIL_BACKEND in {
            'django.core.mail.backends.locmem.EmailBackend', 'django.core.mail.backends.console.EmailBackend',
            'django.core.mail.backends.filebased.EmailBackend', 'django.core.mail.backends.dummy.EmailBackend'} else 'sent'
        if not sent:
            status = 'failed'
    except Exception:
        status = 'failed'  # Never retain provider errors, link secrets or credentials.
    with transaction.atomic():
        lock_role_groups()
        item = Token.objects.select_for_update(of=('self',)).select_related('membership__organization').get(pk=token_id)
        item.delivery = status
        item.save(update_fields=['delivery', 'updated_at'])
        if item.membership_id:
            s.audit(None, item.membership.organization, 'invitation.delivery', item,
                    after={'delivery': status}, why='Customer invitation transport result; no link retained in audit.')


def _issue(identity, membership, purpose, origin, actor=None):
    origin = _origin(origin)
    if membership:
        from apps.accounts.staff_services import rate_limit
        if not rate_limit('customer-invite-actor', actor.pk, 20, 60):
            raise ValidationError('Please wait before sending more customer invitations.')
    query = Token.objects.filter(identity=identity, purpose=purpose, consumed_at__isnull=True, revoked_at__isnull=True)
    if purpose == 'invite':
        query = query.filter(membership=membership)
    query.update(revoked_at=timezone.now())
    raw = secrets.token_urlsafe(48)
    item = Token.objects.create(identity=identity, membership=membership, purpose=purpose,
        token_digest=digest(raw), email=identity.email, security_revision=identity.user.security_revision,
        expires_at=timezone.now() + timedelta(hours=24))
    if membership:
        s.audit(actor, membership.organization, 'invitation.issued', item,
                after={'membership_id': str(membership.pk)}, why='Single-use customer invitation issued; existing seat reservation retained.')
    else:
        from apps.core.audit import record_audit
        record_audit(actor=None, action='customer.recovery_requested', instance=identity,
                     source='customer_authentication', reason='Customer recovery requested; private single-use link issued.')
    transaction.on_commit(lambda: _deliver(item.pk, raw, origin))
    return item


@transaction.atomic
def invite_membership(*, actor, org_id, expected_org_revision, membership_id, origin, expected_revision):
    actor, org, member = _target(actor, org_id, expected_org_revision, membership_id)
    s.expected(member, expected_revision, 'membership')
    if org.status != 'active' or not org.provisioned or member.state != 'enabled' or member.accepted_at:
        raise ValidationError('Invitations require an active company and an enabled, unaccepted membership.')
    identity = CustomerIdentity.objects.select_for_update().select_related('user').get(pk=member.identity_id)
    get_user_model().objects.select_for_update().get(pk=identity.user_id)
    return _issue(identity, member, 'invite', origin, actor)


@transaction.atomic
def invite_member(*, actor, org_id, expected_org_revision, email, first_name, last_name='', origin):
    actor, org, customer = _member_authority(actor, org_id, expected_org_revision)
    email = str(email).strip().casefold()
    identity = CustomerIdentity.objects.filter(email=email).first()
    existing = Membership.objects.filter(organization=org, identity=identity).first() if identity else None
    if existing:
        raise ValidationError('A membership is already prepared here. Use its management or resend action.')
    member = s.save_membership(actor=actor, org_id=org.pk, expected_org_revision=org.revision,
        role='member', identity_id=identity.pk if identity else None,
        identity_email=email, identity_data=None if identity else {'first_name': first_name, 'last_name': last_name, 'email': email},
        why='Member invitation requested by an authorized company Lead.' if customer else 'Member invitation requested by BTW.')
    return _issue(member.identity, member, 'invite', origin, actor)


@transaction.atomic
def revoke_invitation(*, actor, org_id, expected_org_revision, membership_id, expected_revision):
    actor, org, member = _target(actor, org_id, expected_org_revision, membership_id)
    s.expected(member, expected_revision, 'membership')
    if member.accepted_at:
        raise ValidationError('This membership was already accepted. Suspend access instead.')
    Token.objects.filter(membership=member, purpose='invite', consumed_at__isnull=True, revoked_at__isnull=True).update(revoked_at=timezone.now())
    s.audit(actor, org, 'invitation.revoked', member, why='Invitation revoked. Prepared seat retained until membership is explicitly suspended or removed.')


def valid_token(raw, purpose, *, lock=False):
    if not isinstance(raw, str) or not 40 <= len(raw) <= 100:
        raise ValidationError('This link is invalid, expired or already used. Request a fresh link.')
    query = Token.objects.select_related('identity__user', 'membership__organization')
    if lock:
        query = query.select_for_update(of=('self',))
    item = query.filter(token_digest=digest(raw), purpose=purpose, revoked_at__isnull=True,
        consumed_at__isnull=True, expires_at__gt=timezone.now()).first()
    if not item or item.email != item.identity.email or item.security_revision != item.identity.user.security_revision:
        raise ValidationError('This link is invalid, expired or already used. Request a fresh link.')
    if purpose == 'invite':
        m = item.membership
        if m.identity_id != item.identity_id or m.state != 'enabled' or m.accepted_at or m.organization.status != 'active' or not m.organization.provisioned:
            raise ValidationError('This invitation is not available. Contact BTW or your company Lead.')
    elif not item.identity.activated_at or not item.identity.user.is_active:
        raise ValidationError('This recovery link is not available.')
    return item


@transaction.atomic
def accept_invitation(*, raw, password=None, actor=None):
    lock_role_groups()
    item = valid_token(raw, 'invite', lock=True)
    identity = CustomerIdentity.objects.select_for_update().get(pk=item.identity_id)
    user = get_user_model().objects.select_for_update().get(pk=identity.user_id)
    org = type(item.membership.organization).objects.select_for_update().get(pk=item.membership.organization_id)
    member = Membership.objects.select_for_update().get(pk=item.membership_id, organization=org, identity=identity)
    # Re-read after the common security/organization locks.
    valid_token(raw, 'invite')
    if identity.activated_at:
        if not actor or identity_for(actor).pk != identity.pk or password is not None:
            raise PermissionDenied('Sign in with the invited customer identity to accept this company invitation.')
    else:
        if not password:
            raise ValidationError('Choose a password to activate your customer identity.')
        validate_password(password, user)
        identity.activated_at = timezone.now()
        identity.save(update_fields=['activated_at', 'updated_at'])
        user.set_password(password)
        user.is_active = True
        user.save(update_fields=['password', 'is_active'])
    member.accepted_at = timezone.now()
    s.touch(member, 'accepted_at')
    s.touch(org)
    item.consumed_at = timezone.now()
    item.save(update_fields=['consumed_at', 'updated_at'])
    s.audit(user, org, 'membership.accepted', member, why='Invited identity verified and explicitly accepted company membership; no additional seat consumed.')
    return member


@transaction.atomic
def request_recovery(*, email, origin):
    lock_role_groups()
    identity = CustomerIdentity.objects.select_for_update().select_related('user').filter(
        email=str(email).strip().casefold(), activated_at__isnull=False, user__is_active=True).first()
    if identity and not identity.user.is_staff and not identity.user.is_superuser:
        get_user_model().objects.select_for_update().get(pk=identity.user_id)
        _issue(identity, None, 'reset', origin)


@transaction.atomic
def reset_password(*, raw, password):
    lock_role_groups()
    item = valid_token(raw, 'reset', lock=True)
    identity = CustomerIdentity.objects.select_for_update().get(pk=item.identity_id)
    user = get_user_model().objects.select_for_update().get(pk=identity.user_id)
    valid_token(raw, 'reset')
    validate_password(password, user)
    user.set_password(password)
    user.security_revision += 1
    user.save(update_fields=['password', 'security_revision'])
    item.consumed_at = timezone.now()
    item.save(update_fields=['consumed_at', 'updated_at'])
    Token.objects.filter(identity=identity, consumed_at__isnull=True, revoked_at__isnull=True).update(revoked_at=timezone.now())
    from apps.core.audit import record_audit
    record_audit(actor=user, action='customer.password_reset', instance=identity, source='customer_authentication', reason='Customer recovered access; prior sessions and outstanding links invalidated.')


@transaction.atomic
def manage_member(*, actor, org_id, expected_org_revision, membership_id, expected_revision, state, manage_needs, manage_campaigns):
    actor, org, member = _target(actor, org_id, expected_org_revision, membership_id)
    s.expected(member, expected_revision, 'membership')
    if member.role != 'member' or state not in {'enabled', 'suspended', 'removed'}:
        raise PermissionDenied('Only Members may be managed here. BTW retains Lead authority.')
    if type(manage_needs) is not bool or type(manage_campaigns) is not bool:
        raise ValidationError('Choose both permission values explicitly.')
    before = s.seat_usage(org)
    after = dict(before)
    after['member'] += (1 if state == 'enabled' else 0) - (1 if member.state == 'enabled' else 0)
    s.capacity(org.access_plan, before, after)
    member.state, member.manage_needs, member.manage_campaigns = state, manage_needs, manage_campaigns
    s.touch(member, 'state', 'manage_needs', 'manage_campaigns')
    s.touch(org)
    if state != 'enabled':
        Token.objects.filter(membership=member, consumed_at__isnull=True, revoked_at__isnull=True).update(revoked_at=timezone.now())
    s.audit(actor, org, 'membership.permissions_updated', member, after={'state': state, 'manage_needs': manage_needs, 'manage_campaigns': manage_campaigns}, why='Authorized Member access and independent management permissions updated.')
    return member
