"""Explicit read adapters for existing event/review workflows; never make decisions."""
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.events.models import Event
from . import event_reminder_policy as policy, execution_services, task_access
from .event_reminder_calendar import business_days_after, latest_window, local_day
from .execution_models import EventExecution, EventChecklistItem
from .reminder_planning import digest


def enabled():
    return bool(getattr(settings, 'BTW_TASK_REMINDERS_ENABLED', False) and
                getattr(settings, 'BTW_EVENT_APPROVAL_REMINDERS_ENABLED', False))


@dataclass
class Source:
    ref: dict
    user: object
    event: object
    title: str
    label: str
    section: str
    destination: str
    version: str
    window: date

    @property
    def identity(self):
        return digest(self.ref)

    def coverage(self):
        return digest('source', self.user.pk, self.identity, self.version, self.window)

    @property
    def schedule_version(self):
        # A timing edit can replace a still-pending advance window without
        # making the unresolved work disappear from an older readable receipt.
        return digest(self.version, self.window)


def active_user(profile):
    if not profile or not profile.is_active or not profile.user_id:
        return None
    user = get_user_model().objects.filter(pk=profile.user_id).first()
    return user if user and task_access.staff_user(user) else None


def owner(execution, issues):
    user = active_user(execution.primary_manager) if execution else None
    if not user or not execution_services.manager(user, execution):
        issues.append({'category': 'event-owner', 'id': str(execution.event_id) if execution else '',
                       'code': 'missing-or-ineligible'})
        return None
    return user


def work_on(event, day):
    return (event.setup_date or event.local_start_at.date()) <= day <= event.local_end_at.date()


def _source(ref, user, event, title, label, section, url, window, *version):
    return Source(ref, user, event, title, label, section, url,
        digest(ref, user.pk, event.start_at if event else None, event.end_at if event else None,
               event.setup_date if event else None, event.timezone_name if event else None, *version), window)


def event_sources(event, now, *, issues=None):
    if getattr(event, "managed_in_base44", False):
        return []  # still run from base44: no reminders here
    issues = issues if issues is not None else []
    if not enabled() or event.status == 'archived':
        return []
    execution = EventExecution.objects.select_related('primary_manager').filter(event=event).first()
    if execution and execution.closed_at:
        return []
    values = policy.values(event)
    today = local_day(now, event)
    recipient = owner(execution, issues) if execution else None
    if execution is None:
        issues.append({'category': 'event-owner', 'id': str(event.pk), 'code': 'not-configured'})
    result = []
    if event.status in ('draft', 'upcoming'):
        window = latest_window(event.local_start_at.date(), values['staffing_days'], today,
                               work_on=lambda day: work_on(event, day))
        if window and values['staffing_enabled'] and recipient:
            from apps.staffing.services import coverage
            if recipient.has_perms(('staffing.manage_event_staffing', 'events.view_event')):
                for day in coverage(event)['days']:
                    for role in day['roles']:
                        if role['remaining']:
                            ref = {'type': 'staffing', 'event': str(event.pk),
                                   'date': day['date'].isoformat(), 'role': role['key']}
                            result.append(_source(ref, recipient, event, event.name,
                                f"{day['date']:%b %d} · {role['label']}: {role['remaining']} needed",
                                'Staffing', reverse('staffing:event', args=[event.pk]), window))
            else:
                issues.append({'category': 'staffing', 'id': str(event.pk), 'code': 'owner-access'})
        setup = event.setup_date or event.local_start_at.date()
        window = latest_window(setup, values['preparation_days'], today,
                               work_on=lambda day: work_on(event, day))
        if window and values['preparation_enabled'] and execution:
            items = EventChecklistItem.objects.filter(category__execution=execution,
                category__phase__in=('prepare', 'arrival'), is_required=True,
                state='todo', archived_at__isnull=True).select_related('assigned_staff')
            for item in items:
                user = active_user(item.assigned_staff) if item.assigned_staff_id else recipient
                if user and execution_services.can_view(user, event):
                    result.append(_source({'type': 'preparation', 'id': str(item.pk), 'event': str(event.pk)},
                        user, event, item.title, f'{event.name} · {setup:%b %d}', 'Event preparation',
                        reverse('operations:health', args=[event.pk]) + '?state=todo#item-' + str(item.pk),
                        window, item.assigned_staff_id, recipient.pk if not item.assigned_staff_id and recipient else None))
                else:
                    issues.append({'category': 'preparation', 'id': str(item.pk), 'code': 'assignee-access'})
    if execution and recipient and values['closeout_enabled'] and today.weekday() < 5:
        ended = execution.actual_ended_at or event.end_at
        end_day = ended.astimezone(ZoneInfo(event.timezone_name)).date()
        if ended <= now and today >= business_days_after(end_day, values['closeout_days']):
            checklist = EventChecklistItem.objects.filter(category__execution=execution,
                category__phase='closeout', is_required=True, state='todo', archived_at__isnull=True).exists()
            financial_pending = closeout_financial(event)
            financial = financial_pending and can_report(recipient)
            if financial_pending and not financial:
                issues.append({'category': 'closeout', 'id': str(event.pk), 'code': 'owner-report-access'})
            task_open = bool(execution.closeout_task_id and task_access.tasks_for(recipient).filter(
                pk=execution.closeout_task_id, archived_at__isnull=True, status__in=('todo', 'in_progress')).exists())
            if checklist or financial or task_open:
                result.append(_source({'type': 'closeout', 'event': str(event.pk)}, recipient, event,
                    event.name, 'Closeout to finish', 'Event closeout',
                    reverse('operations:closeout', args=[event.pk]), today, ended))
    return result


