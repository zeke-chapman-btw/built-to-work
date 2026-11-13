from django.db import migrations, models


def add_column(apps, schema_editor):
    table = schema_editor.quote_name("participants_registrationformversion")
    kind = "jsonb NOT NULL DEFAULT '{}'::jsonb" if schema_editor.connection.vendor == "postgresql" else "text NOT NULL DEFAULT '{}'"
    # A database default keeps older code paths (and migration tests) able to insert rows.
    schema_editor.execute(f"ALTER TABLE {table} ADD COLUMN screens {kind}")


def drop_column(apps, schema_editor):
    table = schema_editor.quote_name("participants_registrationformversion")
    schema_editor.execute(f"ALTER TABLE {table} DROP COLUMN screens")


class Migration(migrations.Migration):

    dependencies = [
        ("participants", "0022_primary_identifier_provenance"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.AddField(model_name="registrationformversion", name="screens",
                                                  field=models.JSONField(blank=True, default=dict))],
            database_operations=[migrations.RunPython(add_column, drop_column)],
        ),
    ]
