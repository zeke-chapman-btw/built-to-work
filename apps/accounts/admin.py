from django.contrib import admin
from django.contrib.auth.admin import UserAdmin,GroupAdmin
from django.contrib.auth.models import Group
from django.contrib.auth.forms import UserChangeForm
from django.db import transaction
from django import forms
from django.db.models import F
from .models import User
from .roles import lock_role_groups
from .staff_services import account_state,audit
from apps.core.audit import record_audit
class AuditedUserForm(UserChangeForm):
    audit_reason=forms.CharField(label='Reason for account change',widget=forms.Textarea(attrs={'rows':2}))
    def clean(self):
        values=super().clean()
        from apps.participants.models import ParticipantAccount
        if self.instance.pk and values.get('is_staff') and ParticipantAccount.objects.filter(user=self.instance).exists():raise forms.ValidationError('Staff and participant identities must remain separate.')
        if self.instance.pk and self.instance.is_superuser and (not values.get('is_superuser') or not values.get('is_active') or not values.get('is_staff')) and User.objects.filter(is_superuser=True,is_active=True,is_staff=True).exclude(pk=self.instance.pk).count()==0:raise forms.ValidationError('Retain at least one active Administrator.')
        return values
@admin.register(User)
class CustomUserAdmin(UserAdmin):
    readonly_fields=('id','security_revision')
    list_display=('username','email','first_name','last_name','is_staff','is_active')
    form=AuditedUserForm
    fieldsets=UserAdmin.fieldsets+(('Change reason',{'fields':('audit_reason',)}),)
    def has_module_permission(self,request):return request.user.is_active and request.user.is_superuser
    def has_view_permission(self,request,obj=None):return self.has_module_permission(request)
    def has_add_permission(self,request):return self.has_module_permission(request)
    def has_change_permission(self,request,obj=None):return self.has_module_permission(request)
    def has_delete_permission(self,request,obj=None):return False
    def save_model(self,request,obj,form,change):
        lock_role_groups()
        request._staff_before=account_state(User.objects.get(pk=obj.pk)) if change else None
        super().save_model(request,obj,form,change)
    @transaction.atomic
    def user_change_password(self,request,id,form_url=""):
        response=super().user_change_password(request,id,form_url)
        if request.method=="POST" and response.status_code in (302,303):
            from .models import StaffInvitation
            from django.utils import timezone
            obj=User.objects.select_for_update().get(pk=id)
            before=account_state(obj)
            obj.security_revision+=1;obj.save(update_fields=["security_revision"])
            StaffInvitation.objects.filter(user=obj,accepted_at__isnull=True,revoked_at__isnull=True).update(revoked_at=timezone.now())
            audit(request.user,"admin_password_changed",obj,before,"Administrator used the protected password-change workflow.")
        return response
    def save_related(self,request,form,formsets,change):
        super().save_related(request,form,formsets,change)
        obj=form.instance;before=getattr(request,'_staff_before',None)
        if before!=account_state(obj):
            User.objects.filter(pk=obj.pk).update(security_revision=F('security_revision')+1);obj.refresh_from_db()
            audit(request.user,'admin_changed',obj,before,form.cleaned_data.get('audit_reason','Administrator created an account.'))
try:admin.site.unregister(Group)
except admin.sites.NotRegistered:pass
@admin.register(Group)
class ProtectedGroupAdmin(GroupAdmin):
    class AuditGroupForm(forms.ModelForm):
        audit_reason=forms.CharField(label="Reason for permission change",widget=forms.Textarea(attrs={"rows":2}))
        class Meta:
            model=Group
            fields="__all__"
    form=AuditGroupForm
    def has_module_permission(self,request):return request.user.is_active and request.user.is_superuser
    def has_view_permission(self,request,obj=None):return self.has_module_permission(request)
    def has_add_permission(self,request):return self.has_module_permission(request)
    def has_change_permission(self,request,obj=None):return self.has_module_permission(request)
    def has_delete_permission(self,request,obj=None):return False
    def save_model(self,request,obj,form,change):
        lock_role_groups()
        request._group_before={'name':Group.objects.get(pk=obj.pk).name,'permissions':list(Group.objects.get(pk=obj.pk).permissions.values_list('pk',flat=True))} if change else None
        super().save_model(request,obj,form,change)
    def save_related(self,request,form,formsets,change):
        super().save_related(request,form,formsets,change);obj=form.instance
        User.objects.filter(groups=obj).update(security_revision=F('security_revision')+1)
        record_audit(actor=request.user,action='staff.role.configuration_changed',instance=obj,source='protected_role_admin',old_data=getattr(request,'_group_before',None),new_data={'name':obj.name,'permissions':list(obj.permissions.values_list('pk',flat=True))},reason=form.cleaned_data['audit_reason'])
