"""Recovery links are expiring, bound to current account state and consumed on use."""
import hashlib
from django import forms
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST
from .auth import ParticipantAuthenticationBackend
from .forms import ParticipantLoginForm, PasswordResetRequestForm, SetPasswordForm, EmailChangeForm
from .models import ParticipantAccount, ParticipantEmailChange
from .portal_accounts import active_account, throttle, _email_lock, eligible
from .portal_views import participant_view
from .services import audit
from .tokens import read_token, SET_PASSWORD_SALT


def _message(request, title, text, status=200):
    response = render(request, 'participants/portal/message.html', {'title': title, 'message': text}, status=status)
    response['Referrer-Policy'] = 'same-origin'
    response['Cache-Control'] = 'private, no-store'
    return response


def _mail(subject, email, url):
    try:
        send_mail(subject, 'Use this private link within 24 hours:\n' + url, None, [email], fail_silently=False)
    except Exception:
        # No account enumeration or secrets/provider strings in error pages.
        return False
    return True


@never_cache
@require_http_methods(['GET', 'POST'])
def sign_in(request):
    if active_account(request.user):
        return redirect('participants:profile')
    form = ParticipantLoginForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email'].lower()
        permitted = throttle('login-ip', request.META.get('REMOTE_ADDR', ''), limit=40) and throttle('login-email', email, limit=12)
        user = ParticipantAuthenticationBackend().authenticate(request, participant_email=email,
            password=form.cleaned_data['password']) if permitted else None
        if user:
            login(request, user, backend='apps.participants.auth.ParticipantAuthenticationBackend')
            return redirect('participants:profile')
        form.add_error(None, 'Check your email and password, or try again later. Need help? Use account recovery below.')
    return render(request, 'participants/portal/form.html', {'title': 'Welcome back', 'intro': 'Your skills. Your next opportunity.',
        'form': form, 'login_form': True, 'submit_label': 'Sign in'})


@never_cache
@require_POST
def sign_out(request):
    logout(request)
    return redirect('participants:login')


@never_cache
@require_http_methods(['GET', 'POST'])
def reset_request(request):
    form = PasswordResetRequestForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email'].lower()
        allowed = throttle('reset-email', email, limit=3) and throttle('reset-ip', request.META.get('REMOTE_ADDR', ''), limit=15)
        account = ParticipantAccount.objects.select_related('user').filter(login_email__iexact=email, status='active').first() if allowed else None
        if account and active_account(account.user):
            token = signing.dumps({'user': str(account.user_id), 'proof': default_token_generator.make_token(account.user)}, salt='portal.reset.v1')
            url = request.build_absolute_uri(reverse('participants:password-reset-confirm', kwargs={'token': token}))
            _mail('Reset your Built to Work password', email, url)
            audit(None, 'portal.password_reset_requested', account)
        return _message(request, 'Request received', 'If an active participant account matches, a recovery email was requested. Check your inbox or contact BTW for help. Preview test email is not delivered to external inboxes.')
    return render(request, 'participants/portal/form.html', {'title': 'Reset your password', 'form': form, 'submit_label': 'Send reset link'})


def _reset_account(token, lock=False):
    try:
        value = signing.loads(token, salt='portal.reset.v1', max_age=86400)
        qs = ParticipantAccount.objects.select_for_update() if lock else ParticipantAccount.objects
        account = qs.select_related('user').get(user_id=value['user'], status='active')
        if active_account(account.user) and default_token_generator.check_token(account.user, value['proof']):
            return account
    except (signing.BadSignature, KeyError, ValueError, TypeError, ParticipantAccount.DoesNotExist):
        pass
    return None


@never_cache
@require_http_methods(['GET', 'POST'])
def reset_confirm(request, token):
    if not _reset_account(token):
        return _message(request, 'Let’s get a fresh link', 'This recovery link is invalid, expired or already used. Request a new password reset.', 400)
    form = SetPasswordForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            account = _reset_account(token, lock=True)
            if not account:
                return _message(request, 'Link already used', 'Request another reset link.', 400)
            account.user.set_password(form.cleaned_data['password1'])
            account.user.save(update_fields=['password'])
            audit(account.user, 'participant_password_reset', account)
        messages.success(request, 'Password updated. Sign in with your new password.')
        return redirect('participants:login')
    return render(request, 'participants/portal/form.html', {'title': 'Choose a new password', 'form': form, 'submit_label': 'Save password'})


