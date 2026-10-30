"""Customer forms reuse typed hiring terms, never staff-only ModelForms."""
from django import forms
from django.core.exceptions import ValidationError
from .models import WorkforceNeedRevision, Campaign, CustomerContact, CustomerLocation, EDITABLE_TERM_FIELDS
from .forms import HIRING_SECTIONS
from .services import CAMPAIGN_FIELDS
from .portal_policy import require_membership


class ScopedForm(forms.Form):
    org_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    record_revision = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)

    def clean(self):
        values = super().clean()
        if self.is_bound and set(self.data) - set(self.fields) - {'csrfmiddlewaretoken'}:
            raise ValidationError('Unsupported submitted fields. Reload the form before continuing.')
        return values


class PortalModelForm(ScopedForm, forms.ModelForm):
    action_policy = None

    def __init__(self, *args, actor, org_id, **kwargs):
        member = require_membership(actor, org_id, action=self.action_policy)
        super().__init__(*args, **kwargs)
        self.instance.organization = member.organization
        for key in ('primary_contact', 'location'):
            if key in self.fields:
                self.fields[key].queryset = (CustomerLocation if key == 'location' else CustomerContact).objects.filter(organization_id=org_id, is_active=True)
        for field in self.fields.values():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(attrs={'type': 'date'})
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs['rows'] = 3

    @property
    def groups(self):
        return [{'title': title, 'fields': [self[k] for k in keys if k in self.fields]} for title, keys in self.sections]


class NeedForm(PortalModelForm):
    action_policy = 'needs'
    working_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    sections = HIRING_SECTIONS

    class Meta:
        model = WorkforceNeedRevision
        fields = EDITABLE_TERM_FIELDS


class CampaignForm(PortalModelForm):
    action_policy = 'campaigns'
    sections = (('Campaign purpose', ('name', 'description', 'objectives')), ('Timing and contact', ('start_date', 'end_date', 'primary_contact')))

    class Meta:
        model = Campaign
        fields = CAMPAIGN_FIELDS


class ActionForm(ScopedForm):
    working_id = forms.UUIDField(required=False, widget=forms.HiddenInput)


class InviteForm(ScopedForm):
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100, required=False)
    email = forms.EmailField(label='Member email')


class MemberForm(ScopedForm):
    state = forms.ChoiceField(choices=[('enabled', 'Enabled'), ('suspended', 'Suspended'), ('removed', 'Removed')])
    manage_needs = forms.BooleanField(required=False, label='Manage Workforce Needs')
    manage_campaigns = forms.BooleanField(required=False, label='Manage Campaigns')


class SwitchForm(forms.Form):
    organization = forms.UUIDField()


class FeedbackForm(ScopedForm):
    action = forms.ChoiceField(choices=[('feedback', 'Publish feedback only'), ('review', 'Start review & publish feedback'), ('return', 'Return for changes & publish feedback')])
    body = forms.CharField(max_length=4000, label='Customer-visible feedback', widget=forms.Textarea(attrs={'rows': 5}), help_text='Everything entered here will be visible to authorized users of this company. Internal notes and reasons are not copied.')
