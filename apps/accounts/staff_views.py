from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.forms import SetPasswordForm
from .roles import require_account_manager,require_target,roles_for,protected_target
from .staff_services import save_account,revision,set_active,issue_invitation,invitation_token,read_invitation,accept_invitation,rate_limit
from .staff_forms import StaffAccountForm,AccountActionForm

def account_manager(view):
    @login_required(login_url='/admin/login/')
    @wraps(view)
    def secured(request,*args,**kwargs):
        require_account_manager(request.user);return view(request,*args,**kwargs)
    return secured

def page(request,template,**context):
    from apps.internal_ui.views import _staff_context
    return render(request,template,_staff_context(request,**context))

@account_manager
@require_http_methods(['GET'])
def index(request):
    users=get_user_model().objects.filter(is_staff=True).order_by('username')
    if not request.user.is_superuser:users=[u for u in users if not protected_target(u)]
    from django.core.paginator import Paginator
    return page(request,'accounts/staff_accounts.html',page_title='Staff accounts',accounts=Paginator(users,30).get_page(request.GET.get('page')))

@account_manager
@require_http_methods(['GET','POST'])
def edit(request,user_id=None):
    target=get_object_or_404(get_user_model(),pk=user_id,is_staff=True) if user_id else None
    if target:require_target(request.user,target)
    initial={}
    if target:
        initial={name:getattr(target,name) for name in ['username','email','first_name','last_name','is_active']}
        initial.update(roles=roles_for(target),revision=revision(target))
        from apps.staffing.models import StaffProfile
        profile=StaffProfile.objects.filter(user=target).first();initial['profile']=profile.pk if profile else None
    form=StaffAccountForm(request.POST if request.method=='POST' else None,actor=request.user,target=target,initial=initial)
    if request.method=='POST':
        from .staff_services import check_fields
        check_fields(request.POST)
        if form.is_valid():
            try:result=save_account(actor=request.user,data=form.cleaned_data,user_id=user_id)
            except ValidationError as exc:form.add_error(None,exc)
            else:
                messages.success(request,'Staff account saved. Existing identities were not merged.')
                return redirect('accounts:staff-detail',user_id=result.pk)
    return page(request,'accounts/staff_account_form.html',page_title='Edit staff account' if target else 'Create staff account',form=form,target=target)

@account_manager
@require_http_methods(['GET'])
def detail(request,user_id):
    target=get_object_or_404(get_user_model(),pk=user_id,is_staff=True);require_target(request.user,target)
    from apps.core.models import AuditLog
    from django.contrib.contenttypes.models import ContentType
    logs=AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(target),object_id=str(target.pk),action__startswith='staff.account.').order_by('-timestamp')[:30]
    return page(request,'accounts/staff_account_detail.html',page_title='Staff account',target=target,roles=sorted(roles_for(target)),history=logs,invite_form=AccountActionForm(initial={'revision':revision(target)},auto_id='invite_%s'),state_form=AccountActionForm(initial={'revision':revision(target)},auto_id='state_%s'))

@account_manager
@require_http_methods(['POST'])
def action(request,user_id,action):
    target=get_object_or_404(get_user_model(),pk=user_id,is_staff=True);require_target(request.user,target)
    form=AccountActionForm(request.POST)
    if not form.is_valid():return page(request,'accounts/staff_account_form.html',page_title='Review account action',form=form,target=target)
    try:
        if action in ['deactivate','reactivate']:
            set_active(actor=request.user,user_id=target.pk,active=action=='reactivate',expected_revision=form.cleaned_data['revision'],reason=form.cleaned_data['reason'])
            messages.success(request,'Account state updated; previous sessions and setup links are revoked.')
        elif action=='invite':
            if form.cleaned_data['revision']!=revision(target):raise ValidationError('This account changed. Reload before inviting.')
            item=issue_invitation(user=target,actor=request.user,reason=form.cleaned_data['reason'],expected_revision=form.cleaned_data['revision'])
            url=request.build_absolute_uri(reverse('accounts:activate',args=[invitation_token(item)]))
            def deliver():
                try:send_mail('Set up your Built to Work staff account','Set your own password using this private link within 24 hours:\n'+url,None,[target.email],fail_silently=False)
                except Exception:return
            transaction.on_commit(deliver)
            messages.success(request,'Setup invitation requested. Email delivery depends on the configured transport.')
        else:raise ValidationError('Unsupported account action.')
    except ValidationError as exc:messages.error(request,' '.join(exc.messages))
    return redirect('accounts:staff-detail',user_id=target.pk)

@require_http_methods(['GET','POST'])
def activate(request,token):
    item=read_invitation(token)
    form=SetPasswordForm(item.user,request.POST if request.method=='POST' else None) if item else None
    if request.method=='POST':
        allowed=rate_limit('setup-ip',request.META.get('REMOTE_ADDR',''),20)
        if item:allowed=rate_limit('setup',item.pk,8) and allowed
        if form and not allowed:
            form.is_valid();form.add_error(None,'Please wait before trying again.')
        elif form and form.is_valid():
            try:accept_invitation(token=token,password=form.cleaned_data['new_password1'])
            except ValidationError as exc:form.add_error(None,exc)
            else:return redirect('/admin/login/?next=/internal/')
    response=render(request,'accounts/staff_activation.html',{'form':form,'page_title':'Set up your staff account','invalid':not item})
    response['Cache-Control']='private, no-store';response['Referrer-Policy']='same-origin';return response
