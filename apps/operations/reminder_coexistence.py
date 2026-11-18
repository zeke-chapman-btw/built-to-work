"""Share bell coverage for the same submitted job across the two existing senders."""
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.utils import timezone


def settings_job_bell_sent(ref, user, now):
    if ref.get('type') != 'job-review':
        return False
    from apps.reminders.models import ReminderSend
    from apps.reminders.sources import user_target
    target = user_target(user)
    if target is None:
        return False
    day = now.astimezone(ZoneInfo('America/New_York')).date()
    start = datetime.combine(day, time.min, ZoneInfo('America/New_York'))
    return ReminderSend.objects.filter(rule__source='new_position',
        ref='need-revision:' + str(ref['id']), target=target.key, channel='bell', status='sent',
        created_at__gte=start, created_at__lt=start + timedelta(days=1)).exists()


def send_job_bell(rule, target, batch, send):
    """Serialize with 18B's organization/need/revision locks before claiming a bell."""
    from .event_reminder_sources import enabled, job_source
    if not enabled() or not target.user:
        return send(batch)
    from apps.customers.models import CustomerOrganization, WorkforceNeed, WorkforceNeedRevision
    from .models import TaskReminderItem
    ids = {}
    for item, occurrence in batch:
        try:
            prefix, value = item.ref.split(':', 1)
            if prefix == 'need-revision':
                ids[item.ref] = UUID(value)
        except (ValueError, AttributeError):
            continue
    rows = WorkforceNeedRevision.objects.filter(pk__in=ids.values())
    nowait = connection.features.has_select_for_update_nowait
    with transaction.atomic():
        list(CustomerOrganization.objects.select_for_update(nowait=nowait).filter(
            pk__in=rows.values_list('organization_id', flat=True)).order_by('pk'))
        list(WorkforceNeed.objects.select_for_update(nowait=nowait).filter(
            pk__in=rows.values_list('need_id', flat=True)).order_by('pk'))
        current = {row.pk: row for row in rows.select_for_update(nowait=nowait)
            .select_related('need', 'organization').order_by('pk')}
        now = timezone.now()
        remaining = []
        for item, occurrence in batch:
            row = current.get(ids.get(item.ref))
            source = job_source(row, now) if row else None
            if source is not None and source.user.pk == target.user.pk:
                covered = TaskReminderItem.objects.filter(kind='source',
                    dedupe_key=source.coverage(), job__recipient=source.user,
                    job__notification__isnull=False).exists()
                if covered or settings_job_bell_sent(source.ref, source.user, now):
                    continue
            remaining.append((item, occurrence))
        return send(remaining) if remaining else 0
