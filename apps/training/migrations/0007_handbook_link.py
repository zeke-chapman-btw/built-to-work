from django.db import migrations, models


def seed(apps, schema_editor):
    Settings = apps.get_model("training", "TrainingSettings")
    Settings.objects.get_or_create(pk=1, defaults={
        "handbook_url": "https://docs.google.com/document/d/134HomUnNMJNtTioqPaCS7hoZTEHZYno-AczZpFchXzE/edit"})


class Migration(migrations.Migration):
    dependencies = [("training", "0006_job_connection_changes")]
    operations = [
        migrations.CreateModel(
            name="TrainingSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("handbook_url", models.URLField(blank=True, verbose_name="Workflow handbook link",
                    help_text="The Google Doc with the app's main workflows. Shown to staff at the top of Help.")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
