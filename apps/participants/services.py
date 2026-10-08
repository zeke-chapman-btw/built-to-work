from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from apps.core.audit import record_audit
from .models import Participant, ParticipantAccount, ParticipantAccountRequest
from .normalization import normalize_email, normalize_phone
from .tokens import VERIFY_SALT, SET_PASSWORD_SALT, make_token

User = get_user_model()

def audit(actor, action, instance, old_data=None, new_data=None, reason=""):
    return record_audit(actor=actor, action=action, instance=instance, source="participant_portal", old_data=old_data, new_data=new_data, reason=reason)

def find_participant_matches(email="", phone=""):
    from .identity import participant_candidates
    return participant_candidates(email=email, phone=phone)


def send_signed_link(subject, email, path, token, request=None):
    reverse_path = reverse(path, kwargs={'token': token})
    url = request.build_absolute_uri(reverse_path) if request else f"{getattr(settings, 'PARTICIPANT_PORTAL_BASE_URL', '').rstrip('/')}{reverse_path}"
    send_mail(subject, f"Use this secure link within 24 hours: {url}", settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)

@transaction.atomic
def begin_account_request(request_obj, request=None):
    request_obj.login_email = normalize_email(request_obj.login_email)
    request_obj.contact_email = normalize_email(request_obj.contact_email)
    request_obj.contact_phone = normalize_phone(request_obj.contact_phone)
    matches = find_participant_matches(request_obj.contact_email, request_obj.contact_phone)
    request_obj.match_count = matches.count()
    request_obj.matched_participant = matches.first() if request_obj.match_count == 1 else None
    request_obj.save()
    token = make_token(request_obj.pk, VERIFY_SALT)
    send_signed_link("Verify your Built to Work participant account request", request_obj.login_email, "participants:verify-request", token, request)
    audit(None, "participant_account_requested", request_obj, new_data={"status": request_obj.status, "match_count": request_obj.match_count})
    return request_obj

@transaction.atomic
def create_pending_account(participant, email, verified_at, actor=None, request=None):
    email = normalize_email(email)
    if participant.kind != Participant.Kind.PERSON:
        raise ValueError("System test identities cannot have participant accounts.")
    if ParticipantAccount.objects.filter(status=ParticipantAccount.Status.ACTIVE, login_email__iexact=email).exists():
        raise ValueError("An active participant account already uses that email.")
    if ParticipantAccount.objects.filter(participant=participant).exists():
        raise ValueError("This participant already has an account.")
    user = User.objects.create_user(username=f"participant_{__import__('uuid').uuid4().hex}", email=email, password=None, is_active=False)
    account = ParticipantAccount.objects.create(participant=participant, user=user, login_email=email, email_verified_at=verified_at)
    token = make_token(account.pk, SET_PASSWORD_SALT)
    send_signed_link("Set your Built to Work participant account password", email, "participants:set-password", token, request=request)
    audit(actor, "participant_account_created", account, new_data={"participant_id": str(participant.pk), "status": account.status, "login_email": email})
    return account
