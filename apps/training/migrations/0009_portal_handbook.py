from django.db import migrations


def seed(apps, schema_editor):
    from apps.training.handbook_content import HANDBOOK
    Section = apps.get_model("training", "HandbookSection")
    for data in HANDBOOK:
        if set(data["audiences"]) & {"customer", "participant"}:
            Section.objects.get_or_create(slug=data["slug"], defaults={k: v for k, v in data.items() if k != "slug"})


class Migration(migrations.Migration):
    dependencies = [("training", "0008_handbook_sections")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
