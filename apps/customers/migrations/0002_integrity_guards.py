"""Scoped references and immutable/customer-auth invariants on PostgreSQL and SQLite.

These are integrity guards, not audit writers or a second workflow engine.
Aggregate seat policy is serialized by the organization service lock, allowing
the explicitly approved over-cap reductions and seat-neutral role swaps.
"""
from django.db import migrations, models
from django.db.models import Q


def definitions(apps, vendor):
    get = lambda name: apps.get_model("customers", name)
    org, member, contact = get("CustomerOrganization"), get("CustomerMembership"), get("CustomerContact")
    campaign, need, revision = get("Campaign"), get("WorkforceNeed"), get("WorkforceNeedRevision")
    location, identity, plan, scope = get("CustomerLocation"), get("CustomerIdentity"), get("CustomerAccessPlan"), get("CustomerAuditScope")
    user = apps.get_model("accounts", "User")
    participant = apps.get_model("participants", "ParticipantAccount")
    staff = apps.get_model("staffing", "StaffProfile")
    invitation = apps.get_model("accounts", "StaffInvitation")
    q = lambda table: '"' + table + '"'
    table = lambda model: q(model._meta.db_table)
    differing = lambda columns: " OR ".join(f'NEW."{c}" IS DISTINCT FROM OLD."{c}"' if vendor == "postgresql" else f'NEW."{c}" IS NOT OLD."{c}"' for c in columns)
    scopes = [
        ("org_primary", org, "primary_lead_id", "id", member, "organization_id"),
        ("org_contact", org, "primary_contact_id", "id", contact, "organization_id"),
        ("member_contact", member, "contact_id", "organization_id", contact, "organization_id"),
        ("campaign_contact", campaign, "primary_contact_id", "organization_id", contact, "organization_id"),
        ("need_campaign", need, "campaign_id", "organization_id", campaign, "organization_id"),
        ("revision_need", revision, "need_id", "organization_id", need, "organization_id"),
        ("revision_location", revision, "location_id", "organization_id", location, "organization_id"),
        ("revision_contact", revision, "primary_contact_id", "organization_id", contact, "organization_id"),
        ("need_effective", need, "effective_revision_id", "id", revision, "need_id"),
        ("need_working", need, "working_revision_id", "id", revision, "need_id"),
        ("history_need", scope, "workforce_need_id", "organization_id", need, "organization_id"),
        ("history_campaign", scope, "campaign_id", "organization_id", campaign, "organization_id"),
    ]
    rules = []

    def rule(name, model, events, condition, message, prelude=""):
        rules.append(("b13_" + name, model._meta.db_table, events, condition, message, prelude))

    if vendor == "sqlite":
        for name, child, fk, child_scope, parent, parent_scope in scopes:
            condition = (f'NEW."{fk}" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {table(parent)} '
                         f'WHERE id=NEW."{fk}" AND "{parent_scope}"=NEW."{child_scope}")')
            rule("fk_" + name, child, ["INSERT", "UPDATE"], condition, "Customer relationship belongs to another organization or record.")

    valid_primary = (f"NEW.provisioned AND NOT EXISTS (SELECT 1 FROM {table(member)} m "
                     "WHERE m.id=NEW.primary_lead_id AND m.organization_id=NEW.id AND m.role='lead' AND m.state='enabled')")
    rule("primary_valid", org, ["INSERT", "UPDATE"], valid_primary, "A provisioned organization requires its own enabled Primary Lead.")
    rule("primary_member", member, ["UPDATE"],
         f"EXISTS (SELECT 1 FROM {table(org)} o WHERE o.primary_lead_id=OLD.id) AND (NEW.role<>'lead' OR NEW.state<>'enabled' OR " + differing(["organization_id", "identity_id"]) + ")",
         "Replace the Primary Lead before changing or suspending that membership.")
    for name, state_condition in [("effective", "r.state='approved'"), ("working", "r.state IN ('draft','submitted','review')")]:
        column = name + "_revision_id"
        rule("need_" + name, need, ["INSERT", "UPDATE"],
             f"NEW.{column} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {table(revision)} r WHERE r.id=NEW.{column} AND r.need_id=NEW.id AND {state_condition})",
             "Choose a valid revision of this Workforce Need.")
    rule("revision_pointer", revision, ["UPDATE"],
         f"NEW.state NOT IN ('draft','submitted','review') AND EXISTS (SELECT 1 FROM {table(need)} n WHERE n.working_revision_id=NEW.id)",
         "Clear the working pointer before freezing its historical revision.")

    columns = [f.column for f in revision._meta.local_fields]
    mutable_review = {"state", "reviewed_by_id", "approved_at", "approved_by_id", "decision_reason", "updated_at"}
    rule("revision_payload", revision, ["UPDATE"],
         "OLD.state<>'draft' AND (" + differing([c for c in columns if c not in mutable_review]) + ")",
         "Submitted hiring information is immutable; create another revision.")
    rule("revision_final", revision, ["UPDATE"],
         "OLD.state IN ('approved','returned','withdrawn') AND (" + differing([c for c in columns if c != "updated_at"]) + ")",
         "Historical hiring revisions are immutable.")
    rule("revision_identity", revision, ["UPDATE"], differing(["id", "need_id", "organization_id", "number", "created_at"]), "Revision identity cannot be changed.")
    rule("revision_transition", revision, ["UPDATE"],
         "(OLD.state='draft' AND NEW.state NOT IN ('draft','submitted','withdrawn')) OR "
         "(OLD.state='submitted' AND NEW.state NOT IN ('submitted','review','returned','withdrawn')) OR "
         "(OLD.state='review' AND NEW.state NOT IN ('review','approved','returned','withdrawn'))",
         "Invalid hiring revision transition.")
    rule("revision_delete", revision, ["DELETE"], "OLD.state<>'draft'", "Retain submitted hiring revision history.")

    for name, model in [("location", location), ("contact", contact), ("member", member), ("plan", plan), ("campaign", campaign), ("need", need), ("scope", scope)]:
        cols = ["organization_id"]
        if name == "member":
            cols.append("identity_id")
        if name == "scope":
            cols += ["audit_id", "workforce_need_id", "campaign_id"]
        rule(name + "_owner", model, ["UPDATE"], differing(cols), "Customer records cannot be moved or relinked across identity/organization boundaries.")

    planning = ["name", "description", "objectives", "start_date", "end_date", "primary_contact_id", "contact_name"]
    rule("campaign_plan", campaign, ["UPDATE"],
         "(" + differing(planning) + ") AND (NEW.planning_version<>OLD.planning_version+1 OR NEW.approved_planning_version IS NOT NULL OR NEW.approved_at IS NOT NULL OR NEW.approved_by_id IS NOT NULL OR NEW.status='active')",
         "Changed Campaign planning details require a new unapproved planning version.")

    false = "FALSE" if vendor == "postgresql" else "0"
    user_ok = (f"EXISTS (SELECT 1 FROM {table(user)} u WHERE u.id=NEW.user_id AND u.is_active={false} "
               f"AND u.is_staff={false} AND u.is_superuser={false} AND substr(u.password,1,1)='!' AND u.email='')")
    linked = " OR ".join(f"EXISTS (SELECT 1 FROM {table(m)} x WHERE x.user_id=NEW.user_id)" for m in [participant, staff, invitation])
    through = [user._meta.get_field("groups").remote_field.through,
               user._meta.get_field("user_permissions").remote_field.through]
    linked += " OR " + " OR ".join(f"EXISTS (SELECT 1 FROM {table(m)} x WHERE x.user_id=NEW.user_id)" for m in through)
    lock = f"PERFORM 1 FROM {table(user)} WHERE id=NEW.user_id FOR UPDATE;" if vendor == "postgresql" else ""
    rule("identity_auth", identity, ["INSERT", "UPDATE"], "NOT (" + user_ok + ") OR " + linked,
         "Prepare a new inactive, non-staff customer identity without credentials, grants or participant links.", lock)
    rule("identity_user", identity, ["UPDATE"], differing(["user_id"]), "Customer authentication identity cannot be replaced.")
    rule("identity_delete", identity, ["DELETE"], "1=1", "Retain customer identity history; suspend memberships instead.")
    rule("member_delete", member, ["DELETE"], "1=1", "Remove or suspend memberships without deleting history.")
    rule("user_auth", user, ["UPDATE"],
         f"EXISTS (SELECT 1 FROM {table(identity)} c WHERE c.user_id=NEW.id) AND (NEW.is_active<>{false} OR NEW.is_staff<>{false} OR NEW.is_superuser<>{false} OR substr(NEW.password,1,1)<>'!' OR NEW.email<>'')",
         "Customer authentication is inactive and cannot become staff or participant authentication in Build 13.")
    for name, model in [("participant", participant), ("staff", staff), ("invitation", invitation), ("group", through[0]), ("permission", through[1])]:
        rule("customer_no_" + name, model, ["INSERT", "UPDATE"],
             f"EXISTS (SELECT 1 FROM {table(identity)} c WHERE c.user_id=NEW.user_id)",
             "Customer identities cannot receive staff/participant links, grants or invitations.", lock)
    return scopes, rules


