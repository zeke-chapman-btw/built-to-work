"""Build 14 forward upgrade: retain Build 13 guards, replace only auth policy."""
from importlib import import_module

legacy = import_module('apps.customers.migrations.0002_integrity_guards')


def remove_legacy(apps, editor):
    legacy.uninstall(apps, editor)


def seed_campaign_reviews(apps, editor):
    from django.db.models import F
    apps.get_model('customers', 'Campaign').objects.filter(
        approved_planning_version=F('planning_version'), approved_by__isnull=False,
        approved_at__isnull=False).update(review_state='approved')


def restore_legacy(apps, editor):
    legacy.install(apps, editor)


def uninstall(apps, editor):
    """Permit existing empty test-database migration checks, never undo live access."""
    from django.db.migrations.exceptions import IrreversibleError
    if (apps.get_model('customers', 'CustomerIdentity').objects.filter(activated_at__isnull=False).exists()
            or apps.get_model('customers', 'CustomerMembership').objects.filter(accepted_at__isnull=False).exists()
            or apps.get_model('customers', 'CustomerAccessToken').objects.exists()
            or apps.get_model('customers', 'CustomerFeedback').objects.exists()):
        raise IrreversibleError('Build 14 customer access/history exists. Retain the forward migration; use an approved forward repair.')
    q = editor.connection.ops.quote_name
    with editor.connection.cursor() as cursor:
        if editor.connection.vendor == 'sqlite':
            cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'b14_%'")
            for (name,) in cursor.fetchall():
                editor.execute('DROP TRIGGER ' + q(name))
        else:
            cursor.execute("SELECT t.tgname,c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE NOT t.tgisinternal AND t.tgname LIKE 'b14_%'")
            for name, table_name in cursor.fetchall():
                editor.execute(f'DROP TRIGGER {q(name)} ON {q(table_name)}')
                editor.execute(f'DROP FUNCTION {q(name+"_fn")}()')
    legacy.uninstall(apps, editor)


