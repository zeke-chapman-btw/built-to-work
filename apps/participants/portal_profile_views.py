"""Section editors share the existing participant and Admin Preview boundary."""
import uuid
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .portal_views import participant_view
from .models import ParticipantCareerProfile, ParticipantQualification
from .workspace_models import ParticipantWorkExperience
from .portal_services import profile_revision, save_profile
from .portal_profile_forms import section_form, PERSONAL_FIELDS, CAREER_SECTIONS, WorkEntryForm, QualificationEntryForm
from .portal_profile_services import save_entry, archive_entry, can_edit_work, can_edit_qualification, qualification_section

SECTION_TITLES = {'personal': 'Personal & Contact Information', 'preferences': 'Career Interests & Employment Preferences',
                  'availability': 'Availability & Travel', 'skills-summary': 'Skills Summary', 'education-summary': 'Education & Training Summary'}
ENTRY_TITLES = {'work': 'Experience', 'skills': 'Skill', 'training': 'Education or Training', 'credentials': 'Credential'}
ANCHORS = {'skills-summary': 'skills', 'education-summary': 'training', 'preferences': 'preferences', 'availability': 'availability', 'personal': 'personal'}


def _home(section):
    return reverse('participants:profile') + '#' + ANCHORS.get(section, section)


def _editor(request, form, title, section, **extra):
    return render(request, 'participants/portal/profile_editor.html', {
        'title': title, 'form': form, 'section': 'profile', 'cancel_url': _home(section), **extra})


@participant_view
@require_http_methods(['GET', 'POST'])
def edit_section(request, section):
    if section not in SECTION_TITLES:
        raise Http404
    person = request.portal_account.participant
    career = ParticipantCareerProfile.objects.filter(participant=person).first() or ParticipantCareerProfile(participant=person)
    from .intake_workspace import workspace_intake_context
    from .identity import identity_members
    intake = workspace_intake_context(request.user, person, identity_members(person.pk), career)
    form = section_form(section, request.POST if request.method == 'POST' else None,
        person=person, career=career, revision=profile_revision(person, career),
        preview=bool(getattr(request, 'portal_preview', None)), current_interests=intake['current_interests'])
    if request.method == 'POST' and form.is_valid():
        fields = PERSONAL_FIELDS if section == 'personal' else CAREER_SECTIONS[section]
        try:
            save_profile(request.user, {k: form.cleaned_data[k] for k in fields}, form.cleaned_data['revision'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Your information is up to date.')
            return redirect(_home(section))
    return _editor(request, form, 'Edit ' + SECTION_TITLES[section], section,
        intro='Changes update your current BTW profile. Your original intake responses and staff verification history are retained.')


def _record(request, section, object_id):
    if section not in ENTRY_TITLES:
        raise Http404
    model = ParticipantWorkExperience if section == 'work' else ParticipantQualification
    if object_id is None:
        return None
    row = get_object_or_404(model, pk=object_id, participant_id=request.portal_account.participant_id)
    allowed = can_edit_work(row, request.user, request.portal_account.participant) if section == 'work' else (
        qualification_section(row) == section and can_edit_qualification(row, request.user, request.portal_account.participant))
    if not allowed:
        raise PermissionDenied('BTW-reviewed information is protected. Add a new entry or contact BTW for a correction.')
    return row


@participant_view
@require_http_methods(['GET', 'POST'])
def entry(request, section, object_id=None):
    row = _record(request, section, object_id)
    data = request.POST if request.method == 'POST' else None
    if section == 'work':
        instance = row or ParticipantWorkExperience(source='participant')
        form = WorkEntryForm(data, instance=instance, initial={
            'revision': row.updated_at.isoformat() if row else '',
            'request_key': row.source_entry_id if row and row.source_entry_id else uuid.uuid4()})
    else:
        form = QualificationEntryForm(data, section=section, record=row)
    if request.method == 'POST' and form.is_valid():
        try:
            save_entry(request.user, section, form.cleaned_data, object_id)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, ENTRY_TITLES[section] + (' updated.' if row else ' added.'))
            return redirect(_home(section))
    return _editor(request, form, ('Edit ' if row else 'Add ') + ENTRY_TITLES[section], section,
        intro='This is your self-reported information. BTW verification stays separate. Add creates another entry; Edit changes only this entry.')


@participant_view
@require_http_methods(['GET', 'POST'])
def archive(request, section, object_id):
    row = _record(request, section, object_id)
    if row.source != 'participant' or row.created_by_id != request.user.pk:
        raise PermissionDenied('Original intake entries are retained.')
    class ArchiveForm(forms.Form):
        revision = forms.CharField(widget=forms.HiddenInput)
    form = ArchiveForm(request.POST if request.method == 'POST' else None,
                       initial={'revision': row.updated_at.isoformat()})
    if request.method == 'POST' and form.is_valid():
        try:
            archive_entry(request.user, section, object_id, form.cleaned_data['revision'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Entry archived. Its history remains available to BTW staff.')
            return redirect(_home(section))
    return _editor(request, form, 'Archive ' + ENTRY_TITLES[section], section,
        intro='Archive this self-reported entry? It will leave your current profile, while its history is retained.', submit_label='Archive entry')
