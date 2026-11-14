"""Plain-language list and detail helpers for the customer portal pages.

Everything here works from projections the customer is already allowed to see:
released candidates (list_candidates) and the company's own positions and campaigns.
"""
from .position_view import pay_text

# Company response label -> (filter group, chip text, chip tone)
RESPONSE_STATUS = {
    'Request Interview': ('interviewing', 'Interview requested', 'b'),
    'Interested': ('interviewing', 'Interested', 'b'),
    'Not Interested': ('notfit', 'Not a fit', ''),
    'Report Hired': ('hired', 'Hired · waiting on Built to Work', 'y'),
    'Feedback': ('other', 'Note sent', ''),
}
CANDIDATE_FILTERS = [('waiting', 'Waiting on you'), ('interviewing', 'Interviewing'), ('hired', 'Hired'), ('notfit', 'Not a fit'), ('all', 'All')]


def candidate_status(c):
    if c.get('btw_confirmed_hire'):
        return 'hired', 'Hired · confirmed', 'g'
    latest = c.get('latest_response')
    if latest:
        return RESPONSE_STATUS.get(latest['label'], ('other', latest['label'], ''))
    if c.get('can_respond'):
        return 'waiting', 'Waiting on you', 'y'
    return 'other', 'Shared with you', ''


def with_status(candidates):
    rows = []
    for c in candidates:
        group, label, tone = candidate_status(c)
        rows.append(dict(c, status_group=group, status_label=label, status_tone=tone,
            initials=''.join(part[:1] for part in (c['candidate'].get('name') or 'Candidate').split()[:2]).upper()))
    return rows


def candidate_filters(rows, show):
    counts = {key: sum(1 for r in rows if r['status_group'] == key) for key, _ in CANDIDATE_FILTERS}
    counts['all'] = len(rows)
    return [{'key': key, 'label': label, 'count': counts[key], 'on': key == show} for key, label in CANDIDATE_FILTERS]


def default_candidate_filter(rows):
    return 'waiting' if any(r['status_group'] == 'waiting' for r in rows) else 'all'


# Position status -> (filter group, chip text, chip tone)
def position_status(row):
    status, review = row['record_status'], row['review_state']
    if status in {'closed', 'cancelled'}:
        return 'closed', 'Closed' if status == 'closed' else 'Cancelled', ''
    if status in {'active', 'hold'}:
        label, tone = ('Open · recruiting', 'g') if status == 'active' else ('On hold', 'y')
        return 'open', label, tone
    if review == 'returned':
        return 'draft', 'Changes requested', 'r'
    if review in {'submitted', 'review'}:
        return 'waiting', 'Waiting on Built to Work', 'b'
    return 'draft', 'Draft', ''


POSITION_FILTERS = [('open', 'Open'), ('draft', 'Drafts'), ('waiting', 'Waiting on Built to Work'), ('closed', 'Closed'), ('all', 'All')]


def position_summary(need):
    rev = need.display_revision or need.working_revision or need.revisions.order_by('-number').first()
    if not rev:
        return ''
    parts = [rev.get_employment_type_display() if rev.employment_type else '', pay_text(rev), rev.city or rev.location_name]
    return ' · '.join(p for p in parts if p)


def position_rows(needs, objects, candidates):
    """needs: need_row dicts; objects: {id: WorkforceNeed}; candidates: list_candidates rows."""
    counts = {}
    for c in with_status(candidates):
        row = counts.setdefault(c['position_id'], {'sent': 0, 'hired': 0, 'waiting': 0})
        row['sent'] += 1
        row['hired'] += 1 if c['btw_confirmed_hire'] else 0
        row['waiting'] += 1 if c['status_group'] == 'waiting' else 0
    rows = []
    for n in needs:
        group, label, tone = position_status(n)
        obj = objects.get(str(n['id']))
        rows.append(dict(n, **counts.get(str(n['id']), {'sent': 0, 'hired': 0, 'waiting': 0}),
            status_group=group, status_label=label, status_tone=tone,
            change_pending=group == 'open' and n['review_state'] in {'submitted', 'review', 'draft'} and n['pending'],
            summary=position_summary(obj) if obj else ''))
    return rows


def position_filters(rows, show):
    counts = {key: sum(1 for r in rows if r['status_group'] == key) for key, _ in POSITION_FILTERS}
    counts['all'] = len(rows)
    return [{'key': key, 'label': label, 'count': counts[key], 'on': key == show} for key, label in POSITION_FILTERS]


def key_details(position):
    """A short 'at a glance' list taken from the full position view."""
    if not position:
        return []
    cards = {card['key']: {row['label']: row for row in card['rows']} for card in position['cards']}
    tiles = {tile['label']: tile['value'] for tile in position['tiles']}
    def value(card, label):
        row = cards.get(card, {}).get(label) or {}
        return ' · '.join(v for v in (row.get('chip'), row.get('value')) if v)
    where = ' · '.join(v for v in (value('location', 'Place'), value('location', 'Address')) if v) or value('location', 'Setting')
    return [
        {'label': 'Where', 'value': where},
        {'label': 'Schedule', 'value': ' · '.join(v for v in (tiles.get('Schedule'), tiles.get('Start') and 'Start ' + tiles['Start']) if v)},
        {'label': 'Pay', 'value': ' · '.join(v for v in (tiles.get('Pay'), value('pay', 'Benefits')) if v)},
        {'label': 'Must have', 'value': ' · '.join(v for v in (value('requirements', 'Skills needed'), value('requirements', 'Licenses needed')) if v)},
        {'label': 'Hiring contact', 'value': value('contact', 'Contact')},
    ]


# What a candidate page can show. The first look is what BTW releases first;
# the rest opens up as BTW shares more for this relationship.
FIRST_LOOK = ('summary', 'region', 'experience')
MORE_SECTIONS = [
    ('qualifications', 'Certifications & licenses', 'Which credentials they hold and whether Built to Work verified them.'),
    ('assessments', 'Simulator & quiz results', 'Official results from Built to Work events.'),
    ('documents', 'Documents', 'Resume, certificates and other files the candidate agrees to share.'),
    ('references', 'References', 'People who can speak to their work.'),
    ('history', 'Work history', 'Past jobs and how long they stayed.'),
]


def more_sections(projection):
    shared, waiting = [], []
    for key, title, about in MORE_SECTIONS:
        (shared if projection.get(key) is not None else waiting).append({'key': key, 'title': title, 'about': about})
    return shared, waiting


def campaign_status(row):
    status, review = row['record_status'], row['review_state']
    if status in {'completed', 'cancelled'}:
        return 'Finished' if status == 'completed' else 'Cancelled', ''
    if status == 'active':
        return 'Active', 'g'
    if status == 'hold':
        return 'On hold', 'y'
    if review == 'returned':
        return 'Changes requested', 'r'
    if review in {'submitted', 'review'}:
        return 'Waiting on Built to Work', 'b'
    if review == 'approved':
        return 'Approved', 'g'
    return 'Draft', ''


def campaign_extras(row, campaign, candidates):
    """Positions, sent and hired counts for one campaign."""
    from .projections import need_row
    needs = [need_row(n) for n in campaign.workforce_needs.select_related('effective_revision', 'working_revision')]
    ids = {str(n['id']) for n in needs}
    mine = [c for c in candidates if c['position_id'] in ids]
    label, tone = campaign_status(row)
    return dict(row, positions=needs, sent=len(mine), hired=sum(1 for c in mine if c['btw_confirmed_hire']),
        openings=sum(n['quantity'] or 0 for n in needs if n['record_status'] == 'active'),
        status_label=label, status_tone=tone)