def install(apps, editor):
    vendor = editor.connection.vendor
    if vendor not in {'sqlite', 'postgresql'}:
        raise RuntimeError('Customer integrity requires PostgreSQL or SQLite.')
    q = editor.connection.ops.quote_name
    scopes, rules = legacy.definitions(apps, vendor)
    identity = apps.get_model('customers', 'CustomerIdentity')
    user = apps.get_model('accounts', 'User')
    token = apps.get_model('customers', 'CustomerAccessToken')
    member = apps.get_model('customers', 'CustomerMembership')
    feedback = apps.get_model('customers', 'CustomerFeedback')
    campaign = apps.get_model('customers', 'Campaign')
    scope = apps.get_model('customers', 'CustomerAuditScope')
    table = lambda model: q(model._meta.db_table)
    false = 'FALSE' if vendor == 'postgresql' else '0'
    different = lambda cols: ' OR '.join(f'NEW.{q(c)} IS DISTINCT FROM OLD.{q(c)}' if vendor == 'postgresql' else f'NEW.{q(c)} IS NOT OLD.{q(c)}' for c in cols)
    replaced = []
    for name, tab, events, condition, message, prelude in rules:
        if name == 'b13_identity_auth':
            # New identities must still start without credentials. Only a verified
            # invitation can subsequently activate the already-linked identity.
            linked = condition.split(' OR EXISTS', 1)[1]
            linked = 'EXISTS' + linked
            safe_user = (f'EXISTS (SELECT 1 FROM {table(user)} u WHERE u.id=NEW.user_id '
                f'AND u.is_staff={false} AND u.is_superuser={false} AND u.email=\'\' '
                f'AND (NEW.activated_at IS NOT NULL OR (u.is_active={false} AND substr(u.password,1,1)=\'!\')))')
            condition = 'NOT (' + safe_user + ') OR ' + linked
            message = 'Customer credentials require verified activation and remain isolated from staff and participant grants.'
        elif name == 'b13_user_auth':
            condition = (f'EXISTS (SELECT 1 FROM {table(identity)} c WHERE c.user_id=NEW.id AND '
                f'(NEW.is_staff<>{false} OR NEW.is_superuser<>{false} OR NEW.email<>\'\' OR '
                f'(c.activated_at IS NULL AND (NEW.is_active<>{false} OR substr(NEW.password,1,1)<>\'!\'))))')
            message = 'Customer accounts cannot receive staff authority or credentials before verified activation.'
        replaced.append((name, tab, events, condition, message, prelude))
    rules = replaced

    def rule(name, model, events, condition, message):
        rules.append(('b14_' + name, model._meta.db_table, events, condition, message, ''))

    rule('identity_new', identity, ['INSERT'], f"EXISTS (SELECT 1 FROM {table(user)} u WHERE u.id=NEW.user_id AND (u.is_active<>{false} OR substr(u.password,1,1)<>'!')) OR NEW.activated_at IS NOT NULL", 'Prepare a separate inactive customer identity before activation.')
    rule('identity_verified', identity, ['UPDATE'], "OLD.activated_at IS NOT NULL AND (" + different(['activated_at', 'email']) + ')', 'Verified customer identity and email history cannot be replaced.')
    rule('token_member', token, ['INSERT', 'UPDATE'], f'NEW.membership_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {table(member)} m WHERE m.id=NEW.membership_id AND m.identity_id=NEW.identity_id)', 'Invitation membership must belong to the invited identity.')
    rule('token_identity', token, ['UPDATE'], different(['id', 'identity_id', 'membership_id', 'purpose', 'token_digest', 'email', 'security_revision', 'expires_at', 'created_at']), 'Issued invitation and recovery identities are immutable.')
    rule('token_consumed', token, ['UPDATE'], '(OLD.consumed_at IS NOT NULL AND (' + different(['consumed_at', 'revoked_at']) + ')) OR (OLD.revoked_at IS NOT NULL AND (' + different(['consumed_at', 'revoked_at']) + '))', 'Used and revoked links cannot be reactivated.')
    rule('token_delete', token, ['DELETE'], '1=1', 'Retain invitation history.')
    for name, fk, parent in [('feedback_need', 'workforce_need_id', apps.get_model('customers', 'WorkforceNeed')), ('feedback_campaign', 'campaign_id', campaign)]:
        rule(name, feedback, ['INSERT', 'UPDATE'], f'NEW.{fk} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {table(parent)} p WHERE p.id=NEW.{fk} AND p.organization_id=NEW.organization_id)', 'Feedback belongs to another company.')
    rule('feedback_audit', feedback, ['INSERT'], f'NOT EXISTS (SELECT 1 FROM {table(scope)} s WHERE s.organization_id=NEW.organization_id AND s.audit_id=NEW.audit_id)', 'Feedback requires authoritative company audit scope.')
    rule('feedback_immutable', feedback, ['UPDATE'], different([f.column for f in feedback._meta.local_fields if f.column != 'updated_at']), 'Published feedback is retained unchanged.')
    rule('feedback_delete', feedback, ['DELETE'], '1=1', 'Retain published feedback history.')
    rule('campaign_submission', campaign, ['INSERT', 'UPDATE'], "NEW.review_state IN ('submitted','review') AND (NEW.submitted_planning_version IS NULL OR NEW.submitted_planning_version<>NEW.planning_version OR NEW.submitted_by_id IS NULL OR NEW.submitted_at IS NULL)", 'Campaign review must reference its current submitted planning version.')
    rule('campaign_review_state', campaign, ['INSERT', 'UPDATE'], "NEW.review_state NOT IN ('draft','submitted','review','returned','approved')", 'Invalid Campaign review state.')
    rule('member_acceptance', member, ['UPDATE'], 'OLD.accepted_at IS NOT NULL AND (' + different(['accepted_at']) + ')', 'Retain membership acceptance history.')
    if vendor == 'postgresql':
        for name, child, fk, child_scope, parent, parent_scope in scopes:
            editor.execute(f'ALTER TABLE {q(child._meta.db_table)} ADD CONSTRAINT {q("b13_fk_"+name)} FOREIGN KEY ({q(child_scope)}, {q(fk)}) REFERENCES {q(parent._meta.db_table)} ({q(parent_scope)}, id) DEFERRABLE INITIALLY DEFERRED')
    for name, tab, events, condition, message, prelude in rules:
        message = message.replace("'", "''")
        if vendor == 'postgresql':
            editor.execute(f"CREATE FUNCTION {q(name+'_fn')}() RETURNS trigger LANGUAGE plpgsql AS $guard$ BEGIN {prelude} IF ({condition}) THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='{message}'; END IF; IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF; END; $guard$")
            editor.execute(f'CREATE TRIGGER {q(name)} BEFORE {" OR ".join(events)} ON {q(tab)} FOR EACH ROW EXECUTE FUNCTION {q(name+"_fn")}()')
        else:
            for event in events:
                editor.execute(f"CREATE TRIGGER {q(name+'_'+event.lower())} BEFORE {event} ON {q(tab)} FOR EACH ROW WHEN ({condition}) BEGIN SELECT RAISE(ABORT, '{message}'); END")
