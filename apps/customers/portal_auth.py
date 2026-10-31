"""Shared-login entry and customer-only activation/recovery forms."""
from django import forms
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import SetPasswordForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST
from apps.accounts.staff_services import rate_limit
from . import invitations as invites
from .portal_policy import identity_for


class LoginForm(forms.Form):
    email = forms.EmailField(label='Customer email', widget=forms.EmailInput(attrs={'autocomplete': 'username'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))


class RecoveryForm(forms.Form):
    email = forms.EmailField(label='Customer email')


def safe_next(request, value):
    return value if value and value.startswith('/customer/') and not value.startswith('/customer/auth/') and url_has_allowed_host_and_scheme(value, {request.get_host()}, require_https=request.is_secure()) else None


def page(request, title, form=None, *, notice='', status=200, submit='Continue'):
    response = render(request, 'customers/portal/access.html', {'page_title': title, 'form': form, 'notice': notice, 'submit_label': submit}, status=status)
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


@never_cache
@sensitive_post_parameters('password')
@require_http_methods(['GET', 'POST'])
def sign_in(request):
    from .portal_preview import SESSION_KEY, selected_fixture
    if request.session.get(SESSION_KEY):
        selected_fixture(request)
        return redirect('customer_portal:home')
    if request.session.get('_auth_user_backend') == 'apps.customers.auth.CustomerAuthenticationBackend':
        try:
            identity_for(request.user)
            return redirect(safe_next(request, request.GET.get('next')) or 'customer_portal:home')
        except PermissionDenied:
            pass
    form = LoginForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        ip_ok = rate_limit('customer-login-ip', request.META.get('REMOTE_ADDR', ''), 30)
        user_ok = rate_limit('customer-login-email', form.cleaned_data['email'].casefold(), 10)
        user = authenticate(request, customer_email=form.cleaned_data['email'], password=form.cleaned_data['password']) if ip_ok and user_ok else None
        if user:
            login(request, user, backend='apps.customers.auth.CustomerAuthenticationBackend')
            request.session.pop('customer_organization', None)
            from apps.core.audit import record_audit
            record_audit(actor=user, action='customer.signed_in', instance=identity_for(user), source='customer_authentication', reason='Customer signed in through the shared BTW entry.')
            return redirect(safe_next(request, request.POST.get('next')) or 'customer_portal:home')
        form.add_error(None, 'Unable to sign in. Check your details or try again later.')
    response = render(request, 'accounts/unified_login.html', {'page_title': 'Welcome to Built to Work', 'form': form, 'account_type': 'customer', 'next': safe_next(request, request.GET.get('next') or request.POST.get('next')) or '', 'recovery_url': reverse('customer_portal:recovery')})
    response['Cache-Control'] = 'private, no-store'
    return response


@never_cache
@require_POST
def sign_out(request):
    from .portal_preview import SESSION_KEY, exit as exit_preview
    if request.session.get(SESSION_KEY):
        return exit_preview(request)
    if request.session.get('_auth_user_backend') == 'apps.customers.auth.CustomerAuthenticationBackend' and request.user.is_authenticated:
        from apps.core.audit import record_audit
        record_audit(actor=request.user, action='customer.signed_out', instance=identity_for(request.user), source='customer_authentication', reason='Customer signed out.')
    logout(request)
    return redirect(reverse('accounts:login') + '?account_type=customer')


@never_cache
@require_http_methods(['GET', 'POST'])
def recovery(request):
    from .portal_preview import SESSION_KEY
    if request.session.get(SESSION_KEY):
        raise PermissionDenied('Return to Admin before changing accounts or requesting account communications.')
    form = RecoveryForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        ip_ok = rate_limit('customer-reset-ip', request.META.get('REMOTE_ADDR', ''), 15)
        email_ok = rate_limit('customer-reset-email', form.cleaned_data['email'].casefold(), 3)
        if ip_ok and email_ok:
            invites.request_recovery(email=form.cleaned_data['email'], origin=request.build_absolute_uri('/').rstrip('/'))
        return page(request, 'Request received', notice='If an active customer account matches, recovery instructions have been requested. Contact BTW if you need help. Test email capture does not deliver to an external inbox.')
    return page(request, 'Recover customer access', form, submit='Send recovery instructions')


@never_cache
@sensitive_post_parameters('new_password1', 'new_password2')
@require_http_methods(['GET', 'POST'])
def accept(request, token):
    from .portal_preview import SESSION_KEY
    if request.session.get(SESSION_KEY):
        raise PermissionDenied('Return to Admin before changing accounts or requesting account communications.')
    try:
        item = invites.valid_token(token, 'invite')
    except ValidationError:
        return page(request, 'A fresh invitation is needed', notice='This link is invalid, expired, unavailable or already used. Contact BTW or your company Lead.', status=400)
    active = bool(item.identity.activated_at)
    if active:
        try:
            if request.session.get('_auth_user_backend') != 'apps.customers.auth.CustomerAuthenticationBackend' or identity_for(request.user).pk != item.identity_id:
                raise PermissionDenied
        except PermissionDenied:
            return page(request, 'Sign in to accept your invitation', notice='Use Customer sign in with the invited address, then reopen this invitation. Your password will stay unchanged.')
    form = forms.Form(request.POST if request.method == 'POST' else None) if active else SetPasswordForm(item.identity.user, request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            invites.accept_invitation(raw=token, actor=request.user if active else None, password=None if active else form.cleaned_data['new_password1'])
        except (ValidationError, PermissionDenied):
            return page(request, 'Invitation unavailable', notice='This invitation changed or can no longer be accepted. Request a fresh invitation.', status=400)
        messages.success(request, 'Company membership accepted. Sign in to continue.' if not active else 'Company membership accepted.')
        return redirect('customer_portal:home' if active else reverse('accounts:login') + '?account_type=customer')
    return page(request, 'Join ' + item.membership.organization.name, form,
        notice='Accept this company invitation.' if active else 'Verify your invited address by choosing your customer password. Your staff or participant account, if any, stays separate.', submit='Accept invitation' if active else 'Set password & accept')


@never_cache
@sensitive_post_parameters('new_password1', 'new_password2')
@require_http_methods(['GET', 'POST'])
def reset_confirm(request, token):
    from .portal_preview import SESSION_KEY
    if request.session.get(SESSION_KEY):
        raise PermissionDenied('Return to Admin before changing accounts or requesting account communications.')
    try:
        item = invites.valid_token(token, 'reset')
    except ValidationError:
        return page(request, 'A fresh recovery link is needed', notice='This link is invalid, expired or already used. Request customer recovery again.', status=400)
    form = SetPasswordForm(item.identity.user, request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            invites.reset_password(raw=token, password=form.cleaned_data['new_password1'])
        except ValidationError:
            return page(request, 'A fresh recovery link is needed', notice='This link changed or is no longer available.', status=400)
        logout(request)
        messages.success(request, 'Password updated. Sign in with your new password.')
        return redirect(reverse('accounts:login') + '?account_type=customer')
    return page(request, 'Choose your customer password', form, submit='Save password')
