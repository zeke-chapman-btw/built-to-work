"""Source snoozes extend the existing preference, without changing source records."""
from datetime import timedelta, timezone as datetime_timezone

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.core.audit import record_audit
from .event_reminder_calendar import local_day, scheduled_morning
from .models import TaskReminderPreference
from .reminder_preferences import current_user, preference


def snoozed(source, user, now):
    obj = preference(user)
    raw = (obj.snoozes or {}).get('source:' + source.identity) if obj else None
    try:
        until = parse_datetime(raw) if raw else None
        return bool(until and until.tzinfo and until > now)
    except (ValueError, TypeError):
        return False


def next_morning(source, now):
    from .event_reminder_sources import work_on
    day = local_day(now, source.event) + timedelta(days=1)
    while day.weekday() >= 5 and not (source.event and source.ref['type'] in
            ('staffing', 'preparation', 'staffing-review') and work_on(source.event, day)):
        day += timedelta(days=1)
    return scheduled_morning(day, source.event)


@transaction.atomic
def snooze_sources(*, actor, items, choice, now):
    from .event_reminder_sources import resolve
    actor = current_user(actor, lock=True)
    if choice not in ('hour', 'morning'):
        raise ValidationError('Choose one hour or the next morning.')
    sources = [resolve(item.source_ref, now) for item in items]
    if not sources or any(source is None or source.user.pk != actor.pk for source in sources):
        raise PermissionDenied
    obj, _ = TaskReminderPreference.objects.get_or_create(user=actor)
    retained = {}
    for key, raw in (obj.snoozes or {}).items():
        try:
            value = parse_datetime(raw)
            if value and value.tzinfo and value > now:
                retained[key] = raw
        except (TypeError, ValueError):
            continue
    untils = []
    for source in sources:
        until = (now.astimezone(datetime_timezone.utc) + timedelta(hours=1)
                 if choice == 'hour' else next_morning(source, now))
        retained['source:' + source.identity] = until.isoformat()
        untils.append(until)
    obj.snoozes = retained
    obj.save(update_fields=['snoozes', 'updated_at'])
    record_audit(actor=actor, action='operations.reminders.sources_snoozed', instance=obj,
        new_data={'sources': [source.ref for source in sources], 'until': max(untils).isoformat()},
        source='staff', reason='Reminder snoozed.')
    return max(untils)
