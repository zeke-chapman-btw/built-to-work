from django.db import migrations, models

TRADES = [("electrical", "Electrical"), ("welding", "Welding/Fabrication"), ("hvac", "HVAC"),
          ("plumbing", "Plumbing/Pipefitting"), ("carpentry", "Carpentry"), ("equipment", "Heavy Equipment"),
          ("diesel", "Diesel/Automotive"), ("maintenance", "Industrial Maintenance"),
          ("management", "Construction Management")]


def seed(apps, schema_editor):
    Classification = apps.get_model("recruiting", "Classification")
    for position, (key, label) in enumerate(TRADES):
        Classification.objects.get_or_create(kind="trade", key=key, defaults={"label": label, "position": position * 10, "is_active": True})


def unseed(apps, schema_editor):
    apps.get_model("recruiting", "Classification").objects.filter(kind="trade").delete()


class Migration(migrations.Migration):
    dependencies = [("recruiting", "0007_sentence_case_stage_labels")]
    operations = [
        # Choices only; the column is unchanged, so no table rebuild.
        migrations.SeparateDatabaseAndState(state_operations=[migrations.AlterField(
            model_name="classification", name="kind",
            field=models.CharField(max_length=16, choices=[("employment", "Employment"), ("availability", "Availability"), ("trade", "Trade")]))]),
        migrations.RunPython(seed, unseed),
    ]
