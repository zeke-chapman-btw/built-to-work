"""Fresh account and permission checks; never infer staff authority from a portal identity."""
from contextlib import contextmanager
from functools import wraps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from apps.accounts.roles import lock_role_groups, manager, has_role
from apps.participants.workspace_access import require_people
from apps.participants.identity import fresh_identity_context, canonical_id
from apps.participants.models import Participant
from .models import RecruitingLock


def fresh_staff(actor, *, administrator=False):
    if not getattr(actor, 'is_authenticated', False):
        raise PermissionDenied
    user = get_user_model().objects.filter(pk=actor.pk, is_active=True, is_staff=True).first()
    if not user or user.get_session_auth_hash() != actor.get_session_auth_hash():
        raise PermissionDenied('Your staff access changed. Sign in again.')
    if user.__class__.objects.filter(pk=user.pk, customer_identity__isnull=False).exists() or hasattr(user, 'participant_account'):
        raise PermissionDenied
    require_people(user)
    if administrator:
        if not user.is_superuser:
            raise PermissionDenied('Administrator access is required.')
    elif not (manager(user) or has_role(user, 'recruiter') or user.has_perm('recruiting.manage_recruiting')):
        raise PermissionDenied('Recruiting access is required.')
    return user


def fresh_participant(actor, *, lock=False, allow_identity_review=False):
    from apps.participants.portal_accounts import active_account
    from apps.participants.models import ParticipantAccount
    if not getattr(actor, 'is_authenticated', False):
        raise PermissionDenied
    users = get_user_model().objects.select_for_update() if lock else get_user_model().objects
    user = users.filter(pk=actor.pk, is_active=True, is_staff=False, is_superuser=False).first()
    if not user or user.get_session_auth_hash() != actor.get_session_auth_hash() or hasattr(user, 'customer_identity'):
        raise PermissionDenied('Use your current Participant sign-in.')
    account = (ParticipantAccount.objects.select_related('participant','user').filter(user=user,status='active',email_verified_at__isnull=False,participant__kind='person',participant__archived_at__isnull=True).first()
        if allow_identity_review else active_account(user))
    if not account:
        raise PermissionDenied('An active Participant identity is required.')
    if lock:
        account = ParticipantAccount.objects.select_for_update().select_related('participant').get(pk=account.pk)
        if account.status != 'active' or not account.email_verified_at:
            raise PermissionDenied('This Participant account is unavailable.')
    return user, account


@contextmanager
def policy_transaction():
    """Authorization-group locks precede identity lock, then sorted people/records.

    This intentionally serializes policy-changing recruiting writes. It makes
    alias consolidation, selection and disclosure linearizable on PostgreSQL.
    Existing role/membership administration uses the same role-group lock first.
    """
    with transaction.atomic():
        lock_role_groups()
        RecruitingLock.objects.get_or_create(pk=1)
        RecruitingLock.objects.select_for_update().get(pk=1)
        with fresh_identity_context():
            yield


def person(person_id, *, lock=False):
    root = canonical_id(int(person_id))
    qs = Participant.objects.select_for_update() if lock else Participant.objects
    item = qs.get(pk=root, kind='person', archived_at__isnull=True)
    return item


def reason(value):
    value = (value or '').strip()
    if not value or len(value) > 500:
        raise ValidationError('Enter an attributable reason up to 500 characters.')
    return value


def serialize_identity_write(function):
    """Use the same lock before existing reviewed-link services acquire people."""
    @wraps(function)
    def guarded(*args, **kwargs):
        with policy_transaction():
            from .identity_hooks import before_reviewed_write, after_reviewed_write
            before=before_reviewed_write(function.__name__,kwargs)
            result=function(*args, **kwargs)
            after_reviewed_write(before,kwargs.get('actor'))
            return result
    return guarded
