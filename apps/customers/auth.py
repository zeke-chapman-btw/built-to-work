"""Customer credentials use canonical customer email, never another realm's email."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from .models import CustomerIdentity
from .portal_policy import identity_for


class CustomerAuthenticationBackend:
    def authenticate(self, request, customer_email=None, password=None, username=None, **kwargs):
        if customer_email is None:
            # Block fallback to ModelBackend for known customer usernames.
            if username and CustomerIdentity.objects.filter(user__username=username).exists():
                get_user_model()().set_password(password or '')
                raise PermissionDenied
            return None
        if password is None:
            return None
        identity = CustomerIdentity.objects.select_related('user').filter(email=str(customer_email).strip().casefold()).first()
        if not identity:
            get_user_model()().set_password(password)
            return None
        if not identity.user.check_password(password):
            return None
        try:
            return identity_for(identity.user).user
        except PermissionDenied:
            return None

    def get_user(self, user_id):
        user = get_user_model().objects.filter(pk=user_id).first()
        if user:
            try:
                return identity_for(user).user
            except PermissionDenied:
                pass
        return None
