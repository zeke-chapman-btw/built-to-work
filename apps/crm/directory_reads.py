"""Bounded staff directory projections over existing CRM, identity and Task policy.

No writes, communication launch, counts, or inferred follow-ups. Authorization is
part of each SQL selector before its five-row page and one-row lookahead.
Callers receive projected dictionaries, not unevaluated private querysets.
"""
from datetime import date, timedelta
from uuid import UUID
from urllib.parse import urlencode

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db.models import Q, F, Case, When, Value, IntegerField, OuterRef, Subquery
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.timesince import timesince

from apps.customers.access import is_manager, require_manager
from apps.customers.models import CustomerOrganization
from apps.participants.workspace_access import require_people, can_notes
from apps.participants.identity import canonical_records
from apps.participants.models import Participant
from apps.operations.models import Task, TaskParticipantLink
from apps.operations.task_access import tasks_for
from apps.operations.due_dates import DUE_SOON_DAYS
from apps.recruiting.access import policy_transaction
from apps.recruiting.models import RecruitingOpportunity, RecruitingTaskLink
from apps.recruiting.selection import opportunities_for
from .access import fresh_staff
from .models import Interaction
from .navigation import with_context, with_origin, DIRECTORY_PANEL_KEYS

from .actor_names import compact_actor

PAGE_SIZE = 5
CONTEXT_SIZE = 3
PANEL_KEYS = DIRECTORY_PANEL_KEYS
ANCHOR = 'directory-relationships'
ICONS = {'call': 'phone', 'voicemail': 'phone', 'attempt': 'phone',
         'email': 'mail', 'sms': 'message', 'meeting': 'people',
         'customer_meeting': 'customers', 'video': 'people',
         'interview': 'people', 'event': 'calendar', 'follow_up': 'tasks'}


class ReadPolicy:
    """Fresh constant-size policy reads; no per-record guard substitution."""
    def __init__(self, actor, subject):
        self.actor = fresh_staff(actor)
        if subject == 'participants':
            require_people(self.actor)
        elif subject == 'customers':
            require_manager(self.actor)
        else:
            raise ValueError('Unsupported directory subject')
        self.people = self.actor.has_perm('participants.view_participant')
        self.notes = can_notes(self.actor)
        self.customers = is_manager(self.actor)
        try:
            self.opportunities = opportunities_for(self.actor).filter(archived_at__isnull=True)
            self.recruiting = True
        except PermissionDenied:
            self.opportunities = RecruitingOpportunity.objects.none()
            self.recruiting = False

    def interactions(self):
        """SQL equivalent of internal_record / staff_employer_record for reads.

        Organization+engagement and candidate authority are independent, exactly
        as in the existing guards. Staff audit access intentionally survives an
        employer disclosure withdrawal; employer portal access is unchanged.
        """
        allowed = self.opportunities.order_by().values('pk')
        btw = Q(audience='btw')
        if not self.notes:
            btw &= Q(participant__isnull=True)
        org = Q(organization__isnull=True) | Q(engagement_id__in=allowed)
        if self.customers:
            org |= Q(engagement__isnull=True)
        btw &= org
        btw &= Q(candidate__isnull=True) | Q(candidate__opportunity_id__in=allowed)
        employer = Q(audience='employer', candidate__opportunity_id__in=allowed)
        return Interaction.objects.filter(btw | employer, archived_at__isnull=True)

    def recruiting_links(self):
        allowed = self.opportunities.order_by().values('pk')
        return RecruitingTaskLink.objects.filter(opportunity_id__in=allowed).filter(
            Q(candidate__isnull=True) | Q(candidate__opportunity_id__in=allowed)
        ).filter(Q(interest__isnull=True) | Q(interest__opportunity_id__in=allowed))


