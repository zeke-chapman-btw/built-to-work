"""Focused staff profile forms with audited, field-scoped writes."""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.crm.access import fresh_staff
from apps.internal_ui.people_editors import people_return, people_saved
from apps.internal_ui.record_editors import editor_response
from apps.recruiting.access import serialize_identity_write
from .models import Participant, ParticipantCareerProfile, IntakeFieldState
from .workspace_views import workspace, context, person_for
from .workspace_access import require_people
from .workspace_services import lock_person, profile_revision, snapshot, audit
from .identity import update_participant_profile
from .identifier_services import revision as type_revision, save_identifier
from .intake_discovery import effective_travel

CARDS = {
    'identity': ('Contact', Participant, ('first_name', 'last_name', 'preferred_name', 'contact_email', 'contact_phone', 'city', 'state', 'postal_code')),
    'skills': ('Skills & Trades', ParticipantCareerProfile, ('career_interests', 'skills_interests', 'years_of_experience')),
    'education': ('Education & Training', ParticipantCareerProfile, ('education_training',)),
    'preferences': ('Work preferences', ParticipantCareerProfile, ('employment_interest', 'preferred_work_type', 'preferred_locations', 'career_interests')),
}


class ProfileCardForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, card, person, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        title, model, fields = CARDS[card]
        record = person if model is Participant else getattr(person, 'career_profile', None)
        for name in fields:
            field = model._meta.get_field(name).formfield()
            field.label = {'career_interests': 'Trade and career interests', 'skills_interests': 'Reported skills and interests',
                'years_of_experience': 'Reported overall experience (years)', 'education_training': 'Education & training',
                'employment_interest': 'Seeking work', 'preferred_work_type': 'Work type',
                'preferred_locations': 'Preferred work locations'}.get(name, field.label)
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs['rows'] = 3
            if getattr(field, 'choices', None) and not isinstance(field, forms.ModelChoiceField):
                field.choices = [(v, 'Not set' if v == '' else l) for v, l in field.choices]
            self.fields[name] = field
            self.initial[name] = getattr(record, name, model._meta.get_field(name).get_default()) if record else model._meta.get_field(name).get_default()
        if card == 'identity':
            self.fields['primary_identifier'] = Participant._meta.get_field('primary_identifier').formfield(label='Type')
            self.fields['primary_identifier'].choices = [('', 'Not set')] + [(value, label) for value, label in self.fields['primary_identifier'].choices if value]
            self.fields['primary_identifier'].widget = forms.RadioSelect(attrs={'class': 'btw-chip-choices'}, choices=self.fields['primary_identifier'].choices)
            self.fields['type_revision'] = forms.CharField(widget=forms.HiddenInput)
            self.initial.update(primary_identifier=person.primary_identifier, type_revision=type_revision(person))
        if card == 'preferences':
            self.fields['employment_interest'].choices = [('', 'Not set'),('seeking','Yes'),('open','Maybe'),('not_seeking','No')]
            self.fields['travel'] = forms.ChoiceField(required=False, label='Willing to travel', choices=[('', 'Not set'), ('yes', 'Yes'), ('maybe', 'Maybe'), ('no', 'No')])
            state = IntakeFieldState.objects.filter(participant=person, destination='travel_willingness').first()
            self.initial['travel'] = effective_travel(record, state) or ''
            self.fields['travel_revision'] = forms.CharField(widget=forms.HiddenInput)
            self.initial['travel_revision'] = state.updated_at.isoformat() if state else 'none'
            from apps.recruiting.access import fresh_staff as recruiting_staff
            try:
                recruiting_staff(actor)
            except PermissionDenied:
                pass
            else:
                from apps.recruiting.preferences import initial_preferences
                from apps.recruiting.forms import PreferencesForm
                initial = initial_preferences(person)
                preferences = PreferencesForm(initial=initial)
                for key in ('current_employment_status', 'employment_classification', 'availability_classification', 'willing_to_relocate'):
                    self.fields[key] = preferences.fields[key]
                    self.initial[key] = initial[key]
                    if key == 'employment_classification':
                        self.fields[key].label = 'Employment type'
                        self.fields[key].help_text = ''
                    if hasattr(self.fields[key], 'choices'):
                        self.fields[key].choices = [(v, 'Not set' if str(l).lower() in ('unknown', 'not recorded', '---------') else l) for v, l in self.fields[key].choices]
                self.fields['recruiting_revision'] = forms.IntegerField(widget=forms.HiddenInput)
                self.initial['recruiting_revision'] = initial['revision']
                # Catalog classifications repeat the questions above; only show them when the catalog has options or a value is set.
                for key in ('employment_classification', 'availability_classification'):
                    if len(list(self.fields[key].choices)) <= 1 and not self.initial.get(key):
                        del self.fields[key]
            # Standard answers as tap-to-pick chips.
            labels = {'employment_interest': 'Seeking work', 'travel': 'Will travel', 'willing_to_relocate': 'Will relocate',
                      'current_employment_status': 'Working now', 'preferred_work_type': 'Work type wanted',
                      'availability_classification': 'Availability', 'employment_classification': 'Employment type'}
            for key, label in labels.items():
                if key in self.fields:
                    self.fields[key].label = label
            for key in ('employment_interest', 'travel', 'willing_to_relocate', 'current_employment_status', 'preferred_work_type'):
                if key in self.fields:
                    self.fields[key].widget = forms.RadioSelect(attrs={'class': 'btw-chip-choices'}, choices=self.fields[key].choices)
        if card == 'preferences':
            self.order_fields(['employment_interest','travel','willing_to_relocate','current_employment_status','preferred_work_type','availability_classification','employment_classification','career_interests','preferred_locations'])
        self.initial['revision'] = profile_revision(person)
        if card == 'preferences':
            primary=['employment_interest','travel','willing_to_relocate','current_employment_status']
            self.preference_primary=[self[name] for name in primary if name in self.fields]
            self.preference_additional=[field for field in self.visible_fields() if field.name not in primary]
            self.retained_employment=record.employment_status if record else ''
            self.retained_availability=record.work_availability if record else ''

    def clean_contact_email(self):
        from .normalization import normalize_email
        return normalize_email(self.cleaned_data['contact_email'])

    def clean_contact_phone(self):
        from .normalization import normalize_phone
        return normalize_phone(self.cleaned_data['contact_phone'])

    def clean(self):
        data=super().clean()
        if 'current_employment_status' not in self.data:
            data.pop('current_employment_status',None)
        return data


