from django.db import migrations, models


def access_field(model=None):
    field = models.BooleanField(default=False, db_default=False, help_text="Allow this person to view only their own earnings statements.")
    field.set_attributes_from_name('my_pay_enabled')
    if model is not None:
        field.model = model
    return field


def add_access(apps, schema_editor):
    profile = apps.get_model('staffing', 'StaffProfile')
    if schema_editor.connection.vendor == 'sqlite':
        # SQLite supports native ADD COLUMN. Rebuilding StaffProfile would
        # temporarily invalidate the cross-table customer identity guards.
        q = schema_editor.quote_name
        schema_editor.execute(f'ALTER TABLE {q(profile._meta.db_table)} ADD COLUMN {q("my_pay_enabled")} bool NOT NULL DEFAULT 0')
    else:
        schema_editor.add_field(profile, access_field(profile))


def remove_access(apps, schema_editor):
    profile = apps.get_model('staffing', 'StaffProfile')
    if schema_editor.connection.vendor == 'sqlite':
        q = schema_editor.quote_name
        schema_editor.execute(f'ALTER TABLE {q(profile._meta.db_table)} DROP COLUMN {q("my_pay_enabled")}')
    else:
        schema_editor.remove_field(profile, access_field(profile))


class Migration(migrations.Migration):
    dependencies = [("staffing", "0002_daily_role_coverage")]
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunPython(add_access, remove_access)],
        state_operations=[migrations.AddField(
            model_name="staffprofile", name="my_pay_enabled", field=access_field(),
        )],
    )]
