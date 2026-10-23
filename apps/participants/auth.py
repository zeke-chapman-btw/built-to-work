from django.contrib.auth import get_user_model
from .models import ParticipantAccount
from .normalization import normalize_email

class ParticipantAuthenticationBackend:
    """Authenticates only active participant accounts, never staff by their email."""
    def authenticate(self, request, participant_email=None, password=None, **kwargs):
        if not participant_email or password is None: return None
        try: account = ParticipantAccount.objects.select_related("user").get(login_email=normalize_email(participant_email), status=ParticipantAccount.Status.ACTIVE, email_verified_at__isnull=False)
        except ParticipantAccount.DoesNotExist:
            get_user_model()().set_password(password)
            return None
        user = account.user
        from .portal_accounts import active_account
        if user.check_password(password) and active_account(user): return user
        return None
    def get_user(self, user_id):
        User = get_user_model()
        try: user = User.objects.get(pk=user_id)
        except User.DoesNotExist: return None
        from .portal_accounts import active_account
        return user if active_account(user) else None
