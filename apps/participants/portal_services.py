"""Self-service writes use existing profile, document, consent and event services."""
import hashlib
import json
from pathlib import Path
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from apps.core.audit import record_audit
from apps.events.models import Event, EventRegistration, QrTicket
from apps.events.services import register_participant
from .models import (Participant, ParticipantCareerProfile, ParticipantDocument, ParticipantDocumentAudit,
                     ParticipantEventInterest, RegistrationSubmission, ConsentAcceptance)
from .identity import PROFILE_FIELDS, CAREER_FIELDS, update_participant_profile, identity_members
from .portal_accounts import active_account, intake_for


def own_account(user):
    from .portal_preview import preview_account
    preview = preview_account(user)
    if preview:
        return preview
    account = active_account(user)
    if account is None:
        raise PermissionDenied('Use your active participant account to access the portal.')
    return account


def profile_revision(person, career):
    return hashlib.sha256((person.updated_at.isoformat() + '|' + (career.updated_at.isoformat() if career.pk else '')).encode()).hexdigest()


@transaction.atomic
def save_profile(user, updates, revision):
    account = own_account(user)
    from .portal_preview import guard_profile_updates
    guard_profile_updates(user, updates)
    person = Participant.objects.select_for_update().get(pk=account.participant_id)
    career = ParticipantCareerProfile.objects.select_for_update().filter(participant=person).first() or ParticipantCareerProfile(participant=person)
    if profile_revision(person, career) != revision:
        raise ValidationError('Your profile changed while this page was open. Reload before saving.')
    if set(updates) - PROFILE_FIELDS - CAREER_FIELDS:
        raise PermissionDenied
    before = {k: str(getattr(person if k in PROFILE_FIELDS else career, k)) for k in updates}
    saved = update_participant_profile(participant=person, updates=updates)
    record_audit(actor=user, action='portal.profile_updated', instance=saved, source='participant_portal',
        old_data=before, new_data={k: str(v) for k, v in updates.items()})
    return saved


def visible_documents(person):
    # Existing staff attachments may contain private notes: visibility is explicit and defaults closed.
    from django.db.models import Q
    return ParticipantDocument.objects.filter(participant_id__in=identity_members(person.pk)).filter(
        Q(upload_source='participant') | Q(participant_visible=True)).select_related('category')


def document_manageable(document, user):
    return (document.upload_source == 'participant' and document.uploaded_by_id == user.pk
        and document.state == 'active' and not document.verification_records.exists()
        and not document.qualification_evidence_records.exists() and not document.qualification_links.exists())


def upload_document(user, form):
    account = own_account(user)
    document = ParticipantDocument(participant=account.participant, category=form.cleaned_data['category'],
        original_filename=form.filename, content_type=form.content_type,
        file_size=form.cleaned_data['file'].size, uploaded_by=user, upload_source='participant',
        participant_visible=True, description=form.cleaned_data['description'],
        issue_date=form.cleaned_data['issue_date'], expiration_date=form.cleaned_data['expiration_date'])
    from .document_views import _safe_upload_name
    try:
        with transaction.atomic():
            # Bound quota protects shared preview storage without changing staff upload policy.
            Participant.objects.select_for_update().get(pk=account.participant_id)
            if ParticipantDocument.objects.filter(participant=account.participant, upload_source='participant', state='active').count() >= 25:
                raise ValidationError('You have reached 25 current files. Contact BTW for help.')
            upload = _safe_upload_name(form.cleaned_data['file'], form.content_type)
            document.file.save(upload.name, upload, save=False)
            document.full_clean()
            document.save()
            ParticipantDocumentAudit.objects.create(document=document, actor=user, action='uploaded', reason='Participant self-service upload')
    except Exception:
        if document.file.name:
            document.file.storage.delete(document.file.name)
        raise
    return document


@transaction.atomic
def archive_document(user, document_id):
    account = own_account(user)
    document = ParticipantDocument.objects.select_for_update().get(uuid=document_id, participant=account.participant)
    if not document_manageable(document, user):
        raise PermissionDenied('Staff-reviewed evidence is retained. Upload a new version or contact BTW.')
    document.state = 'archived'
    document.save(update_fields=['state', 'updated_at'])
    ParticipantDocumentAudit.objects.create(document=document, actor=user, action='archived', reason='Participant self-service archive; file retained')
    return document