@participant_view
@require_http_methods(['GET', 'POST'])
def email_change(request):
    class ConfirmEmailForm(EmailChangeForm):
        password = forms.CharField(widget=forms.PasswordInput, label='Current password')
    form = ConfirmEmailForm(request.POST if request.method == 'POST' else None)
    account = request.portal_account
    if request.method == 'POST' and form.is_valid():
        if not throttle('email-change', account.pk, limit=5) or not account.user.check_password(form.cleaned_data['password']):
            form.add_error('password', 'Check your password, or try again later.')
        else:
            email = form.cleaned_data['new_email']
            with transaction.atomic():
                _email_lock(email)
                if ParticipantAccount.objects.filter(login_email__iexact=email).exclude(pk=account.pk).exclude(status='disabled').exists():
                    form.add_error('new_email', 'This address is unavailable. Contact BTW if you share an address.')
                else:
                    change, _ = ParticipantEmailChange.objects.update_or_create(account=account, defaults={'new_email': email, 'verified_at': None})
                    payload = {'id': change.pk, 'stamp': change.updated_at.isoformat(), 'proof': default_token_generator.make_token(account.user)}
                    token = signing.dumps(payload, salt='portal.email-change.v2')
                    url = request.build_absolute_uri(reverse('participants:verify-email-change', kwargs={'token': token}))
                    transaction.on_commit(lambda: _mail('Verify your Built to Work sign-in email', email, url))
                    return _message(request, 'Check your new email', 'Your sign-in address changes only after you open and confirm the verification link. Preview test email is not delivered externally.')
    return render(request, 'participants/portal/form.html', {'title': 'Change sign-in email', 'form': form, 'section': 'account', 'submit_label': 'Send verification'})


@participant_view
@require_http_methods(['GET', 'POST'])
def email_confirm(request, token):
    try:
        payload = signing.loads(token, salt='portal.email-change.v2', max_age=86400)
    except (signing.BadSignature, TypeError):
        return _message(request, 'Link expired', 'Request a new email verification link.', 400)
    with transaction.atomic():
        change = get_object_or_404(ParticipantEmailChange.objects.select_for_update(), pk=payload.get('id'), account=request.portal_account, verified_at__isnull=True)
        account = ParticipantAccount.objects.select_for_update().select_related('user').get(pk=change.account_id)
        if payload.get('stamp') != change.updated_at.isoformat() or not default_token_generator.check_token(account.user, payload.get('proof', '')):
            return _message(request, 'Link expired', 'Request a new email verification link.', 400)
        if request.method == 'POST':
            _email_lock(change.new_email)
            if ParticipantAccount.objects.filter(login_email__iexact=change.new_email).exclude(pk=account.pk).exclude(status='disabled').exists():
                return _message(request, 'Address unavailable', 'Request another email address or contact BTW.', 409)
            account.login_email, account.user.email = change.new_email, change.new_email
            account.user.save(update_fields=['email'])
            account.save(update_fields=['login_email', 'updated_at'])
            change.verified_at = timezone.now()
            change.save(update_fields=['verified_at', 'updated_at'])
            audit(request.user, 'participant_login_email_changed', account)
            return _message(request, 'Sign-in email updated', 'Your new address is verified. Your profile contact details remain separately editable.')
    return render(request, 'participants/portal/form.html', {'title': 'Confirm your sign-in email change', 'form': forms.Form(), 'submit_label': 'Confirm email change'})


@never_cache
@require_http_methods(['GET', 'POST'])
def legacy_setup(request, token):
    """Retain issued legacy links only when they pass the same intake eligibility boundary."""
    key = read_token(token, SET_PASSWORD_SALT)
    account = ParticipantAccount.objects.select_related('participant', 'user').filter(pk=key, status='pending').first() if key else None
    if not account or not account.email_verified_at or account.login_email != account.participant.contact_email or not eligible(account.participant, invitation=True):
        return _message(request, 'Invitation needs review', 'Use invitation recovery or contact BTW staff.', 400)
    form = SetPasswordForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            account = ParticipantAccount.objects.select_for_update().select_related('user').get(pk=account.pk)
            _email_lock(account.login_email)
            if account.status != 'pending' or ParticipantAccount.objects.filter(status='active', login_email=account.login_email).exclude(pk=account.pk).exists():
                return _message(request, 'Account already exists', 'Sign in or request recovery.', 400)
            account.user.set_password(form.cleaned_data['password1'])
            account.user.is_active = True
            account.user.save(update_fields=['password', 'is_active'])
            account.status, account.password_set_at = 'active', timezone.now()
            account.save(update_fields=['status', 'password_set_at', 'updated_at'])
            audit(account.user, 'participant_account_activated', account)
        return redirect('participants:login')
    return render(request, 'participants/portal/form.html', {'title': 'Activate your portal', 'form': form})
