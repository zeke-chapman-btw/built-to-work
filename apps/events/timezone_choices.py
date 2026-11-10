"""Staff-facing timezone labels; stored IANA names and scheduling rules are unchanged."""
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

COMMON_ZONES = (
    ("America/New_York", "Eastern", "EST / EDT"),
    ("America/Chicago", "Central", "CST / CDT"),
    ("America/Denver", "Mountain", "MST / MDT"),
    ("America/Phoenix", "Arizona", "MST"),
    ("America/Los_Angeles", "Pacific", "PST / PDT"),
    ("America/Anchorage", "Alaska", "AKST / AKDT"),
    ("Pacific/Honolulu", "Hawaii", "HST"),
    ("UTC", "UTC", "UTC"),
)

@lru_cache(maxsize=1)
def _choices():
    common = [(key, name if name == "UTC" else f"{name} ({abbr})") for key, name, abbr in COMMON_ZONES]
    keys = {key for key, _ in common}
    other = [(key, key.replace("_", " ").replace("/", " — ")) for key in sorted(available_timezones() - keys)]
    return common, other

def timezone_choices(current=None):
    common, other = _choices()
    # Retain an existing/custom or invalid submitted value so validation errors do not lose it.
    if current and current not in {key for key, _ in common + other}:
        other = [*other, (current, current.replace("_", " ").replace("/", " — "))]
    return [("Common time zones", common), ("Other time zones", other)]

def event_timezone_label(event, instant=None):
    zone = ZoneInfo(event.timezone_name)
    names = {key: name for key, name, _ in COMMON_ZONES}
    name = names.get(event.timezone_name, event.timezone_name.split("/")[-1].replace("_", " "))
    dates = [instant] if instant else [event.start_at, event.end_at]
    abbreviations = list(dict.fromkeys(value.astimezone(zone).tzname() for value in dates if value))
    return name if not abbreviations or abbreviations == [name] else f"{name} ({' / '.join(abbreviations)})"
