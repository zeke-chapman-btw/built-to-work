"""Intake-bound invitations. Email proves inbox control, never an ambiguous identity match."""
import hashlib
import uuid
from datetime import timedelta
from urllib.parse import urlsplit
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from apps.core.audit import record_audit
from apps.events.models import ParticipantMatchReview
from .models import (Participant, ParticipantAccount, RegistrationSubmission, ConsentAcceptance,
                     IntakeContactLock, PortalInvitation, PortalDelivery, PortalAccessAttempt)
from .identity import canonical_id, identity_members
from .intake_contracts import age_on, canonical_answers
from .normalization import normalize_email

INVITE_SALT = 'btw.participant.portal.invite.v1'


def _audit(action, instance, actor=None, **data):
    record_audit(actor=actor, action='portal.' + action, instance=instance,
                 source='participant_portal', new_data=data)


def intake_for(person):
    return RegistrationSubmission.objects.filter(
        participant_id__in=identity_members(person.pk), status='submitted',
        intake_operation_id__isnull=False).select_related('form_version').order_by('-submitted_at', '-pk').first()


def identity_clear(person):
    from .models import PortalPreviewFixture
    if PortalPreviewFixture.objects.filter(participant=person).exists():
        return False
    if person.kind != 'person' or person.archived_at or canonical_id(person.pk) != person.pk:
        return False
    members = identity_members(person.pk)
    member_keys = {str(pk) for pk in members}
    for source, candidate, candidates in ParticipantMatchReview.objects.filter(
            status__in=['open', 'later']).values_list('source_participant_id', 'candidate_participant_id', 'candidates'):
        if source in members or candidate in members or member_keys.intersection(str(pk) for pk in (candidates or [])):
            return False
    return True


def eligible(person, *, invitation=False):
    if not identity_clear(person):
        return False
    submission = intake_for(person)
    if not submission or not person.date_of_birth or age_on(person.date_of_birth, timezone.localdate()) < 14:
        return False
    if not ConsentAcceptance.objects.filter(submission=submission).exists():
        return False
    if invitation:
        # Use the latest immutable affirmative preference, never ticket-delivery consent.
        answers = canonical_answers(submission.form_version.snapshot, submission.answers)
        if answers.get('updates_preference') != 'yes':
            return False
        email = normalize_email(person.contact_email)
        if not email or email != normalize_email(answers.get('email', '')):
            return False
        if Participant.objects.filter(contact_email__iexact=email, kind='person', archived_at__isnull=True).exclude(pk__in=identity_members(person.pk)).exists():
            return False
    return True


def _email_lock(email):
    key = hashlib.sha256(('portal-email:' + normalize_email(email)).encode()).hexdigest()
    IntakeContactLock.objects.get_or_create(key=key)
    IntakeContactLock.objects.select_for_update().get(key=key)


def active_account(user):
    if not getattr(user, 'is_authenticated', False) or not user.is_active or user.is_staff or user.is_superuser:
        return None
    account = ParticipantAccount.objects.select_related('participant', 'user').filter(
        user=user, status='active', email_verified_at__isnull=False).first()
    return account if account and identity_clear(account.participant) else None


@transaction.atomic
def throttle(scope, value, *, limit=6, minutes=15):
    key = hashlib.sha256((scope + ':' + str(value)).encode()).hexdigest()
    now = timezone.now()
    row, _ = PortalAccessAttempt.objects.get_or_create(key=key, defaults={'started_at': now})
    row = PortalAccessAttempt.objects.select_for_update().get(pk=key)
    if row.started_at < now - timedelta(minutes=minutes):
        row.started_at, row.count = now, 0
    row.count += 1
    row.save(update_fields=['started_at', 'count'])
    return row.count <= limit


@transaction.atomic
def queue_invitation(person, *, actor=None, resend=False):
    person = Participant.objects.select_for_update().get(pk=person.pk)
    if not eligible(person, invitation=True):
        return None
    email = normalize_email(person.contact_email)
    _email_lock(email)
    existing = ParticipantAccount.objects.select_related('user').filter(participant=person).first()
    if existing and (existing.status != 'pending' or existing.login_email != email or existing.user.is_staff or existing.user.is_superuser):
        return None
    if ParticipantAccount.objects.filter(login_email__iexact=email).exclude(participant=person).exclude(status='disabled').exists():
        return None
    invitation, created = PortalInvitation.objects.get_or_create(
        participant=person, defaults={'email': email, 'submission': intake_for(person)})
    if invitation.activated_at:
        return None
    if invitation.email != email:
        invitation.email, invitation.nonce = email, uuid.uuid4()
        invitation.submission = intake_for(person)
    now = timezone.now()
    delivery_to_send = None
    if created:
        for slot, days in [('initial', 0), ('day3', 3), ('day7', 7)]:
            delivery, _ = PortalDelivery.objects.get_or_create(invitation=invitation, slot=slot,
                defaults={'due_at': now + timedelta(days=days)})
            if slot == 'initial':
                delivery_to_send = delivery.pk
        _audit('invitation_queued', invitation, actor)
    elif resend:
        if invitation.last_requested_at and invitation.last_requested_at > now - timedelta(minutes=15):
            return invitation
        invitation.nonce = uuid.uuid4()
        delivery_to_send = PortalDelivery.objects.create(invitation=invitation,
            slot='resend-' + uuid.uuid4().hex, due_at=now).pk
        _audit('invitation_resent', invitation, actor)
    invitation.last_requested_at = now
    invitation.save()
    if delivery_to_send is not None:
        transaction.on_commit(lambda: deliver_invitation(delivery_to_send), robust=True)
    return invitation


