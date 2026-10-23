from django import forms
from .models import Participant, ParticipantCareerProfile
from .identity import PROFILE_FIELDS, CAREER_FIELDS
from .document_forms import validate_document_file
from .models import ParticipantDocumentCategory


class PortalProfileForm(forms.ModelForm):
    revision = forms.CharField(widget=forms.HiddenInput)
    class Meta:
        model = Participant
        fields = ('first_name', 'last_name', 'preferred_name', 'contact_email', 'contact_phone', 'city', 'state', 'postal_code')
        labels = {'contact_email': 'Contact email', 'contact_phone': 'Phone', 'postal_code': 'ZIP / postal code'}


class PortalCareerForm(forms.ModelForm):
    class Meta:
        model = ParticipantCareerProfile
        fields = tuple(sorted(CAREER_FIELDS))
        widgets = {field: forms.Textarea(attrs={'rows': 3}) for field in ('career_interests', 'skills_interests', 'education_training')}


class PortalDocumentForm(forms.Form):
    category = forms.ModelChoiceField(queryset=ParticipantDocumentCategory.objects.none())
    file = forms.FileField(help_text='PDF, PNG or JPEG, up to 20 MB. Upload only your own supporting documents.')
    description = forms.CharField(max_length=500, required=False, label='Description (optional)')
    issue_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    expiration_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = ParticipantDocumentCategory.objects.filter(is_active=True)
    def clean_file(self):
        upload = self.cleaned_data['file']
        self.filename, self.content_type = validate_document_file(upload)
        return upload
    def clean(self):
        values = super().clean()
        if values.get('issue_date') and values.get('expiration_date') and values['expiration_date'] < values['issue_date']:
            self.add_error('expiration_date', 'Expiration cannot be before the issue date.')
        return values
