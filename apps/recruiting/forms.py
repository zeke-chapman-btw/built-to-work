from apps.core.optional_reason import OptionalReasonMixin
from django import forms
from .models import Classification, CandidateList, RecruitingProfile
from apps.participants.models import ParticipantCareerProfile


class ReasonForm(OptionalReasonMixin, forms.Form):
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={'rows': 2}))


class PreferencesForm(ReasonForm):
    revision = forms.IntegerField(widget=forms.HiddenInput)
    fingerprint = forms.CharField(widget=forms.HiddenInput)
    profile_revision = forms.CharField(required=False, widget=forms.HiddenInput)
    employment_interest = forms.ChoiceField(required=False,
        choices=[('', 'Unknown'), ('seeking','Yes'), ('open','Maybe'), ('not_seeking','No')], label='Interested in work')
    willing_to_travel = forms.TypedChoiceField(choices=[('', 'Not recorded'), ('yes', 'Yes'), ('maybe', 'Maybe'), ('no', 'No')],
        coerce=lambda v: {'yes': True, 'maybe': 'maybe', 'no': False}.get(v), required=False, empty_value=None)
    current_employment_status = forms.ChoiceField(choices=RecruitingProfile.CURRENT_EMPLOYMENT, required=False, label='Current employment status')
    employment_classification = forms.ModelChoiceField(queryset=Classification.objects.filter(kind='employment'), required=False)
    availability_classification = forms.ModelChoiceField(queryset=Classification.objects.filter(kind='availability'), required=False)
    willing_to_relocate = forms.TypedChoiceField(choices=[('', 'Unknown'), ('yes', 'Yes'), ('maybe', 'Maybe'), ('no', 'No')],
        coerce=lambda v: {'yes': True, 'maybe':'maybe', 'no': False}.get(v), required=False, empty_value=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.initial.get('current_employment_status') == '__retained__':
            self.fields['current_employment_status'].choices = list(RecruitingProfile.CURRENT_EMPLOYMENT) + [('__retained__', 'Retain: ' + self.initial.get('retained_employment_label', 'Previously recorded value'))]
        from django.db.models import Q
        for field, kind in [('employment_classification', 'employment'), ('availability_classification', 'availability')]:
            self.fields[field].queryset = Classification.objects.filter(kind=kind).filter(
                Q(is_active=True) | Q(pk=self.initial.get(field)))
            self.fields[field].empty_label = 'Not recorded'
        self.fields['employment_classification'].label = 'Employment classification (retained catalog)'
        self.fields['employment_classification'].help_text = 'Retained classification; current employment status is recorded separately.'
        self.fields['availability_classification'].label = 'Current availability'
        self.order_fields(['employment_interest', 'willing_to_travel', 'willing_to_relocate', 'current_employment_status', 'employment_classification', 'availability_classification', 'reason', 'revision', 'fingerprint'])

    def clean(self):
        data=super().clean()
        if 'current_employment_status' not in self.data:
            data.pop('current_employment_status',None)
        return data


class ListForm(forms.ModelForm):
    expected_revision = forms.IntegerField(initial=0, widget=forms.HiddenInput)
    class Meta:
        model = CandidateList
        fields = ['name', 'shared']
        labels = {'shared': 'Share with authorized recruiting staff (editing remains with owner/Manager)'}


class ConsolidationForm(ReasonForm):
    source_id = forms.IntegerField(widget=forms.HiddenInput)
    target_id = forms.IntegerField(widget=forms.HiddenInput)
    fingerprint = forms.CharField(widget=forms.HiddenInput)
    confirmed = forms.BooleanField(label='I reviewed both established identities and confirm they are the same person; original records and credentials remain separate.')


class ClassificationForm(OptionalReasonMixin, forms.ModelForm):
    expected_revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={'rows':2}))
    class Meta:
        model = Classification
        fields = ['kind', 'key', 'label', 'position', 'is_active']
        labels = {'kind': 'Profile category', 'key': 'Stable code', 'label': 'Display name',
                  'position': 'Display order', 'is_active': 'Available for new selections'}
        help_texts = {'key': 'Set once. Existing answers retain this code.',
                      'position': 'Lower numbers appear first.'}


class CampaignPolicyForm(ReasonForm):
    days = forms.IntegerField(min_value=0, max_value=3650, required=False, label='Exclusive days after actual completion',
        help_text='Unconfigured is not zero days. No planned date is used as actual completion.')
    actual_completion = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    expected_revision = forms.IntegerField(initial=0, widget=forms.HiddenInput)


class CampaignSourceForm(ReasonForm):
    campaign=forms.ModelChoiceField(queryset=None)
    exclusive=forms.BooleanField(required=False,initial=True,label='Exclusive when creating this new sourcing relationship')
    def __init__(self,*args,actor,**kwargs):
        super().__init__(*args,**kwargs)
        from .foundation import visible_campaigns
        self.fields['campaign'].queryset=visible_campaigns(actor,write=True)


