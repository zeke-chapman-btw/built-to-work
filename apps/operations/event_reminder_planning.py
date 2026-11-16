"""Bounded source recovery adds durable children to the existing morning jobs."""
import logging

from django.db import transaction
from django.db.models import Q

from apps.events.models import Event
from apps.staffing.models import AvailabilityRequest
from apps.participants.verification_models import ParticipantVerificationRecord
from apps.customers.models import WorkforceNeedRevision, Campaign
from .models import TaskReminderJob, TaskReminderSchedulerState
from . import event_reminder_sources as sources
from .event_reminder_calendar import local_day, scheduled_morning, summary_scope
from .reminder_planning import digest, REPLACEABLE

log = logging.getLogger(__name__)


@transaction.atomic
def schedule(source, now):
    # Event locks precede reminder rows, matching source edits and delivery.
    if source.event:
        Event.objects.select_for_update().get(pk=source.event.pk)
    source = sources.resolve(source.ref, now)
    if source is None:
        return
    _schedule_current(source, now)


def _schedule_current(source, now):
    day = local_day(now, source.event)
    event, zone = summary_scope(source.event, day) if source.event else (None, 'America/New_York')
    when = scheduled_morning(day, source.event)
    parent_key = digest('summary', source.user.pk, event.pk if event else None, zone, day)
    parent, _ = TaskReminderJob.objects.get_or_create(key=parent_key, defaults=dict(
        kind='summary', recipient=source.user, event=event, local_date=day,
        timezone_name=zone, scheduled_for=when, available_at=when))
    parent = TaskReminderJob.objects.select_for_update().get(pk=parent.pk)
    # No second notice for late discoveries after today's parent was consumed.
    # Pending work is reconsidered at the next applicable morning.
    if parent.state not in REPLACEABLE:
        return
    key = digest('source-job', parent.pk, source.identity, source.version, source.window)
    TaskReminderJob.objects.get_or_create(key=key, defaults=dict(
        kind='source', recipient=source.user, event=source.event, summary=parent,
        local_date=day, timezone_name=zone, scheduled_for=when, available_at=when,
        source_ref=source.ref, fingerprint=source.version))


@transaction.atomic
def schedule_event(event, now, issues):
    event = Event.objects.select_for_update().get(pk=event.pk)
    current = sources.event_sources(event, now, issues=issues)
    for source in current:
        _schedule_current(source, now)
    return len(current)


def scan_queries():
    return {
        'events': Event.objects.exclude(status='archived').filter(
            Q(status__in=('draft', 'upcoming')) | Q(execution__isnull=False, execution__closed_at__isnull=True)),
        'staffing': AvailabilityRequest.objects.filter(status='pending', event__status__in=('draft', 'upcoming')),
        'verification': ParticipantVerificationRecord.objects.filter(status='pending'),
        'jobs': WorkforceNeedRevision.objects.filter(state__in=('submitted', 'review')),
        'customers': Campaign.objects.filter(review_state__in=('submitted', 'review')),
    }


def plan_sources(now, *, limit=50):
    if not sources.enabled():
        return {'sources': 0, 'failed': 0}
    # Each explicit domain has its own rotating cursor. A large event table
    # cannot starve pending approvals, including legitimate older obligations.
    with transaction.atomic():
        state, _ = TaskReminderSchedulerState.objects.get_or_create(key='task-reminders')
        state = TaskReminderSchedulerState.objects.select_for_update().get(pk=state.pk)
        cursors = dict(state.source_scan or {})
        batches = {}
        for name, query in scan_queries().items():
            query = query.order_by('pk')
            batch = list(query.filter(pk__gt=cursors[name])[:limit]) if cursors.get(name) else list(query[:limit])
            if not batch and cursors.get(name):
                batch = list(query[:limit])
            cursors[name] = str(batch[-1].pk) if batch else ''
            batches[name] = batch
        state.source_scan = cursors
        state.save(update_fields=['source_scan'])
    adapters = {'events': sources.event_sources, 'staffing': sources.request_source,
                'verification': sources.verification_source, 'jobs': sources.job_source,
                'customers': sources.campaign_source}
    issues, scanned, failures = [], 0, 0
    for name, batch in batches.items():
        for record in batch:
            try:
                if name == 'events':
                    scanned += schedule_event(record, now, issues)
                    continue
                found = adapters[name](record, now, issues=issues)
                found = [found] if found else []
                for source in found:
                    schedule(source, now)
                    scanned += 1
            except Exception:
                failures += 1
                log.exception('Source reminder planning will retry: %s %s', name, record.pk)
    # A bounded sample is an operational setup warning, never a fabricated
    # approval or a worker error. Refresh it each scan, retaining no labels.
    issues = list({digest(issue): issue for issue in issues}.values())
    TaskReminderSchedulerState.objects.filter(pk='task-reminders').update(
        responsibility_health={'checked_at': now.isoformat(), 'issues': issues[:50],
                               'sampled': sum(map(len, batches.values()))})
    return {'sources': scanned, 'failed': failures}


