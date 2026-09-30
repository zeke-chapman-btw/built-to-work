from django.contrib.auth import get_user_model
from .models import ParticipantAccount
from .normalization import normalize_email

class ParticipantAuthenticationBackend:
    """Authenticates only active participant accounts, never staff by their email."""
    def authenticate(self, request, participant_email=None, password=None, **kwargs):
        if not participant_email or password is None: return None
        try: account = ParticipantAccount.objects.select_related("user").get(login_email=normalize_email(participant_email), status=ParticipantAccount.Status.ACTIVE, email_verified_at__isnull=False)
        except ParticipantAccount.DoesNotExist: return None
        user = account.user
        if user.check_password(password) and user.is_active: return user
        return None
    def get_user(self, user_id):
        User = get_user_model()
        try: user = User.objects.get(pk=user_id)
        except User.DoesNotExist: return None
        return user if ParticipantAccount.objects.filter(user=user, status=ParticipantAccount.Status.ACTIVE, email_verified_at__isnull=False).exists() else None