class OpportunityForm(forms.Form):
    need = forms.ModelChoiceField(queryset=None, label='Approved position')
    owner = forms.ModelChoiceField(queryset=None, label='Responsible BTW staff owner')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        from django.contrib.auth import get_user_model
        from django.db.models import Q
        from apps.customers.models import WorkforceNeed
        self.fields['need'].queryset=WorkforceNeed.objects.filter(status='active',effective_revision__state='approved').select_related('effective_revision','organization')
        self.fields['owner'].queryset=get_user_model().objects.filter(is_active=True,is_staff=True).filter(Q(is_superuser=True)|Q(groups__name__in=['BTW Manager','BTW Recruiter'])).distinct()


class CriteriaForm(ReasonForm):
    expected_need_revision=forms.UUIDField(widget=forms.HiddenInput)
    expected_revision=forms.IntegerField(widget=forms.HiddenInput)
    required_skills=forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':2}),help_text='One explicit reported skill per line; gaps never automatically reject candidates.')
    preferred_skills=forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':2}))
    required_credentials=forms.ModelMultipleChoiceField(queryset=None,required=False)
    preferred_credentials=forms.ModelMultipleChoiceField(queryset=None,required=False)
    experience_minimum=forms.DecimalField(required=False,min_value=0,max_value=80,decimal_places=1,label='Required reported experience (years)')
    preferred_experience=forms.DecimalField(required=False,min_value=0,max_value=80,decimal_places=1)
    region=forms.CharField(required=False,max_length=160,label='Recorded city/state/region comparison')
    travel=forms.ChoiceField(required=False,choices=[('','No travel criterion'),('yes','Willing to travel'),('no','No travel preference')])
    relocation=forms.ChoiceField(required=False,choices=[('','No relocation criterion'),('yes','Willing to relocate'),('no','No relocation preference')])
    skill_importance=forms.IntegerField(initial=1,min_value=1,max_value=10)
    credential_importance=forms.IntegerField(initial=1,min_value=1,max_value=10)
    experience_importance=forms.IntegerField(initial=1,min_value=1,max_value=10)
    geography_importance=forms.IntegerField(initial=1,min_value=1,max_value=10)
    travel_importance=forms.IntegerField(initial=1,min_value=1,max_value=10)
    assessment_rules=forms.JSONField(required=False,initial=list,widget=forms.Textarea(attrs={'rows':4}),
        label='Optional version-pinned assessment comparisons',help_text='Advanced: explicit quiz category/question-snapshot hash or simulator/profile version, minimum percentage and importance. Missing results stay unknown.')

    def __init__(self,*args,actor,**kwargs):
        super().__init__(*args,**kwargs)
        from apps.participants.qualification_models import QualificationCatalogEntry
        allowed=QualificationCatalogEntry.objects.filter(is_active=True,kind__in=['license','certification']) if actor.has_perm('participants.view_participantqualification') else QualificationCatalogEntry.objects.none()
        for key in ['required_credentials','preferred_credentials']:self.fields[key].queryset=allowed

    def rules(self):
        d=self.cleaned_data;result=[]
        for field,required in [('required_skills',True),('preferred_skills',False)]:
            for text in d[field].splitlines():
                if text.strip():result.append({'kind':'skill','label':text.strip(),'value':text.strip(),'required':required,'weight':d['skill_importance']})
        for field,required in [('required_credentials',True),('preferred_credentials',False)]:
            for entry in d[field]:result.append({'kind':'certification','label':entry.name,'catalog':str(entry.stable_id),'required':required,'weight':d['credential_importance'],'verified_only':required})
        for field,required in [('experience_minimum',True),('preferred_experience',False)]:
            if d.get(field) is not None:result.append({'kind':'experience','label':('Required' if required else 'Preferred')+' reported experience','minimum':str(d[field]),'weight':d['experience_importance'],'required':required})
        if d['region']:result.append({'kind':'region','label':'Recorded region / preferred location','value':d['region'],'weight':d['geography_importance'],'required':False})
        for field in ['travel','relocation']:
            if d[field]:result.append({'kind':field,'label':field.title()+' preference','value':d[field],'weight':d['travel_importance'],'required':False})
        if d['assessment_rules']:
            if not isinstance(d['assessment_rules'],list) or any(not isinstance(r,dict) or r.get('kind')!='assessment' for r in d['assessment_rules']):
                raise forms.ValidationError('The advanced field accepts assessment comparisons only.')
            result.extend(d['assessment_rules'])
        from .matching import validate_rules
        return validate_rules(result)


class CandidateSelectionForm(ReasonForm):
    expected_need_revision=forms.UUIDField(widget=forms.HiddenInput)
    expected_conflicts=forms.CharField(widget=forms.HiddenInput)
    criteria_id=forms.UUIDField(required=False,widget=forms.HiddenInput)
    acknowledge_conflicts=forms.BooleanField(required=False,label='I reviewed the current other recruiting engagements and potential conflicts.')
