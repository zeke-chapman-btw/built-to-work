from django.db import migrations


def forwards(apps, schema_editor):
    from apps.training.content import GUIDES
    Guide = apps.get_model("training", "Guide")
    Step = apps.get_model("training", "GuideStep")
    for data in GUIDES:
        if Guide.objects.filter(slug=data["slug"]).exists():
            continue
        steps = data["steps"]
        guide = Guide.objects.create(**{k: v for k, v in data.items() if k != "steps"})
        for i, step in enumerate(steps):
            Step.objects.create(guide=guide, position=i, **step)


class Migration(migrations.Migration):
    dependencies = [("training", "0001_initial")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
