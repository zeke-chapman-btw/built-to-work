from django.db import migrations


def seed_categories(apps, schema_editor):
    Category = apps.get_model("participants", "ParticipantDocumentCategory")
    for position, (slug, name) in enumerate((("resume", "Resume"), ("license-cdl", "License / CDL"), ("certification", "Certification"), ("training-education", "Training / education"), ("other-supporting", "Other supporting record"))):
        if not Category.objects.filter(slug=slug).exists() and not Category.objects.filter(name__iexact=name).exists():
            Category.objects.create(slug=slug, name=name, position=position)


class Migration(migrations.Migration):
    dependencies = [("participants", "0013_participantqualification_expiration_date_and_more")]
    operations = [migrations.RunPython(seed_categories, migrations.RunPython.noop)]
