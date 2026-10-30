"""One customer identity realm; fresh, explicit organization authority on every use."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from .models import CustomerIdentity, CustomerMembership, CustomerOrganization


def is_customer(user):
    return bool(getattr(user, 'pk', None) and CustomerIdentity.objects.filter(user_id=user.pk).exists())


def identity_for(user, *, lock=False):
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None):
        raise PermissionDenied('Customer sign-in is required.')
    users = get_user_model().objects
    identities = CustomerIdentity.objects
    if lock:
        from apps.accounts.roles import lock_role_groups
        lock_role_groups()
        users, identities = users.select_for_update(), identities.select_for_update()
    fresh = users.filter(pk=user.pk, is_active=True, is_staff=False, is_superuser=False).first()
    identity = identities.filter(user_id=user.pk, activated_at__isnull=False).first()
    if not fresh or not identity or fresh.email or not fresh.has_usable_password():
        raise PermissionDenied('Customer sign-in is required.')
    # Includes session invalidation when services receive an old authenticated object.
    if fresh.get_session_auth_hash() != user.get_session_auth_hash():
        raise PermissionDenied('Please sign in again.')
    from apps.participants.models import ParticipantAccount
    from apps.staffing.models import StaffProfile
    if fresh.groups.exists() or fresh.user_permissions.exists() or ParticipantAccount.objects.filter(user=fresh).exists() or StaffProfile.objects.filter(user=fresh).exists():
        raise PermissionDenied('This identity requires BTW review.')
    identity.user = fresh
    return identity


def eligible_memberships(user):
    identity = identity_for(user)
    return CustomerMembership.objects.filter(identity=identity, state='enabled', accepted_at__isnull=False,
        organization__status='active', organization__provisioned=True).select_related('organization', 'identity__user').order_by('organization__name', 'pk')


def require_membership(user, org_id, *, action=None, lock=False):
    identity = identity_for(user, lock=lock)
    orgs, members = CustomerOrganization.objects, CustomerMembership.objects
    if lock:
        orgs, members = orgs.select_for_update(), members.select_for_update()
    org = orgs.filter(pk=org_id, status='active', provisioned=True).first()
    member = members.filter(organization_id=org_id, identity=identity, state='enabled', accepted_at__isnull=False).first()
    if not org or not member:
        raise PermissionDenied('This company is not available to your account.')
    member.organization, member.identity = org, identity
    if action:
        if action == 'members':
            allowed = member.role == 'lead'
        elif action in {'needs', 'campaigns'}:
            allowed = member.role == 'lead' or getattr(member, 'manage_' + action)
            # Need collaboration is an enabled service, including changes to existing Needs.
            if action == 'needs':
                allowed = allowed and org.access_plan.workforce_need_creation is True
        else:
            allowed = False
        if not allowed:
            raise PermissionDenied('Your membership does not allow this action.')
    return member