@transaction.atomic
@serialize_identity_write
def save_card(*, actor, participant_id, card, data):
    actor = fresh_staff(actor)
    require_people(actor, 'participants.change_participant')
    if card not in CARDS:
        raise ValidationError('Unknown profile card.')
    person = lock_person(participant_id)
    career = ParticipantCareerProfile.objects.select_for_update().filter(participant=person).first()
    if data['revision'] != profile_revision(person):
        raise ValidationError('This profile changed while you were editing. Reload before saving.')
    if card == 'preferences':
        state = IntakeFieldState.objects.select_for_update().filter(participant=person, destination='travel_willingness').first()
        if data['travel_revision'] != (state.updated_at.isoformat() if state else 'none'):
            raise ValidationError('These preferences changed while you were editing. Reload before saving.')
    title, model, fields = CARDS[card]
    record = person if model is Participant else career
    before = snapshot(record, fields) if record else {}
    if card == 'preferences' and 'recruiting_revision' in data:
        from apps.recruiting.access import fresh_staff as recruiting_staff
        from apps.recruiting.models import RecruitingProfile
        from apps.recruiting.foundation import save_preferences
        recruiting_staff(actor)
        recruiting = RecruitingProfile.objects.select_for_update().filter(participant=person).first()
        if data['recruiting_revision'] != (recruiting.revision if recruiting else 0):
            raise ValidationError('These recruiting preferences changed. Reload before saving.')
        class_fields = [key for key in ('current_employment_status', 'employment_classification', 'availability_classification', 'willing_to_relocate') if key in data]
        from apps.recruiting.preferences import initial_preferences
        initial=initial_preferences(person)
        def previous(key):
            if key=='current_employment_status':return initial[key]
            if key=='willing_to_relocate':return {'yes':True,'maybe':'maybe','no':False}.get(initial[key])
            return getattr(recruiting,key,None)
        if any(data[key] != previous(key) for key in class_fields):
            save_preferences(actor=actor, participant_id=person.pk, data={key: data[key] for key in class_fields},
                expected_revision=data['recruiting_revision'], why='Staff updated Employment & preferences')
    changed = {key: data[key] for key in fields if not record or data[key] != getattr(record, key)}
    type_changed = card == 'identity' and data['primary_identifier'] != person.primary_identifier
    # Type has its own intake-ownership revision, checked before any write.
    if card == 'identity' and data['type_revision'] != type_revision(person):
        raise ValidationError('This type changed while you were editing. Reload before saving.')
    if type_changed:
        person = save_identifier(actor=actor, participant_id=person.pk,
            value=data['primary_identifier'], expected_revision=data['type_revision'], reason='Profile card: Type updated by staff')
    if changed:
        person = update_participant_profile(participant=person, updates=changed)
    travel_before = travel_after = None
    if card == 'preferences':
        state = IntakeFieldState.objects.select_for_update().filter(participant=person, destination='travel_willingness').first()
        career = ParticipantCareerProfile.objects.select_for_update().filter(participant=person).first()
        travel_before = effective_travel(career, state) or ''
        travel_after = data['travel']
        if travel_before != travel_after:
            career, _ = ParticipantCareerProfile.objects.get_or_create(participant=person)
            from .travel_state import set_current_travel
            set_current_travel(career, travel_after or None)
    current = person if model is Participant else ParticipantCareerProfile.objects.filter(participant=person).first()
    if changed or type_changed or travel_before != travel_after:
        audit(actor, 'profile.card_updated', person, before={**before, **({'travel': travel_before} if card == 'preferences' else {})},
            after={**(snapshot(current, fields) if current else {}), **({'travel': travel_after} if card == 'preferences' else {}),
                   'card': card, 'changed_fields': sorted([*changed, *(['primary_identifier'] if type_changed else []), *(['travel'] if travel_before != travel_after else [])])},
            reason='Staff updated ' + title)
    return person


