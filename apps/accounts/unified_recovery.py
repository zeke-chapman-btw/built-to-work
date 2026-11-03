"""Resolve a single existing recovery identity; never link or broadcast by email."""
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from .roles import lock_role_groups
from django.views.decorators.debug import sensitive_variables

NOTICE = ('If these details identify one eligible account, recovery instructions will be sent. '
          'If you do not receive them, check your details or contact BTW for help. '
          'If you share an email address, include your username if you have one, or contact BTW.')


class RecoveryRequestForm(forms.Form):
    email = forms.EmailField(label='Account email', widget=forms.EmailInput(attrs={
        'autocomplete':'email','autocapitalize':'none','spellcheck':'false','autofocus':True}))
    username = forms.CharField(label='Username (optional)', required=False, max_length=150,
        help_text='If you normally sign in with a username, include it to identify your account more precisely.',
        widget=forms.TextInput(attrs={'autocomplete':'username','autocapitalize':'none','spellcheck':'false'}))


def guard_preview(request):
    from apps.customers.portal_preview import SESSION_KEY as customer
    from apps.participants.portal_preview import SESSION_KEY as participant
    if request.session.get(customer) or request.session.get(participant):
        raise PermissionDenied('Return to Admin before requesting account communications.')


@sensitive_variables()
def candidates(email, username=''):
    from apps.customers.models import CustomerIdentity
    from apps.customers.portal_policy import identity_for
    from apps.participants.models import ParticipantAccount
    from apps.participants.portal_accounts import active_account
    User=get_user_model()
    staff=User.objects.filter(email__iexact=email,is_active=True,is_staff=True,
        participant_account__isnull=True,customer_identity__isnull=True)
    if username:
        # Existing username+email proof is preserved. No fallback to a different identity.
        staff=staff.filter(username=username)
    result=[{'realm':'staff','user':user.pk,'email':user.email,'username':user.username}
            for user in staff if user.has_usable_password()]
    if username:
        return result
    for identity in CustomerIdentity.objects.select_related('user').filter(email__iexact=email):
        try:
            identity_for(identity.user)
        except PermissionDenied:
            continue
        result.append({'realm':'customer','user':identity.user_id,'email':identity.email})
    for account in ParticipantAccount.objects.select_related('user').filter(
            login_email__iexact=email,status='active',email_verified_at__isnull=False):
        if (account.user.has_usable_password() and active_account(account.user)
                and not CustomerIdentity.objects.filter(user=account.user).exists()):
            result.append({'realm':'participant','user':account.user_id,'email':account.login_email})
    return result


@transaction.atomic
@sensitive_variables()
def dispatch(request, email, username=''):
    guard_preview(request)
    lock_role_groups()
    matches=candidates(email,username)
    if len(matches)!=1:
        return
    selected=matches[0]
    get_user_model().objects.select_for_update().get(pk=selected['user'])
    # Recheck current eligibility and collisions before invoking the existing handler.
    fresh=candidates(email,username)
    if len(fresh)!=1 or fresh[0]['realm']!=selected['realm'] or fresh[0]['user']!=selected['user']:
        return
    from .login_views import _staff_recovery
    from apps.customers.portal_auth import _customer_recovery
    from apps.participants.portal_auth import _participant_recovery
    handlers={'staff':_staff_recovery,'customer':_customer_recovery,'participant':_participant_recovery}
    original=request.POST
    data=original.copy();data['email']=selected['email']
    if selected['realm']=='staff':
        data['username']=selected['username']
    request.POST=data
    try:
        # Existing token, delivery, audit and realm-specific throttling remain authoritative.
        handlers[selected['realm']](request)
    except (PermissionDenied,ValidationError):
        # A concurrent eligibility change must not expose which identity was considered.
        pass
    finally:
        request.POST=original
