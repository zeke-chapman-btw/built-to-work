import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def forwards(apps, schema_editor):
    from apps.reminders.seed import apply
    apply(apps.get_model("reminders", "ReminderRole"), apps.get_model("reminders", "ReminderRule"))


class Migration(migrations.Migration):
    dependencies = [("reminders", "0007_gap_rows"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name="reminderrule", name="conditions",
                            field=models.JSONField(blank=True, default=dict, help_text="Only items that match these go out.")),
        migrations.CreateModel(
            name="ReminderPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bell", models.BooleanField(default=True)),
                ("email", models.BooleanField(default=False)),
                ("text", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("rule", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="preferences", to="reminders.reminderrule")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reminder_preferences", to=settings.AUTH_USER_MODEL)),
            ],
            options={"default_permissions": ()},
        ),
        migrations.AddConstraint(model_name="reminderpreference",
                                 constraint=models.UniqueConstraint(fields=("user", "rule"), name="reminders_one_preference")),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
