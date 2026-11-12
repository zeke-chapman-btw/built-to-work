"""Staff username authentication. Participant portal identities stay separate."""
from apps.core.email_text import link_email
from django import forms
from django.contrib import messages
from django.contrib.auth import login,logout,get_user_model,update_session_auth_hash
from django.contrib.auth.forms import AuthenticationForm,PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import render,redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters,sensitive_variables
from django.views.decorators.http import require_http_methods,require_POST
from .roles import active_staff,has_role,manager,lock_role_groups
from .models import StaffInvitation
from .staff_services import rate_limit,issue_invitation,invitation_token,audit,account_state

class StaffLoginForm(AuthenticationForm):
    def confirm_login_allowed(self,user):
        super().confirm_login_allowed(user)
        from apps.participants.models import ParticipantAccount
        if not active_staff(user) or ParticipantAccount.objects.filter(user=user).exists():
            raise forms.ValidationError('Unable to sign in with these staff credentials.',code='invalid_login')
class RecoveryForm(forms.Form):
    username=forms.CharField(max_length=150,label='Staff username')
    email=forms.EmailField(label='Account email')
def home(user):
    if manager(user):return reverse('internal:home')
    if has_role(user,'recruiter'):return reverse('participants:staff-directory')
    if has_role(user,'event_staff'):return reverse('staffing:general-schedule')
    return reverse('internal:home')
def safe_next(request,value):
    if value and value.startswith('/') and not value.startswith('//') and url_has_allowed_host_and_scheme(value,{request.get_host()},require_https=request.is_secure()):
        if not value.startswith(('/accounts/login','/accounts/logout','/participants/')):return value
        if value.startswith('/participants/staff/'):return value
    return None
def page(request,title,form=None,**extra):
    response=render(request,'accounts/access_form.html',dict(page_title=title,form=form,**extra))
    response['Cache-Control']='private, no-store';response['Referrer-Policy']='same-origin';return response

@never_cache
@sensitive_variables()
@sensitive_post_parameters('password','username','email','identifier')
@require_http_methods(['GET','POST'])
def sign_in(request):
    from . import unified_auth as unified
    from apps.customers.portal_preview import SESSION_KEY as customer_preview, selected_fixture
    from apps.participants.portal_preview import SESSION_KEY as participant_preview, participant_context
    if request.session.get(customer_preview):
        selected_fixture(request)
        return redirect('customer_portal:home')
    if request.session.get(participant_preview):
        with participant_context(request):
            return redirect('participants:profile')
    if request.user.is_authenticated:
        realm = next((key for key, backend in unified.BACKENDS.items() if request.session.get('_auth_user_backend') == backend), None)
        if realm and unified.eligible(realm, request.user):
            item = {'realm': realm, 'user': str(request.user.pk), 'proof': request.user.get_session_auth_hash()}
            default = home(request.user) if realm == 'staff' else reverse('customer_portal:home') if realm == 'customer' else reverse('participants:profile')
            return redirect(unified.safe_destination(request, request.GET.get('next', ''), realm, request.user) or default)
    if request.method == 'GET':
        request.session.pop(unified.PENDING, None)
    form = unified.LoginForm(unified.submitted_data(request) if request.method == 'POST' else None)
    if request.method == 'POST':
        request.session.pop(unified.PENDING, None)
        valid = form.is_valid()
        allowed = unified.allowed_attempt(request, form.cleaned_data.get('username', '') if valid else '')
        if valid and allowed:
            matches = unified.authenticate_all(request, form.cleaned_data['username'], form.cleaned_data['password'])
            destination = request.POST.get('next', '')
            if len(matches) == 1:
                target = unified.establish(request, matches[0], destination)
                if target:
                    return redirect(target)
            elif len(matches) > 1:
                unified.begin_choice(request, matches, destination)
                return redirect('accounts:identity-choice')
        form.add_error(None, unified.GENERIC)
    response = render(request, 'accounts/unified_login.html', {'page_title': 'Sign in', 'form': form,
        'next': request.POST.get('next', '') if request.method == 'POST' else request.GET.get('next', ''),
        'recovery_url': reverse('accounts:recovery')})
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'same-origin'
    return response


