"""Allow only active-manifest-owned SAMPLE hiring history to be reset.

Ordinary submitted history keeps its existing database delete guard. No trigger
or constraint is disabled while the command runs.
"""
from django.db import migrations


def install(apps, schema_editor, *, sample_exception):
    vendor = schema_editor.connection.vendor
    quote = schema_editor.connection.ops.quote_name
    table = quote(apps.get_model("customers", "WorkforceNeedRevision")._meta.db_table)
    audit = quote(apps.get_model("core", "AuditLog")._meta.db_table)
    latest = (f"SELECT id FROM {audit} WHERE source='sample_data_v1' "
              "AND action IN ('sample_data.created','sample_data.reset') "
              "ORDER BY timestamp DESC,id DESC LIMIT 1")
    if vendor == "postgresql":
        owned = "(a.new_data->'owned'->'customers.workforceneedrevision') ? OLD.id::text"
    elif vendor == "sqlite":
        owned = ("EXISTS (SELECT 1 FROM json_each(json_extract(a.new_data, "
                 "'$.owned.\"customers.workforceneedrevision\"')) x "
                 "WHERE replace(x.value,'-','')=OLD.id)")
    else:
        raise RuntimeError("Sample reset supports PostgreSQL and SQLite only.")
    condition = "OLD.state<>'draft'"
    if sample_exception:
        condition += (f" AND NOT (OLD.title LIKE 'SAMPLE %' AND EXISTS (SELECT 1 FROM {audit} a "
                      f"WHERE a.id=({latest}) AND a.action='sample_data.created' AND ({owned})))")
    message = "Retain submitted hiring revision history."
    if vendor == "postgresql":
        schema_editor.execute(
            'CREATE OR REPLACE FUNCTION "b13_revision_delete_fn"() RETURNS trigger LANGUAGE plpgsql '
            f"AS $guard$ BEGIN IF ({condition}) THEN RAISE EXCEPTION USING ERRCODE='23514', "
            f"MESSAGE='{message}'; END IF; RETURN OLD; END; $guard$", params=None)
    else:
        schema_editor.execute('DROP TRIGGER IF EXISTS "b13_revision_delete_delete"')
        schema_editor.execute(
            f'CREATE TRIGGER "b13_revision_delete_delete" BEFORE DELETE ON {table} '
            f"FOR EACH ROW WHEN ({condition}) BEGIN SELECT RAISE(ABORT, '{message}'); END", params=None)


def forward(apps, schema_editor):
    install(apps, schema_editor, sample_exception=True)


def backward(apps, schema_editor):
    install(apps, schema_editor, sample_exception=False)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial"), ("customers", "0003_customer_portal_activation")]
    operations = [migrations.RunPython(forward, backward)]
