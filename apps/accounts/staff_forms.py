from django import forms
from apps.staffing.models import StaffProfile
from .roles import ROLE_GROUPS
class StaffAccountForm(forms.Form):
    username=forms.CharField(max_length=150)
    can_finalize_event_reports=forms.BooleanField(required=False,label="Can finalize event reports",help_text="Only for Administrators and Managers.")
    can_approve_weekly_payroll=forms.BooleanField(required=False,label="Can approve weekly payroll",help_text="Only for Administrators. Approving doesn't send any money.")
    email=forms.EmailField()
    first_name=forms.CharField(max_length=150)
    last_name=forms.CharField(max_length=150)
    roles=forms.MultipleChoiceField(label='What can they do?',choices=[('manager','Manager'),('recruiter','Recruiter'),('event_staff','Event staff')],widget=forms.CheckboxSelectMultiple)
    profile=forms.ModelChoiceField(queryset=StaffProfile.objects.none(),required=False,label='Staff list person',help_text='Who this sign-in belongs to on the Staff list.')
    is_active=forms.BooleanField(required=False,label='Can sign in',help_text='Untick to turn off their sign-in without deleting anything.')
    confirm_identity=forms.BooleanField(label='I checked this is the right person.')
    revision=forms.CharField(required=False,widget=forms.HiddenInput)
    reason=forms.CharField(required=False,widget=forms.HiddenInput,label='Reason for this change')
    def __init__(self,*args,actor,target=None,**kwargs):
        super().__init__(*args,**kwargs)
        choices=[('recruiter','Recruiter'),('event_staff','Event staff')]
        if actor.is_superuser:choices=[('administrator','Administrator'),('manager','Manager')]+choices
        self.fields['roles'].choices=choices
        from django.db.models import Q
        self.fields['profile'].queryset=StaffProfile.objects.filter(Q(user__isnull=True)|Q(user=target) if target else Q(user__isnull=True)).order_by('last_name','first_name')
        if target is None:self.fields.pop('is_active')
        if not actor.is_superuser:
            self.fields.pop('can_finalize_event_reports')
            self.fields.pop('can_approve_weekly_payroll')
    @property
    def field_groups(self):
        """Presentation groups; original fields, defaults and validation stay authoritative."""
        sections = (
            ('identity', 'Sign-in', '', ('username', 'email', 'first_name', 'last_name')),
            ('roles', 'Roles', '', ('roles', 'profile', 'is_active')),
            ('financial', 'Money approvals',
             'Extra permissions on top of their role.',
             ('can_finalize_event_reports', 'can_approve_weekly_payroll')),
            ('confirmation', 'Check', '', ('reason', 'confirm_identity')),
        )
        groups, used = [], set()
        for key, title, description, names in sections:
            fields = [self[name] for name in names if name in self.fields and not self.fields[name].widget.is_hidden]
            if fields:
                groups.append({'key': key, 'title': title, 'description': description, 'fields': fields})
                used.update(field.name for field in fields)
        remaining = [field for field in self.visible_fields() if field.name not in used]
        if remaining:
            groups.append({'key': 'additional', 'title': 'Additional settings', 'description': '', 'fields': remaining})
        return groups

class AccountActionForm(forms.Form):
    revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(required=False,widget=forms.HiddenInput)
