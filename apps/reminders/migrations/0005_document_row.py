from django.db import migrations


def forwards(apps, schema_editor):
    from apps.reminders.seed import apply
    apply(apps.get_model("reminders", "ReminderRole"), apps.get_model("reminders", "ReminderRule"))


class Migration(migrations.Migration):
    dependencies = [("reminders", "0004_onboarding_row"), ("onboarding", "0004_file_existing_documents")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