def can_report(user):
    from apps.event_reports.services import can_report as allowed
    return allowed(user)


def closeout_financial(event):
    """Only recorded or explicitly required work; an absent expense is not a debt."""
    from apps.event_reports.services import fingerprint
    from apps.event_reports.models import ManualExpense, IncentiveLine, CostReview
    from apps.workdays.models import Workday, MileageTrip
    from apps.staffing.models import AssignmentDay
    actual = (Workday.objects.filter(event=event, is_active=True).exists() or
              MileageTrip.objects.filter(event=event).exclude(status='void').exists() or
              ManualExpense.objects.filter(event=event, is_active=True).exists() or
              IncentiveLine.objects.filter(event=event, is_active=True).exists() or
              CostReview.objects.filter(event=event).exists() or
              AssignmentDay.objects.filter(assignment__event=event, assignment__status='confirmed').exists())
    if not actual:
        return False
    days = Workday.objects.filter(event=event, is_active=True)
    trips = MileageTrip.objects.filter(event=event)
    if days.exclude(approval='approved').exists() or trips.exclude(status__in=('approved', 'void')).exists():
        return True
    recorded = set(days.values_list('staff_id', 'work_date'))
    assigned = set(AssignmentDay.objects.filter(assignment__event=event, assignment__status='confirmed')
                   .values_list('assignment__staff_id', 'date'))
    if assigned - recorded:
        return True
    review = CostReview.objects.filter(event=event).first()
    return not (review and review.manual_reviewed and review.incentives_reviewed and
                review.staff_reviewed and review.source_fingerprint == fingerprint(event))


def _reviewer(category, issues):
    user = policy.reviewer(category)
    if user is None:
        issues.append({'category': category, 'code': 'recipient-not-assigned-or-ineligible'})
    return user


def request_source(record, now, *, issues=None):
    issues = issues if issues is not None else []
    event = record.event
    if not enabled() or record.status != 'pending' or event.status not in ('draft', 'upcoming'):
        return None
    values = policy.values(event)
    if not values['staffing_reviews_enabled']:
        return None
    user = _reviewer('staffing', issues)
    if not user or record.staff.user_id == user.pk:
        return None
    day = local_day(now, event)
    requested = [date.fromisoformat(value) for value in record.requested_dates]
    if day.weekday() >= 5 and not (day in requested and work_on(event, day)):
        return None
    return _source({'type': 'staffing-review', 'id': str(record.pk), 'event': str(event.pk)},
        user, event, str(record.staff), event.name, 'Approvals',
        reverse('staffing:request-decision', args=[record.pk, 'approve']), day,
        record.requested_dates, record.staff_id)


