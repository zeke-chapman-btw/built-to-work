from django.db import migrations, models

FREQUENCY_CHOICES = [
    ("weekly", "Weekly"),
    ("biweekly", "Every two weeks"),
    ("semimonthly", "Twice a month"),
    ("monthly", "Monthly"),
]


def add_column(apps, schema_editor):
    # A plain ADD COLUMN keeps the integrity triggers on this table intact
    # (SQLite would otherwise rebuild the table and trip over them).
    table = schema_editor.quote_name("customers_workforceneedrevision")
    schema_editor.execute(f"ALTER TABLE {table} ADD COLUMN pay_frequency varchar(12) NOT NULL DEFAULT ''")


def drop_column(apps, schema_editor):
    table = schema_editor.quote_name("customers_workforceneedrevision")
    schema_editor.execute(f"ALTER TABLE {table} DROP COLUMN pay_frequency")


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0003_customer_portal_activation"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="workforceneedrevision",
                    name="pay_frequency",
                    field=models.CharField(blank=True, choices=FREQUENCY_CHOICES, max_length=12),
                ),
            ],
            database_operations=[migrations.RunPython(add_column, drop_column)],
        ),
    ]
