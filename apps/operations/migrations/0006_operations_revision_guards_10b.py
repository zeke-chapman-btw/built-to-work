"""Protect published revision contents and application provenance below the ORM."""
from django.db import migrations

def install_guards(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "sqlite":
        statements = [
            "CREATE TRIGGER ops_revision_no_update BEFORE UPDATE ON operations_operationstemplaterevision WHEN OLD.status = 'published' BEGIN SELECT RAISE(ABORT, 'Published operations revisions are immutable'); END",
            "CREATE TRIGGER ops_revision_no_delete BEFORE DELETE ON operations_operationstemplaterevision WHEN OLD.status = 'published' BEGIN SELECT RAISE(ABORT, 'Published operations revisions are immutable'); END",
            "CREATE TRIGGER ops_resource_no_insert BEFORE INSERT ON operations_operationstemplateresource WHEN EXISTS(SELECT 1 FROM operations_operationstemplaterevision WHERE id = NEW.revision_id AND status = 'published') BEGIN SELECT RAISE(ABORT, 'Published operations resources are immutable'); END",
            "CREATE TRIGGER ops_resource_no_update BEFORE UPDATE ON operations_operationstemplateresource WHEN EXISTS(SELECT 1 FROM operations_operationstemplaterevision WHERE id IN (OLD.revision_id, NEW.revision_id) AND status = 'published') BEGIN SELECT RAISE(ABORT, 'Published operations resources are immutable'); END",
            "CREATE TRIGGER ops_resource_no_delete BEFORE DELETE ON operations_operationstemplateresource WHEN EXISTS(SELECT 1 FROM operations_operationstemplaterevision WHERE id = OLD.revision_id AND status = 'published') BEGIN SELECT RAISE(ABORT, 'Published operations resources are immutable'); END",
            "CREATE TRIGGER ops_application_no_update BEFORE UPDATE ON operations_eventoperationsapplication BEGIN SELECT RAISE(ABORT, 'Operations application history is immutable'); END",
            "CREATE TRIGGER ops_application_no_delete BEFORE DELETE ON operations_eventoperationsapplication BEGIN SELECT RAISE(ABORT, 'Operations application history is immutable'); END",
        ]
    elif vendor == "postgresql":
        statements = [
            """CREATE FUNCTION ops_revision_guard_10b() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
              IF OLD.status = 'published' THEN RAISE EXCEPTION 'Published operations revisions are immutable' USING ERRCODE = '23514'; END IF;
              IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; RETURN NEW; END; $$""",
            "CREATE TRIGGER ops_revision_guard BEFORE UPDATE OR DELETE ON operations_operationstemplaterevision FOR EACH ROW EXECUTE FUNCTION ops_revision_guard_10b()",
            """CREATE FUNCTION ops_resource_guard_10b() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
              IF TG_OP = 'INSERT' THEN PERFORM id FROM operations_operationstemplaterevision WHERE id=NEW.revision_id FOR UPDATE;
              ELSIF TG_OP = 'DELETE' THEN PERFORM id FROM operations_operationstemplaterevision WHERE id=OLD.revision_id FOR UPDATE;
              ELSE PERFORM id FROM operations_operationstemplaterevision WHERE id IN (OLD.revision_id,NEW.revision_id) ORDER BY id FOR UPDATE; END IF;
              IF TG_OP <> 'INSERT' THEN
                IF EXISTS(SELECT 1 FROM operations_operationstemplaterevision WHERE id=OLD.revision_id AND status='published') THEN RAISE EXCEPTION 'Published operations resources are immutable' USING ERRCODE='23514'; END IF;
              END IF;
              IF TG_OP <> 'DELETE' THEN
                IF EXISTS(SELECT 1 FROM operations_operationstemplaterevision WHERE id=NEW.revision_id AND status='published') THEN RAISE EXCEPTION 'Published operations resources are immutable' USING ERRCODE='23514'; END IF;
                RETURN NEW;
              END IF; RETURN OLD; END; $$""",
            "CREATE TRIGGER ops_resource_guard BEFORE INSERT OR UPDATE OR DELETE ON operations_operationstemplateresource FOR EACH ROW EXECUTE FUNCTION ops_resource_guard_10b()",
            """CREATE FUNCTION ops_application_guard_10b() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Operations application history is immutable' USING ERRCODE='23514'; END; $$""",
            "CREATE TRIGGER ops_application_guard BEFORE UPDATE OR DELETE ON operations_eventoperationsapplication FOR EACH ROW EXECUTE FUNCTION ops_application_guard_10b()",
        ]
    else:
        raise RuntimeError("Build 10B revision guards support the established SQLite and PostgreSQL databases only.")
    for statement in statements: schema_editor.execute(statement)

def remove_guards(apps, schema_editor):
    if schema_editor.connection.vendor == "sqlite":
        for name in ("ops_revision_no_update", "ops_revision_no_delete", "ops_resource_no_insert", "ops_resource_no_update", "ops_resource_no_delete", "ops_application_no_update", "ops_application_no_delete"):
            schema_editor.execute("DROP TRIGGER IF EXISTS "+name)
    elif schema_editor.connection.vendor == "postgresql":
        for name, table in (("ops_revision_guard", "operations_operationstemplaterevision"), ("ops_resource_guard", "operations_operationstemplateresource"), ("ops_application_guard", "operations_eventoperationsapplication")):
            schema_editor.execute("DROP TRIGGER IF EXISTS "+name+" ON "+table)
        for name in ("ops_revision_guard_10b", "ops_resource_guard_10b", "ops_application_guard_10b"):
            schema_editor.execute("DROP FUNCTION IF EXISTS "+name+"()")

class Migration(migrations.Migration):
    dependencies = [("operations", "0005_operations_templates_10b")]
    operations = [migrations.RunPython(install_guards, remove_guards)]