def install(apps, schema_editor):
    connection = schema_editor.connection
    vendor = connection.vendor
    if vendor not in {"sqlite", "postgresql"}:
        raise RuntimeError("Build 13 integrity guards require PostgreSQL or SQLite.")
    quote = connection.ops.quote_name
    scopes, rules = definitions(apps, vendor)
    if vendor == "postgresql":
        for name, child, fk, child_scope, parent, parent_scope in scopes:
            schema_editor.execute(f'ALTER TABLE {quote(child._meta.db_table)} ADD CONSTRAINT {quote("b13_fk_" + name)} FOREIGN KEY ({quote(child_scope)}, {quote(fk)}) REFERENCES {quote(parent._meta.db_table)} ({quote(parent_scope)}, id) DEFERRABLE INITIALLY DEFERRED')
    for name, table, events, condition, message, prelude in rules:
        message = message.replace("'", "''")
        if vendor == "postgresql":
            schema_editor.execute(f"CREATE FUNCTION {quote(name + '_fn')}() RETURNS trigger LANGUAGE plpgsql AS $guard$ BEGIN {prelude} IF ({condition}) THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='{message}'; END IF; IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF; END; $guard$")
            schema_editor.execute(f'CREATE TRIGGER {quote(name)} BEFORE {" OR ".join(events)} ON {quote(table)} FOR EACH ROW EXECUTE FUNCTION {quote(name + "_fn")}()')
        else:
            for event in events:
                schema_editor.execute(f"CREATE TRIGGER {quote(name + '_' + event.lower())} BEFORE {event} ON {quote(table)} FOR EACH ROW WHEN ({condition}) BEGIN SELECT RAISE(ABORT, '{message}'); END")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    quote = schema_editor.connection.ops.quote_name
    scopes, rules = definitions(apps, vendor)
    for name, table, events, *_ in reversed(rules):
        if vendor == "postgresql":
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {quote(name)} ON {quote(table)}')
            schema_editor.execute(f'DROP FUNCTION IF EXISTS {quote(name + "_fn")}()')
        else:
            for event in events:
                schema_editor.execute(f'DROP TRIGGER IF EXISTS {quote(name + "_" + event.lower())}')
    if vendor == "postgresql":
        for name, child, *_ in scopes:
            schema_editor.execute(f'ALTER TABLE {quote(child._meta.db_table)} DROP CONSTRAINT IF EXISTS {quote("b13_fk_" + name)}')


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0001_initial"),
        ("accounts", "0004_security_revision_database_default"),
        ("participants", "0020_participant_profile_entries"),
        ("staffing", "0002_daily_role_coverage"),
    ]
    operations = [
        migrations.AddConstraint(
            model_name="workforceneedrevision",
            constraint=models.CheckConstraint(
                condition=~Q(state__in=["submitted", "review", "approved", "returned"]) | Q(submitted_at__isnull=False, submitted_by__isnull=False),
                name="customer_submission_recorded",
            ),
        ),
        migrations.RunPython(install, uninstall),
    ]
