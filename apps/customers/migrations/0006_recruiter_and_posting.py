import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def add_columns(apps, schema_editor):
    # Plain ADD COLUMN keeps the integrity triggers on these tables intact
    # (SQLite would otherwise rebuild the tables and trip over them).
    q = schema_editor.quote_name
    pg = schema_editor.connection.vendor == "postgresql"
    uuid = "uuid" if pg else "char(32)"
    false = "false" if pg else "0"
    boolean = "boolean" if pg else "bool"
    schema_editor.execute(f"ALTER TABLE {q('customers_customerorganization')} ADD COLUMN btw_recruiter_id {uuid} NULL "
                          f"REFERENCES {q('accounts_user')} (id) DEFERRABLE INITIALLY DEFERRED")
    schema_editor.execute(f"CREATE INDEX customers_org_btw_recruiter_idx ON {q('customers_customerorganization')} (btw_recruiter_id)")
    schema_editor.execute(f"ALTER TABLE {q('customers_workforceneedrevision')} ADD COLUMN post_to_participants {boolean} NOT NULL DEFAULT {false}")


def drop_columns(apps, schema_editor):
    q = schema_editor.quote_name
    schema_editor.execute("DROP INDEX customers_org_btw_recruiter_idx")
    schema_editor.execute(f"ALTER TABLE {q('customers_customerorganization')} DROP COLUMN btw_recruiter_id")
    schema_editor.execute(f"ALTER TABLE {q('customers_workforceneedrevision')} DROP COLUMN post_to_participants")


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0005_customer_staff_notes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="customerorganization",
                    name="btw_recruiter",
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                            related_name="recruiting_customers", to=settings.AUTH_USER_MODEL,
                                            verbose_name="Built to Work recruiter",
                                            help_text="New positions from this customer go to this person, and they run the recruiting."),
                ),
                migrations.AddField(
                    model_name="workforceneedrevision",
                    name="post_to_participants",
                    field=models.BooleanField(default=False, verbose_name="Post this job for Built to Work participants?",
                        help_text="Built to Work can share a short version of this job with people in our program so they can say they're interested. Your company name and private details aren't shown."),
                ),
            ],
            database_operations=[migrations.RunPython(add_columns, drop_columns)],
        ),
    ]
