"""Calendar rules for source-backed event reminders, using the 18A clock."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from .reminder_calendar import EASTERN, morning


def business_days_after(day, count=3):
    """The end date is not counted; holidays are outside this release."""
    if count < 1:
        raise ValueError('Business-day delay must be positive.')
    while count:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count -= 1
    return day


def advance_day(work_day, days_before, *, weekend_eligible=False):
    if days_before < 1:
        raise ValueError('Advance reminders must precede the work date.')
    day = work_day - timedelta(days=days_before)
    if day.weekday() >= 5 and not weekend_eligible:
        day -= timedelta(days=day.weekday() - 4)
    return day


def latest_window(work_day, leads, today, *, work_on=lambda day: False):
    """One useful remaining advance window; never replay notices after work."""
    if today >= work_day or (today.weekday() >= 5 and not work_on(today)):
        return None
    eligible = []
    for lead in leads:
        raw = work_day - timedelta(days=lead)
        day = advance_day(work_day, lead, weekend_eligible=work_on(raw))
        if day <= today:
            eligible.append(day)
    return max(eligible) if eligible else None


def summary_scope(event, day):
    """Eastern weekday event work shares 18A's general summary identity."""
    zone = event.timezone_name if event else EASTERN
    return (None if day.weekday() < 5 and zone == EASTERN else event), zone


def local_day(now, event=None):
    return now.astimezone(ZoneInfo(event.timezone_name if event else EASTERN)).date()


def scheduled_morning(day, event=None):
    return morning(day, event.timezone_name if event else EASTERN)
