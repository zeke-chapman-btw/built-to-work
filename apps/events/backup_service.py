"""Encrypted, local database backups for trailer recovery."""
import hashlib
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.utils import timezone as django_timezone

from .models import BackupRecord, TrailerInstance


def _fernet():
    key = os.environ.get("BTW_TRAILER_BACKUP_KEY", "")
    if not key:
        raise ImproperlyConfigured("BTW_TRAILER_BACKUP_KEY must be configured outside the backup directory.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ImproperlyConfigured("BTW_TRAILER_BACKUP_KEY must be a valid Fernet key.") from exc


def _backup_root(database_path):
    configured = os.environ.get("BTW_BACKUP_DIR", "")
    if not configured:
        raise ImproperlyConfigured("BTW_BACKUP_DIR must point to storage separate from the primary database.")
    root = Path(configured).expanduser().resolve()
    db = Path(database_path).expanduser().resolve()
    if root == db.parent or root.is_relative_to(db.parent):
        raise ImproperlyConfigured("Backup storage must not be inside the primary database directory.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def create_database_backup(trailer: TrailerInstance) -> BackupRecord:
    """Create a Fernet-encrypted, consistent snapshot of a file-backed SQLite DB."""
    destination = None
    record = None
    try:
        if "sqlite3" not in connection.settings_dict.get("ENGINE", ""):
            raise NotImplementedError("Trailer backup currently supports SQLite only; PostgreSQL tooling is pending.")
        database_path = connection.settings_dict.get("NAME")
        if not database_path or database_path == ":memory:":
            raise ImproperlyConfigured("A file-backed SQLite database is required for trailer backups.")
        root = _backup_root(database_path)
        cipher = _fernet()
        frequency = max(0, int(os.environ.get("BTW_BACKUP_FREQUENCY_MINUTES", "1440")))
        if frequency:
            recent = BackupRecord.objects.filter(
                trailer=trailer, status=BackupRecord.Status.SUCCEEDED,
                completed_at__gte=django_timezone.now() - timedelta(minutes=frequency),
            ).order_by("-completed_at").first()
            if recent:
                return recent
        with tempfile.TemporaryDirectory(prefix="btw-backup-", dir=root) as work:
            plain = Path(work) / "snapshot.sqlite3"
            with sqlite3.connect(database_path) as source, sqlite3.connect(plain) as target_db:
                source.backup(target_db)
            encrypted = cipher.encrypt(plain.read_bytes())
        name = f"{trailer.identity}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}.fernet"
        destination = root / name
        temp_path = destination.with_suffix(".tmp")
        temp_path.write_bytes(encrypted)
        temp_path.chmod(0o600)
        os.replace(temp_path, destination)
        record = BackupRecord.objects.create(
            trailer=trailer, storage_path=str(destination),
            ciphertext_hash=hashlib.sha256(encrypted).hexdigest(),
            status=BackupRecord.Status.SUCCEEDED, completed_at=django_timezone.now(),
        )
        retention = max(0, int(os.environ.get("BTW_BACKUP_RETENTION_COUNT", "30")))
        if retention:
            older = BackupRecord.objects.filter(
                trailer=trailer, status=BackupRecord.Status.SUCCEEDED,
            ).exclude(pk=record.pk).order_by("-completed_at")[max(0, retention - 1):]
            for previous in older:
                old_path = Path(previous.storage_path).resolve()
                if old_path.is_relative_to(root) and old_path.is_file():
                    old_path.unlink()
                previous.status = BackupRecord.Status.EXPIRED
                previous.storage_path = ""
                previous.save(update_fields=["status", "storage_path"])
        return record
    except Exception as exc:
        if record is None:
            if destination and destination.exists():
                destination.unlink()
            BackupRecord.objects.create(
                trailer=trailer, storage_path="", ciphertext_hash="",
                status=BackupRecord.Status.FAILED, error=str(exc)[:2000],
            )
        raise


def restore_to_replacement(record: BackupRecord, target_path: str, actor):
    """Restore only to a new, separate SQLite file; never overwrite the live DB."""
    import os
    import tempfile
    from pathlib import Path

    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from apps.core.audit import record_audit

    if not getattr(actor, "is_superuser", False):
        raise PermissionError("Only a superuser may restore a trailer backup.")
    if record.status != BackupRecord.Status.SUCCEEDED:
        raise ValueError("Only a successful backup can be restored.")
    source = Path(record.storage_path).resolve()
    if not source.is_file():
        raise FileNotFoundError("The encrypted backup file is unavailable.")
    encrypted = source.read_bytes()
    if hashlib.sha256(encrypted).hexdigest() != record.ciphertext_hash:
        raise ValueError("Backup ciphertext hash verification failed.")
    plaintext = _fernet().decrypt(encrypted)
    target = Path(target_path).expanduser().resolve()
    active = Path(connection.settings_dict["NAME"]).resolve()
    if target == active or target.suffix.lower() not in {".sqlite3", ".sqlite", ".db"}:
        raise ValueError("Restore target must be a separate SQLite database file.")
    if target.exists():
        raise FileExistsError("Restore target already exists; refusing to overwrite it.")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix="btw-restore-", delete=False) as temp:
        temp.write(plaintext)
        temp.flush()
        os.fsync(temp.fileno())
        temp_name = temp.name
    try:
        with sqlite3.connect(temp_name) as restored:
            result = restored.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ValueError("Restored database failed SQLite integrity_check.")
        os.chmod(temp_name, 0o600)
        os.link(temp_name, target)
        record_audit(
            actor=actor,
            action="trailer_backup.restored_to_replacement",
            instance=record,
            source="staff_recovery_command",
            new_data={"target": str(target), "backup_id": str(record.pk)},
            reason="Restore to a new replacement trailer database; active database was not overwritten.",
        )
        return target
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
