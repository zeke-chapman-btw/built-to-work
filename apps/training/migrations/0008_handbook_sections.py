from django.db import migrations, models


def seed(apps, schema_editor):
    from apps.training.handbook_content import HANDBOOK
    Section = apps.get_model("training", "HandbookSection")
    for data in HANDBOOK:
        Section.objects.get_or_create(slug=data["slug"], defaults={k: v for k, v in data.items() if k != "slug"})


class Migration(migrations.Migration):
    dependencies = [("training", "0007_handbook_link")]
    operations = [
        migrations.CreateModel(
            name="HandbookSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=80, unique=True)),
                ("title", models.CharField(max_length=160)),
                ("summary", models.CharField(blank=True, help_text="One line: what this section covers.", max_length=300)),
                ("body", models.TextField(help_text='Start a line with "- " for a bullet (indent 2 spaces to nest), "1. " for a numbered step, "### " for a small heading. Put **two stars** around words to make them bold.')),
                ("audiences", models.JSONField(blank=True, default=list, help_text="Who sees it.")),
                ("guides", models.JSONField(blank=True, default=list, help_text="Step-by-step guides to link at the end.")),
                ("position", models.PositiveIntegerField(default=100, help_text="Lower numbers show first.")),
                ("published", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ("position", "title")},
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
        migrations.DeleteModel(name="TrainingSettings"),
    ]
