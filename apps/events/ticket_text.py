"""Plain wording for what a ticket shows: the event's overall dates and where it is."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone


def _local(value, event):
    try:
        zone = ZoneInfo(getattr(event, "timezone_name", "") or "America/New_York")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("America/New_York")
    return timezone.localtime(value, zone) if timezone.is_aware(value) else value


def event_dates(event):
    """'Thursday, December 3, 2026', 'Dec 3–4, 2026' or 'Nov 30 – Dec 1, 2026'."""
    start = getattr(event, "start_at", None)
    if not start:
        return ""
    start = _local(start, event)
    end = getattr(event, "end_at", None)
    end = _local(end, event) if end else start
    if end.date() <= start.date():
        return f"{start:%A}, {start:%B} {start.day}, {start.year}"
    if start.year != end.year:
        return f"{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}"
    if start.month != end.month:
        return f"{start:%b} {start.day} – {end:%b} {end.day}, {end.year}"
    return f"{start:%b} {start.day}–{end.day}, {end.year}"


def event_place(event):
    """Venue name, then 'City, ST' (or the street line when there is no city)."""
    name = (getattr(event, "location_name", "") or "").strip()
    city = ", ".join(part for part in ((getattr(event, "city", "") or "").strip(), (getattr(event, "state", "") or "").strip()) if part)
    street = (getattr(event, "address_line_1", "") or "").strip()
    second = city or street
    if name and second and second.lower() not in name.lower():
        return f"{name} · {second}"
    return name or second