def public_events():
    from .portal_preview import current_fixture
    from .models import PortalPreviewFixture
    fixture = current_fixture()
    if fixture:
        return Event.objects.filter(pk=fixture.event_id, status='upcoming', end_at__gte=timezone.now()).select_related('public_consent_document')
    # Slug publication is the existing explicit public visibility boundary. Never expose staff descriptions.
    return Event.objects.exclude(pk__in=PortalPreviewFixture.objects.values('event_id')).filter(status='upcoming', end_at__gte=timezone.now(),
        public_registration_status__in=['open', 'paused', 'closed'],
        public_slugs__is_current=True).distinct().select_related('public_consent_document').order_by('start_at')


def event_contract(event):
    doc = event.public_consent_document
    questions = list(event.registration_questions.filter(is_active=True).order_by('position', 'id').values('key', 'label', 'field_type', 'options', 'is_required'))
    return {'event': str(event.pk), 'document': doc.pk if doc else None,
        'hash': doc.content_hash if doc else '', 'text': hashlib.sha256(doc.body.encode()).hexdigest() if doc else '',
        'questions': questions, 'group_mode': event.group_mode,
        'groups': list(event.groups.filter(is_active=True).order_by('pk').values_list('pk', flat=True))}


def contract_hash(event):
    return hashlib.sha256(json.dumps(event_contract(event), sort_keys=True, default=str).encode()).hexdigest()


def previous_event_answers(person, event):
    prior = EventRegistration.objects.filter(participant_id__in=identity_members(person.pk), event=event).order_by('-registered_at').first()
    return dict(prior.custom_answers) if prior else {}


def current_registration(person, event):
    return EventRegistration.objects.filter(participant_id__in=identity_members(person.pk), event=event, status='active').first()


@transaction.atomic
def record_interest(user, event):
    account = own_account(user)
    Event.objects.select_for_update().get(pk=event.pk)
    if not public_events().filter(pk=event.pk).exists():
        raise PermissionDenied
    interest, created = ParticipantEventInterest.objects.get_or_create(participant=account.participant, event=event)
    if created:
        record_audit(actor=user, action='portal.event_interest', instance=interest, source='participant_portal')
    return interest


@transaction.atomic
def register_for_event(user, event, values, expected_contract):
    account = own_account(user)
    event = Event.objects.select_for_update(of=('self',)).select_related('public_consent_document').get(pk=event.pk)
    # Recheck under lock, including the shared registration service's capacity/window rules.
    if not public_events().filter(pk=event.pk).exists():
        raise ValidationError('This event is no longer publicly available.')
    existing = current_registration(account.participant, event)
    if existing:
        return existing, False
    if contract_hash(event) != expected_contract:
        raise ValidationError('The event questions or consent changed. Reload and review them again.')
    from .intake_submission import _legal, _public_event, _event_extras
    _public_event(event)
    doc = event.public_consent_document
    _legal(doc)
    if not values.get('consent') and not ConsentAcceptance.objects.filter(
            participant_id__in=identity_members(account.participant_id), document=doc,
            submission__event=event, submission__status='submitted').exists():
        raise ValidationError('Review and accept the event consent to register.')
    source = intake_for(account.participant)
    if not source:
        raise ValidationError('Complete your BTW intake before registering for another event.')
    from types import SimpleNamespace
    definitions = [{'key': q['key'], 'label': q['label'], 'type': q['field_type'],
        'options': q['options'], 'required': q['is_required']} for q in event_contract(event)['questions']]
    supplied = {q['key']: values.get('q_' + q['key'], '') for q in definitions}
    prior = previous_event_answers(account.participant, event)
    supplied = {key: (value or prior.get(key, {}).get('answer', '')) for key, value in supplied.items()}
    group, answers, _, _ = _event_extras(SimpleNamespace(event=event, context={
        'event_questions': definitions, 'extras': {'group_id': values.get('group') or None, 'answers': supplied}}))
    registration, created = register_participant(event=event, participant=account.participant,
        actor=user, source='online', group=group, custom_answers=answers)
    if created:
        submission = RegistrationSubmission.objects.create(form_version=source.form_version,
            participant=account.participant, event=event, status='submitted', submitted_at=timezone.now(),
            source='participant_portal', answers={'event_answers': answers},
            answer_snapshot={'event_contract': event_contract(event), 'consent_accepted': True},
            idempotency_key='portal:' + str(registration.pk))
        ConsentAcceptance.objects.create(participant=account.participant, submission=submission,
            document=doc, metadata={'channel': 'participant_portal', 'content_hash': doc.content_hash,
                'registration_id': str(registration.pk)})
        record_audit(actor=user, action='portal.registration_confirmed', instance=registration,
                     source='participant_portal')
    return registration, created
