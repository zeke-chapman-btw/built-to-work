from django.db import migrations

RENAMES = {"Qualification Review": "Qualification review", "Ready for Employer": "Ready for employer",
           "Employer Reviewing": "Employer reviewing", "Not Selected": "Not selected"}


def forwards(apps, schema_editor):
    # Only the original default labels change; labels staff have customised stay as they are.
    Stage = apps.get_model("recruiting", "RecruitingStage")
    for old, new in RENAMES.items():
        Stage.objects.filter(label=old).update(label=new)


def backwards(apps, schema_editor):
    Stage = apps.get_model("recruiting", "RecruitingStage")
    for old, new in RENAMES.items():
        Stage.objects.filter(label=new).update(label=old)


class Migration(migrations.Migration):
    dependencies = [("recruiting", "0006_current_preference_choices")]
    operations = [migrations.RunPython(forwards, backwards)]