@never_cache
@sensitive_post_parameters('challenge')
@require_http_methods(['GET', 'POST'])
def identity_choice(request):
    from . import unified_auth as unified
    if request.method == 'POST':
        target = unified.choose(request, request.POST.get('challenge', ''), request.POST.get('choice', ''))
        if target:
            return redirect(target)
        messages.error(request, unified.GENERIC)
        return redirect('accounts:login')
    pending = unified.pending_choice(request)
    if not pending:
        return redirect('accounts:login')
    choices = []
    for index, item in enumerate(pending['identities']):
        user = unified.fresh_identity(item)
        if user:
            label = {'staff': 'Staff workspace', 'customer': 'Customer workspace', 'participant': 'Participant Portal'}[item['realm']]
            if item['realm'] == 'staff':
                label += ' — ' + user.username
            choices.append({'value': str(index), 'label': label})
    response = render(request, 'accounts/identity_choice.html', {'page_title': 'Choose your workspace',
        'choices': choices, 'challenge': pending['nonce']})
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'same-origin'
    return response

@never_cache
@require_POST
def sign_out(request):
    from .unified_auth import BACKENDS
    from apps.customers.portal_preview import SESSION_KEY as customer_preview
    from apps.participants.portal_preview import SESSION_KEY as participant_preview
    backend = request.session.get('_auth_user_backend')
    if request.session.get(customer_preview) or backend == BACKENDS['customer']:
        from apps.customers.portal_auth import sign_out as customer_logout
        return customer_logout(request)
    if request.session.get(participant_preview) or backend == BACKENDS['participant']:
        from apps.participants.portal_auth import sign_out as participant_logout
        return participant_logout(request)
    if active_staff(request.user):audit(request.user,'signed_out',request.user,reason='Account holder signed out.')
    logout(request);return redirect('accounts:login')

@never_cache
@sensitive_post_parameters('old_password','new_password1','new_password2')
@login_required(login_url='accounts:login')
@require_http_methods(['GET','POST'])
def password_change(request):
    if not active_staff(request.user):raise PermissionDenied
    form=PasswordChangeForm(request.user,request.POST if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            lock_role_groups();user=get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if not active_staff(user) or user.get_session_auth_hash()!=request.user.get_session_auth_hash():raise PermissionDenied
            locked=PasswordChangeForm(user,request.POST)
            if not locked.is_valid():form=locked
            else:
                before=account_state(user);user=locked.save(commit=False);user.security_revision+=1;user.save()
                StaffInvitation.objects.filter(user=user,accepted_at__isnull=True,revoked_at__isnull=True).update(revoked_at=timezone.now())
                audit(user,'password_changed',user,before,'Account holder changed their password; other sessions and recovery links revoked.')
                request.user=user;update_session_auth_hash(request,user)
                messages.success(request,'Password changed. Other sessions and setup links have been revoked.')
                return redirect('internal:settings')
    return page(request,'Change staff password',form,submit_label='Change password',mode='change')

@never_cache
@require_http_methods(['GET','POST'])
def _staff_recovery(request):
    form=RecoveryForm(request.POST if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        allowed_ip=rate_limit('recovery-ip',request.META.get('REMOTE_ADDR',''),20)
        allowed_user=rate_limit('recovery-user',form.cleaned_data['username'].casefold(),5,60)
        if allowed_ip and allowed_user:
            users=get_user_model().objects.filter(username=form.cleaned_data['username'],email__iexact=form.cleaned_data['email'],is_active=True,is_staff=True,participant_account__isnull=True)
            user=users.first()
            if user and user.has_usable_password():
                item=issue_invitation(user=user,purpose='reset',reason='Account holder requested staff recovery.')
                url=request.build_absolute_uri(reverse('accounts:activate',args=[invitation_token(item)]))
                try:send_mail('Reset your Built to Work password',link_email('We got a request to reset your staff password.','Choose a new password',url),None,[user.email],fail_silently=False)
                except Exception:pass
        return page(request,'Check your email',mode='sent',notice='If the details match an eligible staff account, recovery instructions will be sent. Contact an Administrator if you still need help.')
    return page(request,'Recover staff access',form,mode='recovery',submit_label='Send recovery instructions')


@never_cache
@sensitive_variables()
@sensitive_post_parameters('email','username')
@require_http_methods(['GET','POST'])
def recovery(request):
    from . import unified_recovery as unified
    unified.guard_preview(request)
    form=unified.RecoveryRequestForm(request.POST if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        email=form.cleaned_data['email'];username=form.cleaned_data['username']
        checks=[rate_limit('unified-recovery-ip',request.META.get('REMOTE_ADDR',''),15),
                rate_limit('unified-recovery-email',email.casefold(),3)]
        if all(checks):
            unified.dispatch(request,email,username)
        return page(request,'Check your email',mode='sent',notice=unified.NOTICE)
    response=render(request,'accounts/unified_recovery.html',{'page_title':'Reset your password','form':form})
    response['Cache-Control']='private, no-store';response['Referrer-Policy']='same-origin'
    return response
