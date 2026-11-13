"""Permanent sign-up links and the QR codes printed from them.

- General: /participants/register/ — always the general online form.
- Event:   /participants/register/event/<event id>/ — never changes for that event.
- Trailer: /participants/register/trailer/<trailer id>/ — a sign that stays on the trailer and
  opens whatever event that trailer is at today (or the general form between events).
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone


def general_path():
    return reverse("participants:registration-start")


def event_path(event):
    return reverse("participants:registration-event-start", args=[event.pk])


def trailer_path(trailer):
    return reverse("participants:registration-trailer-start", args=[trailer.pk])


def trailer_event(trailer, now=None):
    """The event a trailer is at right now: one happening today, else one starting within a day (setup)."""
    from apps.events.models import Event
    now = now or timezone.now()
    events = (Event.objects.filter(offline_preparations__trailer=trailer)
              .exclude(offline_preparations__status="closed")
              .filter(status="upcoming").distinct())
    happening = events.filter(start_at__lte=now + timedelta(hours=12), end_at__gte=now - timedelta(hours=2)).order_by("end_at").first()
    if happening:
        return happening
    return events.filter(start_at__gt=now, start_at__lte=now + timedelta(days=1)).order_by("start_at").first()


def public_events(limit=60):
    """Events people can still sign up for, soonest first."""
    from apps.events.models import Event
    return Event.objects.filter(end_at__gte=timezone.now(), status="upcoming").order_by("start_at")[:limit]
