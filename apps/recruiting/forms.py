from django import forms
from .models import Classification, CandidateList, RecruitingProfile


class ReasonForm(forms.Form):
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={'rows': 2}))


class PreferencesForm(ReasonForm):
    revision = forms.IntegerField(widget=forms.HiddenInput)
    employment_classification = forms.ModelChoiceField(queryset=Classification.objects.filter(kind='employment'), required=False)
    availability_classification = forms.ModelChoiceField(queryset=Classification.objects.filter(kind='availability'), required=False)
    willing_to_relocate = forms.TypedChoiceField(choices=[('', 'Not recorded'), ('yes', 'Yes'), ('no', 'No')],
        coerce=lambda v: {'yes': True, 'no': False}.get(v), required=False, empty_value=None)


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


class ClassificationForm(forms.ModelForm):
    class Meta:
        model = Classification
        fields = ['kind', 'key', 'label', 'position', 'is_active']


class CampaignPolicyForm(ReasonForm):
    days = forms.IntegerField(min_value=0, max_value=3650, required=False, label='Exclusive days after actual completion',
        help_text='Unconfigured is not zero days. No planned date is used as actual completion.')
    actual_completion = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    expected_revision = forms.IntegerField(initial=0, widget=forms.HiddenInput)
