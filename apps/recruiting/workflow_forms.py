import uuid
from django import forms
from django.contrib.auth import get_user_model
from apps.accounts.roles import manager
from apps.operations.forms import TaskForm
from apps.operations.task_access import tasks_for
from .models import RecruitingStage, CandidateInterest, CandidateOutreach


def staff_choices():
    from .access import fresh_staff
    from django.core.exceptions import PermissionDenied
    result=[]
    for user in get_user_model().objects.filter(is_active=True,is_staff=True):
        try:fresh_staff(user)
        except PermissionDenied:continue
        result.append((user.pk,user.get_full_name() or user.username))
    return result


class VersionReasonForm(forms.Form):
    expected_revision=forms.IntegerField(widget=forms.HiddenInput)
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={'rows':2}))


class StageChangeForm(VersionReasonForm):
    stage=forms.ModelChoiceField(queryset=RecruitingStage.objects.filter(is_active=True))
    confirm_hired=forms.BooleanField(required=False,label='BTW confirms that the actual hiring outcome occurred')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.fields['stage'].label_from_instance=lambda obj:obj.label


class ReassignmentForm(VersionReasonForm):
    user=forms.ChoiceField(label='Responsible staff member')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.fields['user'].choices=staff_choices()


class CollaborationForm(ReassignmentForm):
    mode=forms.ChoiceField(choices=[('owner','Responsible owner'),('view','View-only collaborator'),('manage','Managing collaborator'),('revoke','Revoke collaboration')])


class PublicationForm(VersionReasonForm):
    expected_need_revision=forms.UUIDField(widget=forms.HiddenInput)
    action=forms.ChoiceField(choices=[('publish','Publish a reviewed version'),('unpublish','Unpublish'),('close','Close to new interest')])
    title=forms.CharField(max_length=200,required=False)
    trade=forms.CharField(max_length=160,required=False)
    summary=forms.CharField(max_length=3000,required=False,widget=forms.Textarea(attrs={'rows':3}))
    location=forms.CharField(max_length=240,required=False)
    employment_type=forms.CharField(max_length=160,required=False)
    schedule=forms.CharField(max_length=500,required=False)
    pay=forms.CharField(max_length=500,required=False)
    qualifications=forms.CharField(max_length=3000,required=False,widget=forms.Textarea(attrs={'rows':3}))
    travel=forms.CharField(max_length=500,required=False)
    expires_at=forms.DateTimeField(required=False,widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    approved_copy=forms.BooleanField(required=False,label='I reviewed these participant-facing details against the approved position version')
    def clean(self):
        data=super().clean()
        if data.get('action')=='publish' and not data.get('approved_copy'):self.add_error('approved_copy','Review the approved version before publishing.')
        return data
    def details(self):
        return {k:self.cleaned_data[k] for k in ['title','trade','summary','location','employment_type','schedule','pay','qualifications','travel']}


class OutreachForm(VersionReasonForm):
    operation_id=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    method=forms.ChoiceField(choices=CandidateOutreach._meta.get_field('method').choices)
    contacted_at=forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    outcome=forms.CharField(max_length=160)
    response=forms.CharField(max_length=500,required=False,widget=forms.Textarea(attrs={'rows':2}))
    notes=forms.CharField(max_length=3000,required=False,widget=forms.Textarea(attrs={'rows':3}))
    task=forms.ModelChoiceField(queryset=None,required=False,label='Existing follow-up Task (optional)')
    def __init__(self,*args,actor,**kwargs):
        super().__init__(*args,**kwargs);self.fields['reason'].required=False;self.fields['task'].queryset=tasks_for(actor).filter(archived_at__isnull=True)


class ReviewForm(VersionReasonForm):
    participant_message=forms.CharField(required=False,max_length=500,widget=forms.Textarea(attrs={'rows':3}),
        label='Message for the Participant',help_text='Required for Information Needed. Visible to the Participant as a profile update request. Keep private review notes in Internal review note.')
    def clean(self):
        data=super().clean()
        if data.get('status')=='information' and not data.get('participant_message'):
            self.add_error('participant_message','Explain what the Participant should review or update.')
        if data.get('participant_message') and data.get('status')!='information':
            self.add_error('participant_message','Use a participant message only with Information Needed.')
        return data

    expected_need_revision=forms.UUIDField(widget=forms.HiddenInput)
    operation_id=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    status=forms.ChoiceField(choices=[c for c in CandidateInterest._meta.get_field('status').choices if c[0]!='withdrawn'])
    task=forms.ModelChoiceField(queryset=None,required=False)
    select_candidate=forms.BooleanField(required=False,label='Explicitly select this eligible person for the recruiting pipeline')
    expected_conflicts=forms.CharField(widget=forms.HiddenInput)
    acknowledge_conflicts=forms.BooleanField(required=False,label='I reviewed the current recruiting engagements shown here')
    def __init__(self,*args,actor,**kwargs):
        super().__init__(*args,**kwargs);self.fields['reason'].label='Internal review note';self.fields['task'].queryset=tasks_for(actor).filter(archived_at__isnull=True)


class FollowupForm(TaskForm):
    operation_id=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)


class StageConfigurationForm(VersionReasonForm):
    key=forms.SlugField(max_length=80, label='Stable code', help_text='Set once. Existing pipeline history retains this code.')
    label=forms.CharField(max_length=100, label='Stage name')
    position=forms.IntegerField(min_value=0,max_value=32767, label='Display order', help_text='Lower numbers appear first.')
    is_active=forms.BooleanField(required=False,initial=True, label='Available for new selections')
    expected_revision=forms.IntegerField(required=False,widget=forms.HiddenInput)
