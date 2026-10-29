"""Staff username authentication. Participant portal identities stay separate."""
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
from django.views.decorators.debug import sensitive_post_parameters
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
    response['Cache-Control']='private, no-store';response['Referrer-Policy']='no-referrer';return response

@never_cache
@sensitive_post_parameters('password')
@require_http_methods(['GET','POST'])
def sign_in(request):
    if active_staff(request.user):return redirect(safe_next(request,request.GET.get('next')) or home(request.user))
    form=StaffLoginForm(request,data=request.POST if request.method=='POST' else None)
    if request.method=='POST':
        allowed_ip=rate_limit('login-ip',request.META.get('REMOTE_ADDR',''),30)
        allowed_user=rate_limit('login-user',request.POST.get('username','').strip().casefold(),10)
        if not (allowed_ip and allowed_user):
            form.is_valid();form.add_error(None,'Unable to sign in. Please wait before trying again.')
        elif form.is_valid():
            user=form.get_user();login(request,user,backend='django.contrib.auth.backends.ModelBackend')
            audit(user,'signed_in',user,reason='Account holder signed in through BTW staff login.')
            return redirect(safe_next(request,request.POST.get('next')) or home(user))
    return page(request,'Staff sign in',form,submit_label='Sign in',mode='login',next=safe_next(request,request.GET.get('next') or request.POST.get('next')) or '')

@never_cache
@require_POST
def sign_out(request):
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
def recovery(request):
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
                try:send_mail('Reset your Built to Work staff password','Use this private single-use link within 24 hours:\n'+url,None,[user.email],fail_silently=False)
                except Exception:pass
        return page(request,'Check your email',mode='sent',notice='If the details match an eligible staff account, recovery instructions will be sent. Contact an Administrator if you still need help.')
    return page(request,'Recover staff access',form,mode='recovery',submit_label='Send recovery instructions')
