import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import totp
from .models import RecoveryCode, SecuritySettings, SentCode, TwoStepDevice

CODE_MINUTES = 10

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


CUSTOMER_BACKEND = "apps.customers.auth.CustomerAuthenticationBackend"


def is_customer(user):
    return not user.is_staff and hasattr(user, "customer_identity")


def covered(request):
    """Staff, and customers signed in to the customer portal. Never participants."""
    user = getattr(request, "user", None)
    if not (user is not None and user.is_authenticated):
        return False
    if user.is_staff:
        return True
    return request.session.get("_auth_user_backend") == CUSTOMER_BACKEND


def required_for_person(user):
    from .models import PersonRequirement
    return bool(getattr(user, "pk", None)) and PersonRequirement.objects.filter(user=user).exists()


def required_by_group(user):
    row = SecuritySettings.current()
    if is_customer(user):
        return row.require_two_step_customers
    from apps.accounts.roles import manager
    return row.require_two_step_staff or (row.require_two_step_managers and manager(user))


def required(user):
    if required_for_person(user):
        return True
    row = SecuritySettings.current()
    if is_customer(user):
        return row.require_two_step_customers
    from apps.accounts.roles import manager
    return row.require_two_step_staff or (row.require_two_step_managers and manager(user))


def home_url(user):
    from django.urls import reverse
    return reverse("customer_portal:workspace", args=["account"]) if is_customer(user) else reverse("internal:settings")


def state_for(user):
    if device(user):
        return "pending"
    if required(user):
        return "setup"
    return "ok"


def start(user, method="app"):
    secret = totp.new_secret() if method == "app" else ""
    TwoStepDevice.objects.update_or_create(user=user, defaults={
        "method": method, "secret_encrypted": _crypto().encrypt(secret, PURPOSE) if secret else "",
        "confirmed_at": None, "last_step": 0})
    return secret


def phone_for(user):
    for attr in ("staff_profile", "customer_identity"):
        try:
            phone = getattr(user, attr).phone
        except Exception:
            phone = ""
        if phone:
            return phone
    return ""


def email_for(user):
    if user.email:
        return user.email
    try:
        return user.customer_identity.email or ""
    except Exception:
        return ""


def masked(method, user):
    if method == "text":
        digits = "".join(ch for ch in phone_for(user) if ch.isdigit())
        return f"your phone ending in {digits[-4:]}" if digits else ""
    if method == "email":
        email = email_for(user)
        name, _, domain = email.partition("@")
        return f"{name[:2]}…@{domain}" if domain else ""
    return ""


def can_use(method, user):
    if method == "text":
        from apps.messaging import texting
        return bool(texting.phone_key(phone_for(user))) and texting.ready()
    if method == "email":
        return bool(email_for(user)) and email_ready()
    return True


def email_ready():
    """False on a copy of the app whose noreply@ sign-in hasn't been saved yet (mail would go nowhere)."""
    from apps.messaging import store
    if getattr(settings, "EMAIL_BACKEND", "") == "apps.messaging.backends.ConnectionsEmailBackend":
        return bool(store.email_settings().get("source"))
    return True


def send_code(user, method):
    """Text or email a 6-digit code. Returns an error message, or "" when sent."""
    recent = SentCode.objects.filter(user=user, created_at__gte=timezone.now() - timedelta(minutes=15)).count()
    if recent >= 5:
        return "Too many codes sent. Wait a few minutes and try again."
    code = f"{secrets.randbelow(1_000_000):06d}"
    body = f"Your Built to Work sign-in code is {code}. It expires in {CODE_MINUTES} minutes. Don't share it with anyone."
    try:
        if method == "text":
            from apps.events import sms_providers
            from apps.messaging import texting
            key = texting.phone_key(phone_for(user))
            if not key or not texting.ready():
                return "Texting isn't available. Use a recovery code or ask an administrator."
            sms_providers.send_sms(key, body, idempotency_key=f"two-step-{user.pk}-{secrets.token_hex(6)}")
        elif method == "email":
            from django.core.mail import send_mail
            from apps.messaging import store
            if not email_for(user):
                return "There's no email on your account. Use a recovery code or ask an administrator."
            if not email_ready():
                return "Email isn't set up on this app yet (Settings → Connections). Use a recovery code or ask an administrator."
            send_mail("Your Built to Work sign-in code", body, store.email_settings().get("from_address") or None,
                      [email_for(user)], fail_silently=False, connection=store.smtp_connection())
        else:
            return ""
    except Exception:
        return "The code couldn't be sent. Try again, or use a recovery code."
    SentCode.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
    SentCode.objects.create(user=user, code_hash=_hash(code), channel=method,
                            expires_at=timezone.now() + timedelta(minutes=CODE_MINUTES))
    return ""


def _sent_code_ok(user, code):
    row = SentCode.objects.select_for_update().filter(user=user, used_at__isnull=True,
                                                      expires_at__gt=timezone.now()).first()
    if not row:
        return False
    row.attempts += 1
    if row.attempts > 5:
        row.used_at = timezone.now()
        row.save(update_fields=["attempts", "used_at"])
        return False
    if row.code_hash == _hash(code):
        row.used_at = timezone.now()
        row.save(update_fields=["attempts", "used_at"])
        return True
    row.save(update_fields=["attempts"])
    return False


def secret_of(row):
    return _crypto().decrypt(row.secret_encrypted, PURPOSE)


@transaction.atomic
def confirm(user, code):
    row = TwoStepDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=True).first()
    if not row:
        return None
    if row.method == "app":
        step = totp.verify(secret_of(row), code)
        if step is None:
            return None
    else:
        if not _sent_code_ok(user, code):
            return None
        step = 0
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
    if row.method != "app":
        if _sent_code_ok(user, code):
            return True
        step = None
    else:
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
