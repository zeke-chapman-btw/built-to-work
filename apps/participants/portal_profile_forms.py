"""Small, independent editors over the existing canonical profile records."""
import uuid
from django import forms
from .portal_forms import PortalProfileForm, PortalCareerForm
from .workspace_models import ParticipantWorkExperience

PERSONAL_FIELDS = ('first_name', 'last_name', 'preferred_name', 'contact_email', 'contact_phone', 'city', 'state', 'postal_code')
CAREER_SECTIONS = {
    'preferences': ('career_interests', 'employment_status', 'employment_interest', 'preferred_work_type', 'preferred_locations', 'years_of_experience'),
    'availability': ('work_availability', 'willing_to_travel'),
    'skills-summary': ('skills_interests',),
    'education-summary': ('education_training',),
}


def section_form(section, data=None, *, person, career, revision, preview=False, current_interests=''):
    if section == 'personal':
        form = PortalProfileForm(data, instance=person, initial={'revision': revision})
        if preview:
            for name in ('first_name', 'last_name', 'contact_email', 'contact_phone'):
                form.fields[name].disabled = True
        return form
    fields = CAREER_SECTIONS[section]
    initial = {'revision': revision}
    if section == 'preferences' and not career.career_interests:
        initial['career_interests'] = current_interests
    form = PortalCareerForm(data, instance=career, initial=initial)
    for name in list(form.fields):
        if name not in fields:
            del form.fields[name]
    form.fields['revision'] = forms.CharField(widget=forms.HiddenInput, initial=revision)
    return form


class WorkEntryForm(forms.ModelForm):
    revision = forms.CharField(required=False, widget=forms.HiddenInput)
    request_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    role = forms.CharField(max_length=200, label='Role, trade or activity')
    class Meta:
        model = ParticipantWorkExperience
        fields = ('role', 'employer', 'location', 'started_on', 'ended_on', 'is_current', 'description')
        labels = {'employer': 'Employer or organization (optional)', 'is_current': 'I still do this work',
                  'started_on': 'Start date (if known)', 'ended_on': 'End date (if known)',
                  'description': 'Work, equipment and responsibilities'}
        widgets = {'description': forms.Textarea(attrs={'rows': 4}),
                   'started_on': forms.DateInput(attrs={'type': 'date'}), 'ended_on': forms.DateInput(attrs={'type': 'date'})}


class QualificationEntryForm(forms.Form):
    revision = forms.CharField(required=False, widget=forms.HiddenInput)
    request_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    custom_name = forms.CharField(max_length=200, label='Name')
    reported_provider = forms.CharField(max_length=200, required=False, label='School, training provider or issuer (optional)')
    experience_details = forms.CharField(max_length=3000, required=False, label='Details (optional)', widget=forms.Textarea(attrs={'rows': 4}))
    years_of_experience = forms.DecimalField(max_digits=4, decimal_places=1, min_value=0, max_value=80, required=False, label='Years of experience (optional)')
    issue_date = forms.DateField(required=False, label='Completed or issued (if known)', widget=forms.DateInput(attrs={'type': 'date'}))
    expiration_date = forms.DateField(required=False, label='Expires (if applicable)', widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, section, record=None, **kwargs):
        if record:
            kwargs['initial'] = {key: getattr(record, key) for key in self.base_fields if hasattr(record, key)}
            kwargs['initial'].update(revision=record.updated_at.isoformat(), request_key=record.portal_request_key or uuid.uuid4())
        super().__init__(*args, **kwargs)
        self.fields['custom_name'].label = {'skills': 'Skill or trade', 'training': 'Education or training', 'credentials': 'Certification or license'}[section]
        if section != 'skills':
            del self.fields['years_of_experience']
        if section == 'skills':
            for field in ('reported_provider', 'issue_date', 'expiration_date'):
                del self.fields[field]

    def clean(self):
        data = super().clean()
        if data.get('issue_date') and data.get('expiration_date') and data['expiration_date'] < data['issue_date']:
            self.add_error('expiration_date', 'Expiration cannot be before the issue date.')
        return data
