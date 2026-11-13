"""Calendar rules only: source deadlines remain date-only and unmodified."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

EASTERN = 'America/New_York'


def previous_working_day(day):
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def next_working_day(day):
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def morning(day, zone=EASTERN):
    # Seven is outside DST transition gaps/folds; ZoneInfo handles UTC conversion.
    return datetime.combine(day, time(7), ZoneInfo(zone))


def primary_event(task):
    if hasattr(task, '_reminder_primary_event'):
        return task._reminder_primary_event
    if task.event_id:
        return task.event
    # Do not invent a primary event when legacy links are ambiguous.
    links = list(task.event_links.select_related('event').order_by('event_id')[:2])
    return links[0].event if len(links) == 1 else None


def weekend_applies(task, day, event=None):
    work = task.weekend_work_date
    if day.weekday() < 5 or not work or work.weekday() < 5:
        return False
    event = event or primary_event(task)
    if event is None:
        return False
    saturday = day - timedelta(days=day.weekday() - 5)
    work_saturday = work - timedelta(days=work.weekday() - 5)
    return saturday == work_saturday


def task_zone(task):
    event = primary_event(task)
    return event.timezone_name if event else EASTERN


def scope_for(task, day=None):
    """A deterministic scope keeps the same deadline out of two summaries."""
    event = primary_event(task)
    if event and (event.timezone_name != EASTERN or (day and weekend_applies(task, day, event))):
        return event, event.timezone_name
    return None, EASTERN


def next_applicable_morning(task, now):
    event = primary_event(task)
    zone = ZoneInfo(event.timezone_name if event else EASTERN)
    local = now.astimezone(zone)
    day = local.date()
    for _ in range(9):
        candidate = morning(day, str(zone))
        if candidate > now and (day.weekday() < 5 or weekend_applies(task, day, event)):
            return candidate
        day += timedelta(days=1)
    raise ValueError('No applicable morning found.')
