"""Campaign connections that come from Campaign events.

A person is connected to a Campaign when they signed up for one of its events
(even if they never came), or did activity at the trailer during one of its
events. Online sign-ups through an event's public page are sign-ups too.
Voided sign-ups do not count.
"""
from django.db.models import Q


def campaign_event_ids(campaign_ids):
    from .models import Event
    return Event.objects.filter(purpose=Event.Purpose.CAMPAIGN, campaign_id__in=campaign_ids).values("pk")


def connected_participant_ids(campaign):
    from .models import EventRegistration
    from apps.stations.models import ExperienceSession
    events = campaign_event_ids([campaign.pk])
    people = set(EventRegistration.objects.filter(event_id__in=events).exclude(status="void").values_list("participant_id", flat=True))
    people |= set(ExperienceSession.objects.filter(event_id__in=events, participant__isnull=False).values_list("participant_id", flat=True))
    return people


def campaigns_for_participants(participant_ids):
    """Campaign ids reached through Campaign events for any of these people."""
    from .models import Event, EventRegistration
    from apps.stations.models import ExperienceSession
    campaign_events = Event.objects.filter(purpose=Event.Purpose.CAMPAIGN, campaign__isnull=False)
    by_registration = EventRegistration.objects.filter(participant_id__in=participant_ids, event__in=campaign_events).exclude(status="void").values_list("event__campaign_id", flat=True)
    by_activity = ExperienceSession.objects.filter(participant_id__in=participant_ids, event__in=campaign_events).values_list("event__campaign_id", flat=True)
    return set(by_registration) | set(by_activity)
