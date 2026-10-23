"""A distinct participant boundary; never infer the target person from a URL or POST."""
from functools import wraps
from pathlib import Path
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.views.decorators.cache import never_cache
from apps.events.models import EventRegistration, QrTicket
from .models import (ParticipantCareerProfile, ParticipantDocument, ParticipantDocumentAudit,
                     PortalInvitation, ParticipantEventInterest, ConsentAcceptance)
from .forms import SetPasswordForm, PasswordResetRequestForm
from .portal_forms import PortalProfileForm, PortalCareerForm, PortalDocumentForm
from .portal_accounts import (read_invitation, activate, throttle, queue_invitation,
                              process_deliveries, active_account)
from .portal_services import (own_account, profile_revision, save_profile, visible_documents,
    document_manageable, upload_document, archive_document, public_events, contract_hash,
    event_contract, previous_event_answers, current_registration, record_interest, register_for_event)
from .identity import identity_members


def participant_view(view):
    @never_cache
    @login_required(login_url='participants:login')
    @wraps(view)
    def secured(request, *args, **kwargs):
        request.portal_account = own_account(request.user)
        response = view(request, *args, **kwargs)
        # Keep same-origin form provenance for Django CSRF checks while
        # preventing private portal paths from leaking to other origins.
        response['Referrer-Policy'] = 'same-origin'
        return response
    return secured


