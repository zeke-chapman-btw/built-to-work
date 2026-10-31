"""Small admin-only synthetic context. Django's authenticated user never changes."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, timedelta
from types import SimpleNamespace
import hashlib
import os
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from apps.core.audit import record_audit
from .models import (PortalPreviewFixture, Participant, ParticipantCareerProfile,
    ParticipantAccount, ParticipantQualification, RegistrationSubmission, ConsentDocumentVersion,
    ConsentAcceptance)

SESSION_KEY = 'btw_participant_portal_preview'
_context = ContextVar('btw_admin_participant_preview', default=None)


def enabled():
    # The existing gate alone is not evidence that a service is non-production.
    # Pin interactive fixtures to the owner-approved Preview service or isolated
    # test settings; migrations never create fixtures in any environment.
    service = os.environ.get('RENDER_SERVICE_ID')
    # Django's override_settings holder intentionally has SETTINGS_MODULE=None.
    module = settings.SETTINGS_MODULE or os.environ.get('DJANGO_SETTINGS_MODULE')
    destination = service == 'srv-db8n044s728c73aaet00' or (
        not service and module == 'config.settings.test')
    return bool(getattr(settings, 'BTW_PREVIEW_GATE_ENABLED', False) and destination)


def require_admin(user):
    if not (getattr(user, 'is_authenticated', False) and user.is_active and user.is_staff and user.is_superuser):
        raise PermissionDenied('Portal Preview is available to BTW administrators only.')


def _audit(user, action, fixture):
    record_audit(actor=user, action='portal.preview_' + action, instance=fixture,
        source='admin_portal_preview', new_data={'synthetic': True})


def validate_fixture(fixture, user):
    require_admin(user)
    if not enabled() or fixture.administrator_id != user.pk:
        raise PermissionDenied('This preview is unavailable.')
    from .identity import identity_members
    person = fixture.participant
    if (person.kind != 'person' or person.archived_at or identity_members(person.pk) != {person.pk}
            or person.first_name != 'SYNTHETIC' or person.last_name != 'Admin Portal Preview'
            or person.contact_email != f'portal-preview-{user.pk}@example.test' or person.contact_phone
            or ParticipantAccount.objects.filter(participant=person).exists()
            or not fixture.event.name.startswith('SYNTHETIC — Admin Portal Preview')
            or fixture.event.public_slugs.exists()):
        raise PermissionDenied('The synthetic fixture needs administrator review.')
    return fixture


@transaction.atomic
def fixture_for(user):
    require_admin(user)
    if not enabled():
        raise PermissionDenied('Interactive Portal Preview is disabled outside the preview environment.')
    # The administrator row serializes first-use creation on PostgreSQL.
    get_user_model().objects.select_for_update().get(pk=user.pk)
    fixture = PortalPreviewFixture.objects.select_related('participant', 'event').filter(administrator=user).first()
    if fixture:
        return validate_fixture(fixture, user)
    from apps.events.models import Event, EventRegistrationQuestion
    from .intake_lifecycle import create_form, publish, revision
    person = Participant.objects.create(first_name='SYNTHETIC', last_name='Admin Portal Preview',
        contact_email=f'portal-preview-{user.pk}@example.test', date_of_birth=date(2000, 1, 1),
        city='Example City', state='GA', postal_code='00123')
    ParticipantCareerProfile.objects.create(participant=person, career_interests='Explore the skilled trades')
    body = 'SYNTHETIC PREVIEW ONLY. This is a nonbinding test notice, not a legal agreement.'
    document = ConsentDocumentVersion.objects.create(key='admin-portal-preview-' + str(user.pk),
        version=uuid.uuid4().hex, title='Synthetic preview notice', body=body,
        content_hash=hashlib.sha256(body.encode()).hexdigest(), is_approved=True)
    draft = create_form(actor=user, name='SYNTHETIC Admin Portal Preview')
    version = publish(actor=user, version_id=draft.pk, expected_revision=revision(draft))
    # A private, explicitly synthetic fixture, with no public intake assignment.
    submission = RegistrationSubmission.objects.create(participant=person, form_version=version,
        source='admin_portal_preview', status='submitted', submitted_at=timezone.now(),
        intake_operation_id=uuid.uuid4(), intake_metadata={'synthetic_admin_preview': True},
        answers={'first_name':'SYNTHETIC','last_name':'Admin Portal Preview','dob':'2000-01-01',
            'email':person.contact_email,'phone':'','city':'Example City','state':'GA','postal_code':'00123',
            'current_situation':'unemployed','career_timeline':'exploring',
            'opportunity_types':['exploration'],'travel_willingness':'no','updates_preference':'no'},
        answer_snapshot={'synthetic_admin_preview': True})
    ConsentAcceptance.objects.create(participant=person, submission=submission, document=document,
        metadata={'synthetic_admin_preview': True, 'nonbinding_notice': True})
    event = Event.objects.create(name='SYNTHETIC — Admin Portal Preview',
        start_at=timezone.now() + timedelta(days=90), end_at=timezone.now() + timedelta(days=90, hours=4),
        timezone_name='America/New_York', status='upcoming', public_registration_status='open',
        public_consent_document=document, location_name='Synthetic preview venue', city='Example City', state='GA')
    EventRegistrationQuestion.objects.create(event=event, key='preview_goal', label='What would you like to explore?',
        field_type='short_text', is_required=True, is_active=True, position=0)
    ParticipantQualification.objects.create(participant=person, custom_name='SYNTHETIC Trade Safety Certificate',
        is_self_reported=True)
    fixture = PortalPreviewFixture.objects.create(administrator=user, participant=person, event=event)
    _audit(user, 'fixture_created', fixture)
    return fixture


@contextmanager
def participant_context(request):
    selected = request.session.get(SESSION_KEY)
    if not selected:
        yield
        return
    require_admin(request.user)
    if not isinstance(selected, dict) or selected.get('administrator') != str(request.user.pk):
        raise PermissionDenied('Invalid preview session.')
    try:
        age = timezone.now().timestamp() - float(selected['started'])
        fixture = PortalPreviewFixture.objects.select_related('participant', 'event').get(pk=selected['fixture'])
    except (KeyError, ValueError, TypeError, ValidationError, PortalPreviewFixture.DoesNotExist):
        raise PermissionDenied('Invalid preview session.')
    if not 0 <= age <= 3600:
        raise PermissionDenied('This preview expired. Return to Settings and open it again.')
    validate_fixture(fixture, request.user)
    request.portal_preview = fixture
    token = _context.set(fixture)
    try:
        yield
    finally:
        _context.reset(token)


def current_fixture(user=None):
    fixture = _context.get()
    if fixture is not None and user is not None:
        validate_fixture(fixture, user)
    return fixture


def preview_account(user):
    fixture = current_fixture(user)
    return SimpleNamespace(participant=fixture.participant, participant_id=fixture.participant_id,
        user=user, login_email=fixture.participant.contact_email, status='admin_preview') if fixture else None


def guard_profile_updates(user, updates):
    fixture = current_fixture(user)
    if fixture:
        fixed = {'first_name':'SYNTHETIC', 'last_name':'Admin Portal Preview',
            'contact_email':fixture.participant.contact_email, 'contact_phone':''}
        if any(key in updates and updates[key] != value for key, value in fixed.items()):
            raise ValidationError('Synthetic identity and contact fields stay fixed in Portal Preview.')


def guard_registration(event, participant, actor):
    """Both directions: preview people cannot enter real events or vice versa."""
    from django.db.models import Q
    fixture = PortalPreviewFixture.objects.filter(Q(event=event) | Q(participant=participant)).first()
    if fixture:
        current = current_fixture(actor)
        if (not getattr(actor, 'pk', None) or fixture.administrator_id != actor.pk
                or not current or current.pk != fixture.pk or fixture.event_id != event.pk
                or fixture.participant_id != participant.pk):
            raise ValidationError('Synthetic preview registrations are restricted to their own preview event.')
        return fixture


def block_account_changes(request):
    if request.session.get(SESSION_KEY):
        raise PermissionDenied('Account changes and communications are unavailable in Admin Preview. Return to Admin first.')


@never_cache
@login_required(login_url='admin:login')
@require_GET
def index(request):
    require_admin(request.user)
    from apps.internal_ui.views import _staff_context
    return render(request, 'internal/portal_preview.html', _staff_context(request,
        page_title='Portal Preview', preview_enabled=enabled()))


@never_cache
@login_required(login_url='admin:login')
@require_POST
def enter(request):
    fixture = fixture_for(request.user)
    from apps.customers.portal_preview import SESSION_KEY as CUSTOMER_PREVIEW_KEY
    request.session.pop(CUSTOMER_PREVIEW_KEY, None)
    request.session.pop('customer_organization', None)
    request.session[SESSION_KEY] = {'fixture':str(fixture.pk), 'administrator':str(request.user.pk),
        'started':timezone.now().timestamp()}
    _audit(request.user, 'entered', fixture)
    return redirect('participants:profile')


@never_cache
@login_required(login_url='admin:login')
@require_POST
def exit(request):
    require_admin(request.user)
    selected = request.session.pop(SESSION_KEY, None)
    fixture = PortalPreviewFixture.objects.filter(administrator=request.user).first()
    if selected and fixture:
        _audit(request.user, 'exited', fixture)
    return redirect('internal:portal-preview')
