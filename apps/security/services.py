import hashlib
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import totp
from .models import RecoveryCode, SecuritySettings, TwoStepDevice

PURPOSE = "two-step-secret"
SESSION_KEY = "two_step"


def _crypto():
    from apps.messaging import crypto
    return crypto


def _hash(code):
    clean = "".join(ch for ch in str(code).lower() if ch.isalnum())
    return hashlib.sha256((settings.SECRET_KEY + ":" + clean).encode()).hexdigest()


def device(user):
    return TwoStepDevice.objects.filter(user=user, confirmed_at__isnull=False).first()


def required(user):
    from apps.accounts.roles import manager
    return SecuritySettings.current().require_two_step_managers and manager(user)


def state_for(user):
    if device(user):
        return "pending"
    if required(user):
        return "setup"
    return "ok"


def start(user):
    secret = totp.new_secret()
    TwoStepDevice.objects.update_or_create(user=user, defaults={
        "secret_encrypted": _crypto().encrypt(secret, PURPOSE), "confirmed_at": None, "last_step": 0})
    return secret


def secret_of(row):
    return _crypto().decrypt(row.secret_encrypted, PURPOSE)


@transaction.atomic
def confirm(user, code):
    row = TwoStepDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=True).first()
    if not row:
        return None
    step = totp.verify(secret_of(row), code)
    if step is None:
        return None
    row.confirmed_at, row.last_step = timezone.now(), step
    row.save()
    return new_recovery_codes(user)


def new_recovery_codes(user):
    RecoveryCode.objects.filter(user=user).delete()
    codes = []
    for _ in range(10):
        raw = secrets.token_hex(4)
        code = f"{raw[:4]}-{raw[4:]}"
        codes.append(code)
        RecoveryCode.objects.create(user=user, code_hash=_hash(code))
    return codes


@transaction.atomic
def check(user, code):
    """True for a valid authenticator code or an unused recovery code."""
    row = TwoStepDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
    if not row:
        return False
    step = totp.verify(secret_of(row), code, after_step=row.last_step)
    if step is not None:
        row.last_step = step
        row.save(update_fields=["last_step"])
        return True
    match = RecoveryCode.objects.select_for_update().filter(user=user, code_hash=_hash(code), used_at__isnull=True).first()
    if match:
        match.used_at = timezone.now()
        match.save(update_fields=["used_at"])
        return True
    return False


def turn_off(user):
    TwoStepDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()


def mark(request, state):
    request.session[SESSION_KEY] = f"{state}:{request.user.pk}"
