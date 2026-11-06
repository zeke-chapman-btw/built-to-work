"""Compact staff-only primary identifier editor."""
from django import forms
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from apps.internal_ui.people_editors import people_return, people_saved
from apps.internal_ui.record_editors import editor_response
from .workspace_views import workspace, context, person_for
from .workspace_access import require_people
from .identifier_definitions import PrimaryIdentifier, ORIGIN_LABELS
from .identifier_services import revision, save_identifier
from .models import IntakeFieldState

class IdentifierForm(forms.Form):
    identifier = forms.ChoiceField(required=False, choices=[('', 'Choose an identifier')] + list(PrimaryIdentifier.choices))
    revision = forms.CharField(widget=forms.HiddenInput)
    audit_reason = forms.CharField(max_length=500, label='Reason for this change', widget=forms.Textarea(attrs={'rows': 2}))
    def clean_identifier(self):
        value = self.cleaned_data['identifier']
        if not value and self.data.get('operation') != 'clear':
            raise ValidationError('Choose an identifier, or use Clear identifier.')
        return value

@workspace
@require_http_methods(['GET', 'POST'])
def edit(request, participant_id):
    from apps.crm.access import fresh_staff
    request.user = fresh_staff(request.user)
    require_people(request.user, 'participants.change_participant')
    person = person_for(participant_id, editing=True)
    state = IntakeFieldState.objects.filter(participant=person, destination='primary_identifier').first()
    form = IdentifierForm(request.POST if request.method == 'POST' else None,
        initial={'identifier': person.primary_identifier, 'revision': revision(person, state)})
    conflict = False
    if request.method == 'POST' and form.is_valid():
        try:
            save_identifier(actor=request.user, participant_id=person.pk,
                value='' if request.POST.get('operation') == 'clear' else form.cleaned_data['identifier'],
                expected_revision=form.cleaned_data['revision'], reason=form.cleaned_data['audit_reason'])
        except ValidationError as exc:
            form.add_error(None, exc)
            conflict = any('Reload' in message for message in exc.messages)
        else:
            messages.success(request, 'Identifier cleared and protected.' if request.POST.get('operation') == 'clear' else 'Identifier saved and protected.')
            return people_saved(request, 'participants:staff-profile', participant_id=person.pk)
    back = people_return(request, person)
    return editor_response(request, template='participants/identifier_editor.html',
        panel='participants/identifier_editor_panel.html', context=context(request,
            participant=person, page_title='Participant identifier', form=form, back=back,
            identifier_origin=ORIGIN_LABELS.get((state.provenance or {}).get('origin', 'unclassified'), 'Source requires review') if state else ORIGIN_LABELS['unclassified'],
            save_conflict=conflict, full_page_editor=request.headers.get('X-BTW-Editor') != '1'))