def _cursor(token, *, actor, subject, view, audience):
    if not token:
        return None, False
    try:
        if len(token) > 2048:
            raise ValueError
        value = signing.loads(token, salt='crm.directory.page.v1')
        expected = [str(actor.pk), subject, view, audience]
        if value['scope'] != expected or value['day'] != timezone.localdate().isoformat():
            raise ValueError
        UUID(value['pk'])
        if view == 'activity':
            value['at'] = parse_datetime(value['at'])
            value['created'] = parse_datetime(value['created'])
            if not all(stamp and timezone.is_aware(stamp) for stamp in (value['at'], value['created'])):
                raise ValueError
        else:
            value['due'] = date.fromisoformat(value['due']) if value['due'] else None
            if value['group'] not in range(5) or (value['group'] == 4) != (value['due'] is None):
                raise ValueError
        return value, False
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None, True


def _next(row, *, actor, subject, view, audience):
    value = {'scope': [str(actor.pk), subject, view, audience],
             'day': timezone.localdate().isoformat(), 'pk': str(row.pk)}
    if view == 'activity':
        value.update(at=row.occurred_at.isoformat(), created=row.created_at.isoformat())
    else:
        value.update(group=row._due_group, due=row.due_date.isoformat() if row.due_date else '')
    return signing.dumps(value, salt='crm.directory.page.v1', compress=True)


def panel_url(request, *, view, audience='all', cursor=None):
    params = request.GET.copy()
    for key in PANEL_KEYS:
        params.pop(key, None)
    params['listing'] = view
    params['rel_view'] = view
    if view == 'activity' and audience != 'all':
        params['rel_audience'] = audience
    if cursor:
        params['rel_cursor'] = cursor
    return request.path + '?' + params.urlencode() + '#' + ANCHOR


def _subject_url(subject, pk, back, *, relationship=True, audience='btw'):
    if subject == 'participants':
        query = {'section': 'relationship' if relationship else 'overview', 'from': back.split('#', 1)[0]}
        if relationship and audience == 'employer':
            query['crm_view'] = 'employer'
        url = reverse('participants:staff-profile', args=[pk]) + '?' + urlencode(query)
        return with_context(url, back, 'crm_return_to') if relationship else with_origin(url, back)
    return with_context(reverse('customers:workspace', args=[pk, 'crm']), back, 'crm_return_to')


def _activity(policy, subject, audience, cursor, back):
    base = policy.interactions()
    base = base.filter(participant__kind='person') if subject == 'participants' else base.filter(organization__isnull=False)
    query = base.filter(audience=audience) if audience != 'all' else base
    if cursor:
        query = query.filter(Q(occurred_at__lt=cursor['at']) |
            Q(occurred_at=cursor['at'], created_at__lt=cursor['created']) |
            Q(occurred_at=cursor['at'], created_at=cursor['created'], pk__lt=cursor['pk']))
    query = canonical_records(query).select_related(
        'created_by', 'created_by__staff_profile', 'membership__identity', 'organization', 'owner_organization')
    rows = list(query.order_by('-occurred_at', '-created_at', '-pk')[:PAGE_SIZE + 1])
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    # Six or fewer IDs only; no history-sized identity or authorization ID list.
    people = {p.pk: p for p in Participant.objects.filter(pk__in={r._canonical_person for r in rows if r.participant_id})}
    now = timezone.now()
    items = []
    for row in rows:
        person = people.get(row._canonical_person)
        employer = row.audience == 'employer'
        identity = row.membership.identity if employer else None
        author = compact_actor(identity if employer else row.created_by)
        contexts = []
        if subject == 'customers':
            contexts.append({'label': str(row.organization), 'kind': 'Customer', 'url': _subject_url(subject, row.organization_id, back)})
        if person:
            contexts.append({'label': str(person), 'kind': 'Participant', 'url': _subject_url('participants', person.pk, back, audience=row.audience),
                             'archived': bool(person.archived_at)})
        items.append({'id': str(row.pk), 'summary': row.summary, 'type': row.get_activity_type_display(),
            'icon': ICONS.get(row.activity_type, 'notes'), 'at': row.occurred_at,
            'relative': timesince(row.occurred_at, now).split(',')[0] + ' ago' if row.occurred_at <= now else 'Future timestamp',
            'author': author, 'audience': 'Employer' if employer else 'BTW Staff',
            'company': str(row.owner_organization) if employer else '',
            'source': 'Manually recorded' if row.source == 'manual' else 'Provider-confirmed',
            'contexts': contexts, 'url': with_origin(reverse('crm:detail', args=[row.pk]), back)})
    empty = ('No more activity in this view.' if cursor else
             'No matches for this audience.' if audience != 'all' and base.exists() else
             'No activity in your accessible records.')
    return items, rows[-1] if more else None, empty


