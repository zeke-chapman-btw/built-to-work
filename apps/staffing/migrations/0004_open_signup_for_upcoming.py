from django.db import migrations
from django.utils import timezone


def open_upcoming(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    Plan = apps.get_model("staffing", "StaffingPlan")
    for event in Event.objects.filter(status="upcoming", end_at__gte=timezone.now()):
        plan = Plan.objects.filter(event=event).first()
        if plan is None:
            Plan.objects.create(event=event, is_published=True)
        elif not plan.is_published:
            plan.is_published = True
            plan.save(update_fields=["is_published"])


class Migration(migrations.Migration):
    dependencies = [("staffing", "0003_staffprofile_my_pay_enabled"), ("events", "0020_event_map_pin")]
    operations = [migrations.RunPython(open_upcoming, migrations.RunPython.noop)]
