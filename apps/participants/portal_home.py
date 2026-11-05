"""Small participant-owned Home projection; no new workflow or notification state."""
from django.urls import reverse
from django.utils import timezone
from apps.events.models import EventRegistration
from apps.participants.identity import identity_members
from apps.recruiting import jobs
from apps.recruiting.foundation import freshness
from apps.recruiting.models import ProfileUpdateRequest


def home_context(actor, account, *, preview=False):
    person = account.participant
    requests = ProfileUpdateRequest.objects.filter(participant_id__in=identity_members(person.pk),
        fulfilled_by__isnull=True).order_by('-created_at', '-pk')
    interests = list(jobs.own_interests(actor)[:5])
    registrations = list(EventRegistration.objects.filter(participant_id__in=identity_members(person.pk),
        status='active', event__end_at__gte=timezone.now()).select_related('event').order_by('event__start_at', 'pk')[:3])
    for registration in registrations:
        registration.portal_ticket = registration.tickets.filter(is_current=True, revoked_at__isnull=True,
            expires_at__gte=timezone.now()).first()
    pending = list(requests[:3])
    information = jobs.own_interests(actor).filter(active=True, status='information').first()
    confirmation = freshness(person)
    if preview:
        step = {'title': 'Explore your participant workspace', 'text': 'This is synthetic Administrator Preview. Recruiting submissions and preference confirmations require a real Participant account.',
            'label': 'Explore opportunities', 'url': reverse('participants:portal-jobs')}
    elif pending:
        step = {'title': 'BTW requested information', 'text': pending[0].message,
            'label': 'Review and update my information', 'url': reverse('recruiting:portal-preferences')}
    elif information:
        step = {'title': 'Review your job-interest next steps' if confirmation['needs_confirmation'] else 'Awaiting BTW review',
            'text': 'BTW needs more information before continuing its review.' if confirmation['needs_confirmation'] else 'Your current profile is confirmed. BTW reviews your interest separately; confirming your profile does not change its recruiting status.',
            'label': 'View my submitted interest', 'url': reverse('recruiting:portal-interest', args=[information.pk])}
    elif registrations:
        step = {'title': 'Your event registration is ready', 'text': 'Review your event details and ticket before you arrive.',
            'label': 'View my event activity', 'url': reverse('participants:portal-activity')}
    elif confirmation['needs_confirmation']:
        step = {'title': 'Keep your profile current', 'text': 'Review your preferences so BTW has the information you want to share.',
            'label': 'Review my information', 'url': reverse('recruiting:portal-preferences')}
    else:
        step = {'title': 'Find your next opportunity', 'text': 'Explore approved opportunities and let BTW know what interests you.',
            'label': 'Browse opportunities', 'url': reverse('participants:portal-jobs')}
    return {'next_step': step, 'update_requests': pending, 'interests': [jobs.interest_projection(r) for r in interests],
        'registrations': registrations, 'freshness': confirmation}