def _task_subjects(policy, subject):
    """Correlated subject selectors, independently authorized before Task paging.

    The double OuterRef addresses the Task outside the subject subquery. The
    canonical CASE comes from the existing reviewed identity engine, including
    its ambiguity/cycle rules and policy-transaction read context.
    """
    interactions = policy.interactions().filter(task_links__task_id=OuterRef(OuterRef('pk')))
    recruiting = policy.recruiting_links().filter(task_id=OuterRef(OuterRef('pk')))
    if subject == 'customers':
        if not policy.customers:
            return CustomerOrganization.objects.none()
        return CustomerOrganization.objects.filter(
            Q(pk__in=interactions.order_by().values('organization_id')) |
            Q(pk__in=recruiting.order_by().values('opportunity__need__organization_id')))
    if not policy.people:
        return Participant.objects.none()
    direct = canonical_records(TaskParticipantLink.objects.filter(task_id=OuterRef(OuterRef('pk'))))
    crm = canonical_records(interactions)
    candidates = canonical_records(recruiting, 'candidate__participant_id')
    interests = canonical_records(recruiting, 'interest__participant_id')
    roots = canonical_records(Participant.objects.filter(kind='person'), 'pk').filter(pk=F('_canonical_person'))
    return roots.filter(Q(pk__in=direct.order_by().values('_canonical_person')) |
        Q(pk__in=crm.order_by().values('_canonical_person')) |
        Q(pk__in=candidates.order_by().values('_canonical_person')) |
        Q(pk__in=interests.order_by().values('_canonical_person')))


def _followups(policy, subject, cursor, back):
    today = timezone.localdate()
    subjects = _task_subjects(policy, subject).order_by('pk')
    query = tasks_for(policy.actor).filter(archived_at__isnull=True, status__in=('todo', 'in_progress')).annotate(
        _due_group=Case(When(due_date__isnull=True, then=Value(4)),
            When(due_date__lt=today, then=Value(0)), When(due_date=today, then=Value(1)),
            When(due_date__lte=today + timedelta(days=DUE_SOON_DAYS), then=Value(2)),
            default=Value(3), output_field=IntegerField()),
        **{'_subject' + str(i): Subquery(subjects.values('pk')[i:i + 1]) for i in range(CONTEXT_SIZE + 1)}
    ).filter(_subject0__isnull=False)
    if cursor:
        tied = Q(_due_group=cursor['group'])
        after = Q(pk__gt=cursor['pk'])
        if cursor['due']:
            after = Q(due_date__gt=cursor['due']) | Q(due_date=cursor['due'], pk__gt=cursor['pk'])
        query = query.filter(Q(_due_group__gt=cursor['group']) | (tied & after))
    rows = list(query.order_by('_due_group', 'due_date', 'pk')[:PAGE_SIZE + 1])
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    ids = {getattr(row, '_subject' + str(i)) for row in rows for i in range(CONTEXT_SIZE)} - {None}
    model = Participant if subject == 'participants' else CustomerOrganization
    labels = {record.pk: record for record in model.objects.filter(pk__in=ids)}
    items = []
    for row in rows:
        contexts = []
        for index in range(CONTEXT_SIZE):
            record = labels.get(getattr(row, '_subject' + str(index)))
            if record is not None:
                contexts.append({'label': str(record), 'url': _subject_url(subject, record.pk, back,
                    relationship=policy.notes or policy.recruiting) if subject == 'participants' else _subject_url(subject, record.pk, back),
                    'relationship': subject == 'customers' or policy.notes or policy.recruiting,
                    'archived': bool(record.archived_at) if subject == 'participants' else record.status == 'archived'})
        items.append({'id': str(row.pk), 'title': row.title, 'contexts': contexts,
            'more_context': getattr(row, '_subject' + str(CONTEXT_SIZE)) is not None,
            'assignee': compact_actor(row.assigned_staff), 'due': row.due_date,
            'due_group': ['Overdue', 'Due today', 'Due soon', 'Later', 'Undated'][row._due_group],
            'priority': row.get_priority_display(), 'status': row.get_status_display(),
            'url': with_context(reverse('operations:task', args=[row.pk]), back, 'crm_return_to')})
    return items, rows[-1] if more else None, 'No more open follow-ups in this view.' if cursor else 'No open follow-ups in your accessible records.'