def invite_token(invitation):
    return signing.dumps({'id': str(invitation.pk), 'nonce': str(invitation.nonce),
                          'email': hashlib.sha256(invitation.email.encode()).hexdigest()}, salt=INVITE_SALT)


def read_invitation(token, *, lock=False):
    try:
        payload = signing.loads(token, salt=INVITE_SALT, max_age=24 * 3600)
        qs = PortalInvitation.objects.select_for_update() if lock else PortalInvitation.objects
        invitation = qs.select_related('participant').get(pk=payload['id'], activated_at__isnull=True)
        if (str(invitation.nonce) != payload['nonce'] or hashlib.sha256(invitation.email.encode()).hexdigest() != payload['email']
                or invitation.email != normalize_email(invitation.participant.contact_email)
                or not eligible(invitation.participant, invitation=True)):
            return None
        return invitation
    except (signing.BadSignature, ValueError, KeyError, TypeError, PortalInvitation.DoesNotExist):
        return None


@transaction.atomic
def activate(token, password):
    invitation = read_invitation(token)
    if not invitation:
        raise ValidationError('This link is invalid or expired. Request a new invitation.')
    person = Participant.objects.select_for_update().get(pk=invitation.participant_id)
    _email_lock(invitation.email)
    invitation = read_invitation(token, lock=True)
    if not invitation:
        raise ValidationError('This invitation is no longer available. Request a fresh link.')
    existing = ParticipantAccount.objects.select_related('user').filter(participant=person).first()
    if (existing and (existing.status != 'pending' or existing.login_email != invitation.email
            or existing.user.is_staff or existing.user.is_superuser)) or ParticipantAccount.objects.filter(
            login_email__iexact=invitation.email).exclude(participant=person).exclude(status='disabled').exists():
        raise ValidationError('An account already exists. Sign in or request account recovery.')
    user = existing.user if existing else get_user_model()(username='participant_' + uuid.uuid4().hex, email=invitation.email)
    from django.contrib.auth.password_validation import validate_password
    validate_password(password, user)
    user.set_password(password)
    user.is_active = True
    user.save()
    now = timezone.now()
    account = existing or ParticipantAccount(participant=person, user=user, login_email=invitation.email)
    account.status, account.email_verified_at, account.password_set_at = 'active', now, now
    account.save()
    invitation.activated_at, invitation.nonce = now, uuid.uuid4()
    invitation.save(update_fields=['activated_at', 'nonce', 'updated_at'])
    invitation.deliveries.filter(status__in=['pending', 'held']).update(status='cancelled', detail='Account activated')
    _audit('account_activated', account, user)
    return account


def portal_base_url():
    base = getattr(settings, 'PARTICIPANT_PORTAL_BASE_URL', '').rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/'):
        return ''
    return base


def deliver_invitation(delivery_id, *, base_url=None):
    """Short DB claim; no legacy worker connection closing. Uncertain sends never auto-retry."""
    with transaction.atomic():
        delivery = PortalDelivery.objects.select_for_update().select_related('invitation__participant').get(pk=delivery_id)
        if delivery.status not in ['pending', 'held'] or delivery.due_at > timezone.now():
            return delivery.status
        invitation = PortalInvitation.objects.select_for_update().get(pk=delivery.invitation_id)
        if invitation.activated_at or ParticipantAccount.objects.filter(participant=invitation.participant).exclude(status='pending').exists():
            delivery.status, delivery.detail = 'cancelled', 'Account exists; reminders stopped'
        elif not eligible(invitation.participant, invitation=True) or invitation.email != normalize_email(invitation.participant.contact_email):
            delivery.status, delivery.detail = 'held', 'Identity or communication permission requires review'
        elif not (base_url or portal_base_url()):
            delivery.status, delivery.detail = 'held', 'Configure PARTICIPANT_PORTAL_BASE_URL as the HTTPS portal origin'
        else:
            delivery.status, delivery.detail = 'sending', ''
            delivery.attempts += 1
            delivery.attempted_at = timezone.now()
        delivery.save()
        if delivery.status != 'sending':
            return delivery.status
        token = invite_token(invitation)
    base = base_url or portal_base_url()
    url = base + reverse('participants:portal-activate', kwargs={'token': token})
    try:
        # Same Django transport as existing messages; deliberately no unrelated campaign engine.
        message = EmailMessage('Your Built to Work participant portal',
            'Put your skills to work. Activate your existing BTW profile using this private link within 24 hours:\n'
            + url + '\nIf you did not request this, ignore it. Need help? Contact Built to Work.',
            getattr(settings, 'DEFAULT_FROM_EMAIL', None), [invitation.email],
            headers={'Message-ID': '<portal-' + str(delivery.pk) + '@btw.local>'})
        if message.send(fail_silently=False) != 1:
            raise ConnectionError('No provider acceptance')
    except Exception:
        # Never retain exception strings, credentials, activation tokens or recipient data in status/audits.
        PortalDelivery.objects.filter(pk=delivery.pk).update(detail='Delivery uncertain; staff must inspect transport before resending')
        return 'sending'
    backend = getattr(settings, 'EMAIL_BACKEND', '')
    status = 'sent' if backend == 'django.core.mail.backends.smtp.EmailBackend' else 'test'
    PortalDelivery.objects.filter(pk=delivery.pk).update(status=status, completed_at=timezone.now(),
        detail='' if status == 'sent' else 'Test backend accepted only; no live delivery')
    _audit('invitation_delivery_' + status, invitation)
    return status


def process_deliveries(*, limit=100, base_url=None):
    ids = list(PortalDelivery.objects.filter(status__in=['pending', 'held'], due_at__lte=timezone.now()).order_by('due_at', 'pk').values_list('pk', flat=True)[:limit])
    return [deliver_invitation(pk, base_url=base_url) for pk in ids]
