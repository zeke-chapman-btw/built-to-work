"""Populated baseline -> candidate check on an OWNED loopback PostgreSQL database.

Never use Preview/Production credentials. The config file is private and contains
host, port, user, password and name for the isolated Build 16 PostgreSQL server.
This script creates a new database; it refuses to reuse or destroy an existing one.
Only counts and row digests are written to its result artifact, never row values.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile


def private_config(path):
    config = json.loads(Path(path).read_text())
    assert config["host"] == "127.0.0.1" and config["name"] == "btw_build16_base"
    return config


def worker(args):
    config = private_config(args.config)
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    os.chdir(root)
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.test"
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("RENDER_SERVICE_ID", None)
    from django.conf import settings
    settings.DATABASES = {"default": dict(ENGINE="django.db.backends.postgresql", NAME=args.database,
        USER=config["user"], PASSWORD=config["password"], HOST=config["host"], PORT=config["port"])}
    state = Path(args.state).resolve()
    for name in ("MEDIA_ROOT", "PRIVATE_DOCUMENT_ROOT", "PRIVATE_CONSENT_ROOT", "PRIVATE_HISTORICAL_ROOT"):
        setattr(settings, name, state / "private-test-files" / args.database / name.lower())
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connection
    from django.contrib.auth import get_user_model
    if args.mode == "before":
        call_command("migrate", verbosity=0, interactive=False)
        from django.test import TestCase
        from django.utils import timezone
        from apps.recruiting.test_employer import EmployerFixtures
        from apps.recruiting import employer, workflow
        from apps.operations.models import Task, TaskNotification
        from apps.staffing.models import StaffProfile
        from apps.participants.models import ParticipantNote
        from apps.core.models import AuditLog

        class Seed(EmployerFixtures, TestCase):
            pass

        fixture = Seed()
        fixture.employer_setup()
        grant = fixture.release()
        workflow.record_outreach(actor=fixture.admin, candidate_id=fixture.candidate.pk,
            data={"method": "phone", "contacted_at": timezone.now(), "outcome": "SYNTHETIC historical contact"},
            operation_id=__import__("uuid").uuid4(), expected_revision=fixture.candidate.revision)
        fixture.candidate.refresh_from_db()
        reported = fixture.respond(grant, kind="report_hired")
        employer.confirm_hire(actor=fixture.admin, feedback_id=reported.pk,
            expected_candidate_revision=fixture.candidate.revision, why="SYNTHETIC migration-preservation hiring fixture")
        staff = StaffProfile.objects.create(user=fixture.admin, first_name="SYNTHETIC", last_name="Migration owner")
        task = Task.objects.create(title="SYNTHETIC existing task", assigned_staff=staff, created_by=fixture.admin)
        TaskNotification.objects.create(task=task, recipient=fixture.admin, source_event=AuditLog.objects.first(),
            kind="assigned", message="SYNTHETIC retained notification")
        ParticipantNote.objects.create(participant=fixture.person, author=fixture.admin, content="SYNTHETIC existing private note")
    elif args.mode == "after":
        call_command("migrate", verbosity=0, interactive=False)
    elif args.mode == "legacy-insert":
        from apps.participants.models import Participant
        from apps.customers.models import CustomerContact, CustomerOrganization
        Participant.objects.create(first_name="SYNTHETIC", last_name="Old ORM after upgrade")
        organization = CustomerOrganization.objects.first()
        CustomerContact.objects.create(organization=organization, first_name="SYNTHETIC", last_name="Old contact ORM")
        print("Legacy baseline ORM inserts passed")
        return

    tables = {}
    with connection.cursor() as cursor:
        for table in connection.introspection.table_names(cursor):
            cursor.execute("SELECT * FROM " + connection.ops.quote_name(table))
            rows = cursor.fetchall()
            def value_json(value):
                if isinstance(value, (bytes, memoryview)):
                    return bytes(value).hex()
                return str(value)
            digests = sorted(hashlib.sha256(json.dumps(row, default=value_json, sort_keys=True, separators=(",", ":")).encode()).hexdigest() for row in rows)
            tables[table] = {"count": len(rows), "rows": digests,
                "digest": hashlib.sha256(json.dumps(digests).encode()).hexdigest()}
    (state / (args.database + "-" + args.mode + ".json")).write_text(json.dumps(tables, indent=2))
    print(args.mode + " populated table digests captured: " + str(len(tables)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--candidate", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--baseline", default="f017bbb490c9a8f3300b913365bf11832c6a4a6b")
    parser.add_argument("--database", required=True)
    parser.add_argument("--root")
    parser.add_argument("--mode", choices=("before", "after", "legacy-insert"))
    args = parser.parse_args()
    assert re.fullmatch(r"btw_build16_preserve_[a-z0-9_]+", args.database), "Use a fresh owned preservation database."
    if args.mode:
        worker(args)
        return
    config = private_config(args.config)
    import psycopg
    from psycopg import sql
    with psycopg.connect(host=config["host"], port=config["port"], dbname=config["name"], user=config["user"],
                        password=config["password"], autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (args.database,)).fetchone():
            raise RuntimeError("Preservation database already exists; choose a new name. Nothing was overwritten.")
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.database)))
    state = Path(args.state).resolve()
    state.mkdir(parents=True, exist_ok=True)
    baseline = state / (args.database + "-baseline-source")
    if baseline.exists():
        raise RuntimeError("Baseline source destination already exists.")
    baseline.mkdir()
    archive = subprocess.check_output(["git", "-C", args.candidate, "archive", "--format=tar", args.baseline])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(baseline, filter="data")
    common = [sys.executable, str(Path(__file__).resolve()), "--config", str(Path(args.config).resolve()),
              "--state", str(state), "--database", args.database]
    for mode, root in (("before", baseline), ("after", Path(args.candidate))):
        subprocess.run([*common, "--mode", mode, "--root", str(root)], check=True)
    before = json.loads((state / (args.database + "-before.json")).read_text())
    after = json.loads((state / (args.database + "-after.json")).read_text())
    changed = []
    for table, original in before.items():
        current = after.get(table)
        if not current:
            changed.append(table)
        elif table in {"django_migrations", "django_content_type", "auth_permission"}:
            if Counter(original["rows"]) - Counter(current["rows"]):
                changed.append(table)
        elif original != current:
            changed.append(table)
    if changed:
        raise AssertionError("Existing populated rows changed: " + ", ".join(changed))
    subprocess.run([*common, "--mode", "legacy-insert", "--root", str(baseline)], check=True)
    result = {"baseline": args.baseline, "database": args.database, "target": "owned loopback PostgreSQL",
        "existing_tables_checked": len(before), "changed_existing_tables": changed,
        "new_tables": sorted(set(after) - set(before)), "legacy_orm_inserts": "passed",
        "preserved": ["identities", "consent", "approved hiring terms", "disclosure", "employer feedback",
                      "BTW-confirmed hired outcome", "outreach", "Tasks", "notifications", "private notes", "audit"]}
    (state / (args.database + "-RESULT.json")).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
