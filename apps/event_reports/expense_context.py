"""Return only to this event's Expenses section; never trust a submitted URL."""
import re
from urllib.parse import parse_qs, urlencode, urlsplit
from django.urls import reverse

FILTER_KEYS = ('category', 'start', 'end', 'page', 'incentive_page')


def expense_list_url(event, values=None, fragment='expenses'):
    query = {'section': 'expenses'}
    for key in FILTER_KEYS:
        value = (values or {}).get(key, '')
        if isinstance(value, str) and value and len(value) <= 200:
            query[key] = value
    return reverse('event_reports:detail', args=[event.pk]) + '?' + urlencode(query) + '#' + fragment


def expense_return_url(request, event):
    raw = (request.POST if request.method == 'POST' else request.GET).get('return_to', '')
    fallback = expense_list_url(event)
    if not raw or len(raw) > 2000 or any(ord(c) < 32 for c in raw) or '\\' in raw:
        return fallback
    try:
        target = urlsplit(raw)
        query = parse_qs(target.query, max_num_fields=20)
    except ValueError:
        return fallback
    if target.scheme or target.netloc or target.path != reverse('event_reports:detail', args=[event.pk]):
        return fallback
    if query.get('section') != ['expenses']:
        return fallback
    fragment = target.fragment
    if fragment != 'expenses' and not re.fullmatch(r'expense-[0-9a-fA-F-]{36}', fragment):
        fragment = 'expenses'
    return expense_list_url(event, {k: v[-1] for k, v in query.items()}, fragment)