@workspace
@require_http_methods(['GET', 'POST'])
def edit(request, participant_id, card):
    if card not in CARDS:
        raise Http404
    request.user = fresh_staff(request.user)
    require_people(request.user, 'participants.change_participant')
    with transaction.atomic():
        person = person_for(participant_id, editing=True)
        Participant.objects.select_for_update().get(pk=person.pk)
        person = person_for(participant_id, editing=True)
        form = ProfileCardForm(request.POST if request.method == 'POST' else None, card=card, person=person, actor=request.user)
    conflict = False
    if request.method == 'POST' and form.is_valid():
        if set(request.POST) - set(form.fields) - {'csrfmiddlewaretoken', 'return_to'}:
            form.add_error(None, 'Only fields in this card may be submitted.')
        else:
            try:
                save_card(actor=request.user, participant_id=person.pk, card=card, data=form.cleaned_data)
            except ValidationError as exc:
                if hasattr(exc, 'message_dict'):
                    for key, errors in exc.message_dict.items():
                        form.add_error(key if key in form.fields else None, errors)
                else:
                    form.add_error(None, exc)
                conflict = any('Reload' in message for message in exc.messages)
            else:
                messages.success(request, CARDS[card][0] + ' saved.')
                return people_saved(request, 'participants:staff-profile', participant_id=person.pk)
    return editor_response(request, template='participants/compact_form.html', panel='internal/ui/record_form_panel.html',
        context=context(request, participant=person, form=form, page_title='Edit ' + CARDS[card][0],
            back=people_return(request, person, reverse('participants:staff-profile', args=[person.pk])), save_label='Save changes', fields_template={'preferences': 'participants/preference_card_fields.html', 'identity': 'participants/identity_card_fields.html'}.get(card, 'internal/ui/section_fields.html'),
            cautious_save=True, editor_return_label='profile', save_conflict=conflict, full_page_editor=request.headers.get('X-BTW-Editor') != '1'))