def verification_source(record, now, *, issues=None):
    issues = issues if issues is not None else []
    if not enabled() or record.status != 'pending' or local_day(now).weekday() >= 5:
        return None
    if not policy.values()['verification_reviews_enabled']:
        return None
    from apps.participants.verification_services import latest_verification, qualification_snapshot
    target = record.qualification if record.qualification_id else record.document
    if record.qualification_id:
        if not target.is_active or record.qualification_snapshot != qualification_snapshot(target):
            return None
        kind = 'qualification'
        latest = latest_verification(qualification=target)
    else:
        if target.state != 'active':
            return None
        kind = 'document'
        latest = latest_verification(document=target)
    if latest is None or latest.pk != record.pk:
        return None
    user = _reviewer('verification', issues)
    if not user:
        return None
    evidence = record.evidence_document if record.evidence_document_id else record.document
    if evidence and evidence.state != 'active':
        return None
    if not user.has_perm('participants.view_participant') or (evidence and not user.has_perm('participants.view_participantdocument')):
        issues.append({'category': 'verification', 'code': 'recipient-source-access'})
        return None
    return _source({'type': 'verification', 'id': str(record.pk)}, user, None,
        str(record.participant), 'Verification to review', 'Approvals',
        reverse('participants:staff-qualification-approval-detail', args=[kind, target.pk]),
        local_day(now), target.pk, record.pk)


def job_source(revision, now, *, issues=None):
    issues = issues if issues is not None else []
    need = revision.need
    if (not enabled() or local_day(now).weekday() >= 5 or revision.state not in ('submitted', 'review') or
            need.working_revision_id != revision.pk or need.status == 'cancelled' or
            need.organization.status == 'archived' or not policy.values()['job_reviews_enabled']):
        return None
    user = _reviewer('jobs', issues)
    if not user:
        return None
    return _source({'type': 'job-review', 'id': str(revision.pk)}, user, None,
        revision.title or need.organization.name, need.organization.name, 'Approvals',
        reverse('customers:need', args=[need.organization_id, need.pk]), local_day(now), revision.pk)


def campaign_source(campaign, now, *, issues=None):
    issues = issues if issues is not None else []
    if (not enabled() or local_day(now).weekday() >= 5 or
            campaign.review_state not in ('submitted', 'review') or
            campaign.submitted_planning_version != campaign.planning_version or
            campaign.status in ('completed', 'cancelled') or campaign.organization.status == 'archived' or
            not policy.values()['customer_reviews_enabled']):
        return None
    user = _reviewer('customers', issues)
    if not user:
        return None
    return _source({'type': 'customer-review', 'id': str(campaign.pk)}, user, None,
        campaign.name, campaign.organization.name, 'Approvals',
        reverse('customers:campaign', args=[campaign.organization_id, campaign.pk]),
        local_day(now), campaign.planning_version)


def resolve(ref, now, *, cache=None):
    """Allowlisted source IDs are resolved again before delivery and every display."""
    if cache is None:
        return _resolve(ref, now)
    key = ('source', digest(ref))
    if key not in cache:
        cache[key] = _resolve(ref, now, cache=cache)
    return cache[key]


def _resolve(ref, now, *, cache=None):
    if not enabled() or not isinstance(ref, dict):
        return None
    from apps.staffing.models import AvailabilityRequest
    from apps.participants.verification_models import ParticipantVerificationRecord
    from apps.customers.models import WorkforceNeedRevision, Campaign
    kind = ref.get('type')
    try:
        if kind in ('staffing', 'preparation', 'closeout'):
            key = ('event', ref.get('event'))
            if cache is None or key not in cache:
                event = Event.objects.filter(pk=ref.get('event')).first()
                current = {digest(source.ref): source for source in event_sources(event, now)} if event else {}
                if cache is not None:
                    cache[key] = current
            else:
                current = cache[key]
            return current.get(digest(ref))
        adapters = {
            'staffing-review': (AvailabilityRequest, request_source),
            'verification': (ParticipantVerificationRecord, verification_source),
            'job-review': (WorkforceNeedRevision, job_source),
            'customer-review': (Campaign, campaign_source),
        }
        if kind in adapters:
            model, adapter = adapters[kind]
            record = model.objects.filter(pk=ref.get('id')).first()
            source = adapter(record, now) if record else None
            return source if source and source.ref == ref else None
    except (ValidationError, ValueError, TypeError):
        return None
    return None


def owns_task(task):
    """Only the explicit closeout relationship suppresses the task reminder path."""
    return enabled() and EventExecution.objects.filter(closeout_task=task).exists()
