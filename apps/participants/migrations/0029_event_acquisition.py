"""People imported from event files joined at that event: record it so 'How they found us' shows 'At an event'."""
from datetime import datetime, time

from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    Record = apps.get_model("participants", "HistoricalEventRecord")
    Acquisition = apps.get_model("participants", "ParticipantAcquisition")
    now = timezone.now()
    first = {}
    for rec in (Record.objects.filter(event__isnull=False).select_related("event")
                .order_by("event_date", "pk")):
        first.setdefault(rec.participant_id, rec)
    existing = {a.participant_id: a for a in Acquisition.objects.filter(participant_id__in=first)}
    for pid, rec in first.items():
        have = existing.get(pid)
        if have and have.channel != "unknown" and have.correction_reason != "Imported from spreadsheet":
            continue
        when = timezone.make_aware(datetime.combine(rec.event_date, time(12))) if rec.event_date else rec.event.start_at
        if when and when > now:
            when = now
        heard = have.online_source if have and have.channel == "online" else ""
        note = "From the event import" + (f" · heard about us: {heard}" if heard else "")
        values = dict(channel="event", event_id=rec.event_id, acquired_at=when, online_source="",
                      provenance=note[:240], correction_reason="Imported from an event file")
        if have:
            Acquisition.objects.filter(pk=have.pk).update(**values)
        else:
            Acquisition.objects.create(participant_id=pid, **values)


class Migration(migrations.Migration):
    dependencies = [("participants", "0028_staff_documents_verified")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
