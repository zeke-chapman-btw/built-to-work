"""Task workspace queries and strictly local return navigation."""
from urllib.parse import urlencode, urlsplit, parse_qsl
from datetime import timedelta
import uuid
from django.db.models import Q, Count, Case, When, Value, IntegerField, F
from django.urls import reverse, resolve, Resolver404
from django.utils import timezone
from . import task_access
from .models import Task

FILTER_KEYS = ('view','q','status','priority','due','assignee','event','page')

def return_path(raw=''):
    fallback = reverse('operations:tasks')
    if not raw or len(raw)>2400 or any(c in raw for c in ('\\','\r','\n')):
        return fallback
    try:
        parts = urlsplit(raw)
    except ValueError:
        return fallback
    if parts.scheme or parts.netloc:
        return fallback
    if parts.path not in (fallback, reverse('operations:my-tasks')):
        try:
            match = resolve(parts.path)
        except Resolver404:
            return fallback
        if match.view_name != 'operations:event' or match.kwargs.get('section') != 'tasks':
            return fallback
        # Destination access remains enforced by the Event Tasks view.
        return parts.path
    pairs = [(k,v[:200]) for k,v in parse_qsl(parts.query) if k in FILTER_KEYS]
    return parts.path + ('?' + urlencode(pairs) if pairs else '')

def detail_url(task, back=''):
    return reverse('operations:task', args=[task.pk]) + '?' + urlencode({'return_to':return_path(back)})

def workspace(user, params, *, mine=False):
    allowed = task_access.tasks_for(user)
    today = timezone.localdate()
    view = 'mine' if mine else params.get('view','mine')
    if view not in ('mine','created','all','archived','open','completed','overdue'):
        view='mine'
    filters = {k:params.get(k,'').strip()[:200] for k in ('q','status','priority','due','assignee','event')}
    # Existing deep links remain useful; their old view becomes an explicit filter.
    if view in ('open','completed','overdue'):
        if view=='completed':filters['status']='completed'
        if view=='overdue':filters['due']='overdue'
        legacy_open=view=='open';view='all'
    else:legacy_open=False
    qs=allowed.filter(archived_at__isnull=view!='archived')
    if view=='mine':qs=qs.filter(assigned_staff__user=user)
    if view=='created':qs=qs.filter(created_by=user)
    if legacy_open:qs=qs.filter(status__in=('todo','in_progress'))
    if filters['q']:qs=qs.filter(Q(title__icontains=filters['q'])|Q(description__icontains=filters['q']))
    for key, choices in (('status',Task.Status.values),('priority',Task.Priority.values)):
        if filters[key] in choices:qs=qs.filter(**{key:filters[key]})
    due=filters['due']
    if due=='overdue':qs=qs.filter(status__in=('todo','in_progress'),due_date__lt=today)
    elif due=='soon':
        from .due_dates import DUE_SOON_DAYS
        qs=qs.filter(status__in=('todo','in_progress'),due_date__range=(today,today+timedelta(days=DUE_SOON_DAYS)))
    elif due=='today':qs=qs.filter(due_date=today)
    elif due=='week':qs=qs.filter(due_date__range=(today,today+timedelta(days=7)))
    elif due=='undated':qs=qs.filter(due_date__isnull=True)
    managing=task_access.task_admin(user)
    if managing and filters['assignee']:
        try:qs=qs.filter(assigned_staff_id=uuid.UUID(filters['assignee']))
        except ValueError:qs=qs.none()
    if managing and filters['event']:
        try:
            pk=uuid.UUID(filters['event'])
            qs=qs.filter(Q(event_id=pk)|Q(event_links__event_id=pk)).distinct() if task_access.events_for(user).filter(pk=pk).exists() else qs.none()
        except ValueError:qs=qs.none()
    qs=qs.annotate(_action_rank=Case(When(status__in=('todo','in_progress'),then=Value(0)),default=Value(1),output_field=IntegerField()),
        _priority_rank=Case(When(priority='high',then=Value(0)),When(priority='normal',then=Value(1)),default=Value(2),output_field=IntegerField()))
    qs=qs.order_by('_action_rank',F('due_date').asc(nulls_last=True),'_priority_rank','-created_at','pk')
    active=allowed.filter(archived_at__isnull=True)
    counts=allowed.aggregate(mine=Count('pk',filter=Q(archived_at__isnull=True,assigned_staff__user=user)),
        created=Count('pk',filter=Q(archived_at__isnull=True,created_by=user)),
        all=Count('pk',filter=Q(archived_at__isnull=True)),archived=Count('pk',filter=Q(archived_at__isnull=False)))
    summary=active.aggregate(open=Count('pk',filter=Q(status__in=('todo','in_progress'))),
        overdue=Count('pk',filter=Q(status__in=('todo','in_progress'),due_date__lt=today)),completed=Count('pk',filter=Q(status='completed')))
    query={k:v for k,v in filters.items() if v};query['view']=view
    tabs=[]
    for key,label in (('mine','My Tasks'),('created','Assigned by Me'),('all','All Tasks'),('archived','Archived')):
        tab=dict(query,view=key);tabs.append({'label':label,'count':counts[key],'active':view==key,'url':reverse('operations:tasks')+'?'+urlencode(tab)})
    return {'queryset':qs,'allowed':allowed,'view':view,'filters':filters,'tabs':tabs,'summary':summary,'query':urlencode(query),'today':today,'managing':managing}