@participant_view
@require_http_methods(['GET', 'POST'])
def profile(request):
    account = request.portal_account
    person = account.participant
    career = ParticipantCareerProfile.objects.filter(participant=person).first() or ParticipantCareerProfile(participant=person)
    revision = profile_revision(person, career)
    from .intake_workspace import workspace_intake_context
    intake_context = workspace_intake_context(request.user, person, identity_members(person.pk), career)
    form = PortalProfileForm(request.POST if request.method == 'POST' else None,
                             instance=person, initial={'revision': revision})
    career_initial = {'career_interests': career.career_interests or intake_context['current_interests']}
    if career.willing_to_travel is None and intake_context['current_travel'] in ['Yes', 'No']:
        career_initial['willing_to_travel'] = intake_context['current_travel'] == 'Yes'
    career_form = PortalCareerForm(request.POST if request.method == 'POST' else None, instance=career, initial=career_initial)
    if request.method == 'POST' and form.is_valid() and career_form.is_valid():
        try:
            fields = set(form.Meta.fields) | set(career_form.Meta.fields)
            values = {key: form.cleaned_data[key] if key in form.Meta.fields else career_form.cleaned_data[key] for key in fields}
            save_profile(request.user, values, form.cleaned_data['revision'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Your profile is up to date.')
            return redirect('participants:profile')
    documents = list(visible_documents(account.participant))
    for document in documents:
        document.can_manage = document_manageable(document, request.user)
    from .models import ParticipantQualification
    qualifications = ParticipantQualification.objects.filter(participant_id__in=identity_members(account.participant_id), is_active=True).select_related('catalog_entry')
    # Reuse evidence-aware status, exposing none of its internal reasons or reviewer data.
    from .verification_services import qualification_verification_summary
    for qualification in qualifications:
        qualification.portal_status = qualification_verification_summary(qualification)['label']
    return render(request, 'participants/portal/profile.html', {'account': account, 'form': form,
        'career_form': career_form, 'documents': documents, 'qualifications': qualifications, 'section': 'profile',
        'intake_rows': intake_context['intake_profile_rows'], 'hands_on_entries': intake_context['hands_on_entries']})


@participant_view
@require_http_methods(['GET', 'POST'])
def document_upload(request):
    form = PortalDocumentForm(request.POST if request.method == 'POST' else None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            upload_document(request.user, form)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'File uploaded. Authorized BTW staff can review it.')
            return redirect('participants:profile')
    return render(request, 'participants/portal/form.html', {'title': 'Add a supporting document', 'form': form,
        'multipart': True, 'section': 'profile', 'submit_label': 'Upload document'})


@participant_view
@require_POST
def document_archive(request, document_uuid):
    try:
        archive_document(request.user, document_uuid)
    except ParticipantDocument.DoesNotExist:
        raise Http404
    messages.success(request, 'File archived. Its history is retained for BTW staff.')
    return redirect('participants:profile')


@participant_view
@require_GET
def document_download(request, document_uuid):
    document = get_object_or_404(visible_documents(request.portal_account.participant), uuid=document_uuid)
    try:
        handle = document.file.open('rb')
    except (OSError, ValueError):
        raise Http404('This file is temporarily unavailable. Please contact BTW.')
    ParticipantDocumentAudit.objects.create(document=document, actor=request.user, action='downloaded', reason='Participant self-service download')
    response = FileResponse(handle, as_attachment=True, filename=Path(document.original_filename).name,
                            content_type='application/octet-stream')
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@never_cache
@require_http_methods(['GET', 'POST'])
def activate_account(request, token):
    invitation = read_invitation(token)
    if not invitation:
        return render(request, 'participants/portal/message.html', {'title': 'Let’s get you a fresh link',
            'message': 'This invitation is expired, already used, or needs staff review. Request a new link below.',
            'recovery': True}, status=400)
    form = SetPasswordForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        if not throttle('activation', invitation.pk, limit=10):
            form.add_error(None, 'Please wait 15 minutes before trying again.')
        else:
            try:
                activate(token, form.cleaned_data['password1'])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, 'Your participant portal is ready. Sign in to get started.')
                return redirect('participants:login')
    response = render(request, 'participants/portal/form.html', {'title': 'Your next opportunity starts here',
        'intro': 'Set a password to access your existing Built to Work profile.', 'form': form,
        'submit_label': 'Activate my portal'})
    response['Referrer-Policy'] = 'same-origin'
    return response


@never_cache
@require_http_methods(['GET', 'POST'])
def recover_invitation(request):
    form = PasswordResetRequestForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email'].strip().lower()
        if throttle('invite-recovery', email, limit=3) and throttle('invite-ip', request.META.get('REMOTE_ADDR', ''), limit=12):
            # No contact-based creation or claiming here: only resend an already intake-bound invitation.
            invitation = PortalInvitation.objects.select_related('participant').filter(email__iexact=email, activated_at__isnull=True).first()
            if invitation:
                queue_invitation(invitation.participant, resend=True)
            else:
                from .models import Participant
                candidates = Participant.objects.filter(contact_email__iexact=email, kind='person', archived_at__isnull=True)
                if candidates.count() == 1:
                    queue_invitation(candidates.get(), resend=True)
        return render(request, 'participants/portal/message.html', {'title': 'Request received',
            'message': 'If your completed intake and communication preference allow an invitation, it has been queued. Already activated? Use password recovery. If you share an email or need help, contact BTW staff.'})
    return render(request, 'participants/portal/form.html', {'title': 'Need an invitation?', 'form': form,
        'intro': 'Complete your BTW intake first. You can request a fresh activation link here.', 'submit_label': 'Request a fresh link'})


@participant_view
@require_GET
def events(request):
    items = list(public_events())
    for event in items:
        event.my_registration = current_registration(request.portal_account.participant, event)
        event.interested = ParticipantEventInterest.objects.filter(participant=request.portal_account.participant, event=event).exists()
    return render(request, 'participants/portal/events.html', {'events': items, 'section': 'events'})


class EventForm(forms.Form):
    contract = forms.CharField(widget=forms.HiddenInput)
    def __init__(self, *args, person, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['contract'] = signing.dumps({'event': str(event.pk), 'person': person.pk,
            'hash': contract_hash(event)}, salt='portal.event.v1')
        prior = previous_event_answers(person, event)
        if event.group_mode != 'disabled':
            self.fields['group'] = forms.ChoiceField(label=event.group_label,
                required=event.group_mode == 'required', choices=[('', 'Choose a group')] +
                [(str(g.pk), g.name) for g in event.groups.filter(is_active=True)])
        for q in event_contract(event)['questions']:
            if prior.get(q['key'], {}).get('answer'):
                continue
            if q['field_type'] in ['single_choice', 'yes_no']:
                options = [{'value': 'yes', 'label': 'Yes'}, {'value': 'no', 'label': 'No'}] if q['field_type'] == 'yes_no' else q['options']
                choices = [('', 'Choose an answer')] + [(str(o.get('value', '')), str(o.get('label', ''))) if isinstance(o, dict) else (str(o), str(o)) for o in options]
                field = forms.ChoiceField(choices=choices, label=q['label'], required=q['is_required'])
            else:
                field = forms.CharField(label=q['label'], required=q['is_required'],
                    max_length=3000 if q['field_type'] == 'long_text' else 500,
                    widget=forms.Textarea(attrs={'rows': 3}) if q['field_type'] == 'long_text' else forms.TextInput)
            self.fields['q_' + q['key']] = field
        self.needs_consent = not ConsentAcceptance.objects.filter(participant_id__in=identity_members(person.pk),
            submission__event=event, document=event.public_consent_document, submission__status='submitted').exists()
        if self.needs_consent:
            self.fields['consent'] = forms.BooleanField(label='I have read and agree to the event consent shown below.')


@participant_view
@require_http_methods(['GET', 'POST'])
def event_detail(request, event_id):
    event = get_object_or_404(public_events(), pk=event_id)
    person = request.portal_account.participant
    if request.method == 'POST' and request.POST.get('action') == 'interest':
        record_interest(request.user, event)
        messages.success(request, 'Interest recorded. Complete the event requirements below to confirm registration.')
        return redirect('participants:portal-event', event_id=event.pk)
    registration = current_registration(person, event)
    form = EventForm(request.POST if request.method == 'POST' else None, person=person, event=event)
    if request.method == 'POST' and form.is_valid():
        try:
            stamp = signing.loads(form.cleaned_data['contract'], salt='portal.event.v1', max_age=3600)
            if stamp['event'] != str(event.pk) or stamp['person'] != person.pk:
                raise ValidationError('Reload the event before registering.')
            registration, _ = register_for_event(request.user, event, form.cleaned_data, stamp['hash'])
        except (signing.BadSignature, KeyError, ValidationError) as exc:
            form.add_error(None, exc if isinstance(exc, ValidationError) else 'This form expired. Reload it to continue.')
        else:
            messages.success(request, 'Registration confirmed. Your event ticket is ready.')
            return redirect('participants:portal-activity')
    doc = event.public_consent_document
    legal_ready = bool(doc and doc.is_approved and doc.body.strip() and (not doc.effective_at or doc.effective_at <= timezone.now()))
    return render(request, 'participants/portal/event.html', {'event': event, 'form': form,
        'registration': registration, 'consent_document': doc if legal_ready else None,
        'registration_available': event.public_registration_status == 'open' and event.registration_is_open and legal_ready,
        'section': 'events'})


@participant_view
@require_GET
def activity(request):
    person = request.portal_account.participant
    registrations = list(EventRegistration.objects.filter(participant_id__in=identity_members(person.pk)).select_related('event').order_by('-event__start_at'))
    for registration in registrations:
        registration.portal_ticket = registration.tickets.filter(is_current=True, revoked_at__isnull=True,
            expires_at__gte=timezone.now()).first() if registration.status == 'active' else None
        registration.is_past = registration.event.end_at < timezone.now()
    confirmed_ids = {r.event_id for r in registrations if r.status == 'active'}
    interests = ParticipantEventInterest.objects.filter(participant=person).exclude(event_id__in=confirmed_ids).select_related('event')
    from apps.event_reports.activity import activity_sources
    sources = activity_sources()
    sessions = sources['sessions'].filter(participant_id__in=identity_members(person.pk)).select_related('event').order_by('-started_at')[:100]
    return render(request, 'participants/portal/activity.html', {'registrations': registrations, 'interests': interests,
        'recorded_sessions': sessions, 'section': 'activity'})


@participant_view
@require_GET
def ticket(request, ticket_id):
    owned = QrTicket.objects.filter(registration__participant_id__in=identity_members(request.portal_account.participant_id),
        registration__status='active', is_current=True, revoked_at__isnull=True, expires_at__gte=timezone.now())
    qr = get_object_or_404(owned.select_related('registration__participant', 'registration__event'), pk=ticket_id)
    from apps.events.ticket_renderer import ticket_png_bytes
    response = HttpResponse(ticket_png_bytes(qr), content_type='image/png')
    response['Content-Disposition'] = 'inline; filename="built-to-work-ticket.png"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@participant_view
@require_GET
def account(request):
    return render(request, 'participants/portal/account.html', {'account': request.portal_account, 'section': 'account'})


@never_cache
@login_required(login_url='admin:login')
@require_http_methods(['GET', 'POST'])
def staff_invitations(request):
    if not (request.user.is_active and request.user.is_staff and request.user.has_perms([
            'participants.view_participant', 'participants.manage_portal_invitations'])):
        raise PermissionDenied
    if request.method == 'POST':
        from .models import Participant
        if request.POST.get('participant'):
            person = get_object_or_404(Participant, pk=request.POST['participant'], kind='person', archived_at__isnull=True)
        else:
            invitation = get_object_or_404(PortalInvitation.objects.select_related('participant'), pk=request.POST.get('invitation'))
            person = invitation.participant
        queued = queue_invitation(person, actor=request.user, resend=True)
        messages.success(request, 'An eligible resend is queued. Delivery status below shows configuration or review holds.' if queued else 'No invitation sent. Check identity, communication preference, or existing account.')
        return redirect('participants:portal-invitations')
    from apps.internal_ui.people_editors import people_render
    from .models import Participant
    selected = Participant.objects.filter(pk=request.GET.get('participant')).first() if request.GET.get('participant', '').isdigit() else None
    return people_render(request, 'participants/portal/staff_invitations.html', {
        'invitations': PortalInvitation.objects.select_related('participant').prefetch_related('deliveries').order_by('-created_at')[:200],
        'selected': selected, 'page_title': 'Participant portal invitations'})