def directory_panel(request, subject):
    if subject not in {'participants', 'customers'}:
        raise ValueError('Unsupported directory subject')
    view = request.GET.get('listing') or request.GET.get('rel_view', 'activity')
    view = view if view in {'activity', 'followups'} else 'activity'
    audience = request.GET.get('rel_audience', 'all') if view == 'activity' else 'all'
    audience = audience if audience in {'all', 'btw', 'employer'} else 'all'
    back = request.get_full_path() + '#' + ANCHOR
    # An empty history needs no identity graph, opportunity projection or row locks.
    # This existence check exposes no IDs or counts. Fresh realm/directory gates
    # still run before returning; populated reads keep the full transaction below.
    if view == 'activity' and not Interaction.objects.filter(archived_at__isnull=True).exists():
        actor = fresh_staff(request.user)
        require_people(actor) if subject == 'participants' else require_manager(actor)
        cursor, refreshed = _cursor(request.GET.get('rel_cursor', ''), actor=actor,
            subject=subject, view=view, audience=audience)
        rows, token = [], None
        empty = 'No more activity in this view.' if cursor else 'No activity in your accessible records.'
    else:
        with policy_transaction():
            policy = ReadPolicy(request.user, subject)
            cursor, refreshed = _cursor(request.GET.get('rel_cursor', ''), actor=policy.actor,
                subject=subject, view=view, audience=audience)
            if view == 'activity':
                rows, next_row, empty = _activity(policy, subject, audience, cursor, back)
            else:
                rows, next_row, empty = _followups(policy, subject, cursor, back)
            token = _next(next_row, actor=policy.actor, subject=subject, view=view, audience=audience) if next_row else None
    audience_labels = [('all', 'All'), ('btw', 'BTW Staff' if subject == 'participants' else 'BTW–Customer'),
                       ('employer', 'Employer' if subject == 'participants' else 'Employer–Participant')]
    return {'directory_panel': {'subject': subject, 'view': view, 'rows': rows, 'empty': empty,
        'refreshed': refreshed, 'has_cursor': bool(cursor), 'anchor': ANCHOR,
        'views': [{'label': label, 'selected': key == view, 'url': panel_url(request, view=key)}
                  for key, label in [('activity', 'Recent Activity'), ('followups', 'Follow-ups')]],
        'audiences': [{'label': label, 'selected': key == audience, 'url': panel_url(request, view=view, audience=key)}
                      for key, label in audience_labels] if view == 'activity' else [],
        'next_url': panel_url(request, view=view, audience=audience, cursor=token) if token else '',
        'latest_url': panel_url(request, view=view, audience=audience)}}


def directory_sections(request, labels=None):
    """Navigation only: record selectors and audience guards remain unchanged."""
    labels = {'search': 'Search', 'activity': 'Recent Activity', 'followups': 'Follow-ups', **(labels or {})}
    listing = request.GET.get('listing') or request.GET.get('rel_view', 'search')
    if listing not in {'search', 'activity', 'followups'}:
        listing = 'search'
    links = []
    for key, label in [(key, labels[key]) for key in ('search', 'activity', 'followups')]:
        params = request.GET.copy()
        for field in ('listing', 'rel_view', 'rel_cursor', 'rel_audience', 'page'):
            params.pop(field, None)
        if key != 'search':
            params['listing'] = key
        links.append(dict(label=label, selected=listing == key,
            url=request.path + ('?' + params.urlencode() if params else '')))
    return {'directory_listing': listing, 'directory_sections': links}