def proposals(job, user, now):
    result = []
    cache = {}
    children = list(job.source_jobs.filter(state__in=REPLACEABLE).order_by('pk'))
    for child in children:
        source = sources.resolve(child.source_ref, now, cache=cache)
        if (source is None or source.user.pk != user.pk or source.version != child.fingerprint or
                local_day(now, source.event) != child.local_date):
            TaskReminderJob.objects.filter(pk=child.pk).update(state='superseded', finished_at=now)
            continue
        from .event_reminder_snooze import snoozed
        if snoozed(source, user, now):
            continue
        result.append({'task': None, 'kind': 'source', 'source_ref': source.ref,
                       'dedupe_key': source.coverage(), 'fingerprint': source.version})
    return result


def lock_source_rows(job):
    """Called after event/task locks, before user/job locks. Never wait inversely."""
    from django.db import connection
    from .execution_models import EventExecution, EventChecklistItem
    from apps.staffing.models import StaffProfile
    from apps.customers.models import CustomerOrganization, WorkforceNeed
    refs = list(job.source_jobs.filter(state__in=REPLACEABLE).values_list('source_ref', flat=True))
    nowait = connection.features.has_select_for_update_nowait
    models = [('preparation', EventChecklistItem), ('staffing-review', AvailabilityRequest),
              ('verification', ParticipantVerificationRecord), ('job-review', WorkforceNeedRevision),
              ('customer-review', Campaign)]
    event_ids = {ref.get('event') for ref in refs} - {None}
    executions = list(EventExecution.objects.select_for_update(nowait=nowait).filter(event_id__in=event_ids).order_by('pk'))
    revisions = WorkforceNeedRevision.objects.filter(pk__in=[ref['id'] for ref in refs if ref.get('type') == 'job-review'])
    campaigns = Campaign.objects.filter(pk__in=[ref['id'] for ref in refs if ref.get('type') == 'customer-review'])
    organization_ids = set(revisions.values_list('organization_id', flat=True)) | set(campaigns.values_list('organization_id', flat=True))
    list(CustomerOrganization.objects.select_for_update(nowait=nowait).filter(pk__in=organization_ids).order_by('pk'))
    list(WorkforceNeed.objects.select_for_update(nowait=nowait).filter(pk__in=revisions.values_list('need_id', flat=True)).order_by('pk'))
    for kind, model in models:
        ids = [ref['id'] for ref in refs if ref.get('type') == kind]
        list(model.objects.select_for_update(nowait=nowait).filter(pk__in=ids).order_by('pk'))
    # Qualification/document edits need their own current row locks; decisions
    # append new records rather than updating the older pending decision.
    from apps.participants.models import ParticipantQualification, ParticipantDocument
    reviews = ParticipantVerificationRecord.objects.filter(pk__in=[ref['id'] for ref in refs if ref.get('type') == 'verification'])
    for model, ids in ((ParticipantQualification, reviews.values_list('qualification_id', flat=True)),
                       (ParticipantDocument, set(reviews.values_list('document_id', flat=True)) |
                                             set(reviews.values_list('evidence_document_id', flat=True)))):
        list(model.objects.select_for_update(nowait=nowait).filter(pk__in=ids).order_by('pk'))
    profiles = {execution.primary_manager_id for execution in executions} - {None}
    profiles.update(EventChecklistItem.objects.filter(pk__in=[ref['id'] for ref in refs if ref.get('type') == 'preparation'])
                    .values_list('assigned_staff_id', flat=True))
    list(StaffProfile.objects.select_for_update(nowait=nowait).filter(pk__in=profiles).order_by('pk'))
