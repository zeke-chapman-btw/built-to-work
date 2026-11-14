"""Automatic staff mileage: straight-line distance from Watkinsville plus 20%, doubled for the round trip.

The event location comes from its map pin when one is set, otherwise from its ZIP code,
otherwise from the average of the ZIP codes for its city and state. Everything is worked
out offline from bundled ZIP data, so it never depends on an outside service.
"""
from decimal import Decimal, ROUND_HALF_UP
from math import asin, cos, radians, sin, sqrt
import re

ORIGIN_POINT = (Decimal('33.8626'), Decimal('-83.4088'))  # Watkinsville, Georgia
ROAD_FACTOR = Decimal('1.2')
EARTH_MILES = 3958.8


def straight_line(a, b):
    lat1, lon1, lat2, lon2 = map(lambda v: radians(float(v)), (a[0], a[1], b[0], b[1]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return Decimal(str(2 * EARTH_MILES * asin(sqrt(h))))


def _zip_point(code):
    try:
        import zipcodes
        rows = zipcodes.matching(code)
    except Exception:
        return None
    rows = [r for r in rows if r.get('lat') and r.get('long')]
    return (Decimal(rows[0]['lat']), Decimal(rows[0]['long'])) if rows else None


def _city_point(city, state):
    try:
        import zipcodes
        rows = zipcodes.filter_by(city=city.strip().title(), state=state.strip().upper())
    except Exception:
        return None
    rows = [r for r in rows if r.get('lat') and r.get('long')]
    if not rows:
        return None
    n = Decimal(len(rows))
    return (sum(Decimal(r['lat']) for r in rows) / n, sum(Decimal(r['long']) for r in rows) / n)


STATE_CODES = {'georgia': 'GA', 'south carolina': 'SC', 'north carolina': 'NC', 'florida': 'FL', 'alabama': 'AL', 'tennessee': 'TN'}


def event_point(event):
    """Return ((lat, lng), how) or (None, reason)."""
    if event.map_latitude is not None and event.map_longitude is not None:
        return (Decimal(event.map_latitude), Decimal(event.map_longitude)), 'map pin'
    code = re.match(r'\s*(\d{5})', event.postal_code or '')
    if code:
        point = _zip_point(code.group(1))
        if point:
            return point, 'ZIP code ' + code.group(1)
    state = (event.state or '').strip()
    state = STATE_CODES.get(state.lower(), state)
    if event.city and len(state) == 2:
        point = _city_point(event.city, state)
        if point:
            return point, f'{event.city.strip().title()}, {state.upper()}'
    return None, 'Add a ZIP code or map pin to the event location.'


def estimate(event):
    """Worked-out miles for an event, or None when the location is unknown."""
    point, how = event_point(event)
    if point is None:
        return None
    straight = straight_line(ORIGIN_POINT, point)
    one_way = (straight * ROAD_FACTOR).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return {
        'straight_line': straight.quantize(Decimal('1'), rounding=ROUND_HALF_UP),
        'one_way': one_way,
        'round_trip': one_way * 2,
        'based_on': how,
    }
