from django.db import migrations


def forwards(apps, schema_editor):
    from apps.reminders.seed import apply
    apply(apps.get_model("reminders", "ReminderRole"), apps.get_model("reminders", "ReminderRule"))


class Migration(migrations.Migration):
    dependencies = [("reminders", "0006_delivery_row"), ("recruiting", "0009_request_withdrawn")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
