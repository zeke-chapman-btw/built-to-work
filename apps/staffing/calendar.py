"""Bounded daily staffing calendar; no payroll or complex shift engine."""
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError


def event_days(event):
    first = event.setup_date or event.local_start_at.date()
    last = event.local_end_at.date()
    count = (last - first).days + 1
    if count < 1 or count > 366:
        raise ValidationError("Daily staffing supports events of up to 366 days. Review the event schedule.")
    return [first + timedelta(days=i) for i in range(count)]


def local_instant(day, clock, zone):
    value = datetime.combine(day, clock)
    candidates = [value.replace(tzinfo=zone, fold=f) for f in (0, 1)]
    valid = [v for v in candidates if v.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value]
    if not valid or len({v.utcoffset() for v in valid}) > 1:
        raise ValidationError("Choose an unambiguous local time outside the daylight-saving clock change.")
    return valid[0]


def day_window(event, day, start=None, end=None, arrival=None):
    if day not in event_days(event):
        raise ValidationError("Select dates within the event's setup and operating days.")
    zone = ZoneInfo(event.timezone_name)
    setup = day < event.local_start_at.date()
    if bool(start) != bool(end):
        raise ValidationError("Enter both working times or leave both blank.")
    if start and end <= start:
        raise ValidationError("Daily work must end after it starts on the same day.")
    default_start = time.min if setup else event.daily_start_time or (event.local_start_at.time() if day == event.local_start_at.date() else time.min)
    default_end = event.daily_end_time if not setup else None
    starts = local_instant(day, start or default_start, zone)
    ends = local_instant(day, end or default_end, zone) if end or default_end else local_instant(day + timedelta(days=1), time.min, zone)
    if not setup:
        if day == event.local_start_at.date() and starts < event.start_at:
            raise ValidationError("Working time cannot start before this event's operating window; use arrival time for early arrival.")
        if day == event.local_end_at.date():
            if not end and not default_end:
                ends = event.end_at
            if ends > event.end_at:
                raise ValidationError("Working time cannot end after this event's operating window.")
    if ends <= starts:
        raise ValidationError("The selected day has no operating time. Review the event dates.")
    arrival_at = local_instant(day, arrival, zone) if arrival else None
    if arrival_at and arrival_at > starts:
        raise ValidationError("Arrival must be at or before work starts.")
    return dict(date=day, starts_at=starts, ends_at=ends, arrival_at=arrival_at)
