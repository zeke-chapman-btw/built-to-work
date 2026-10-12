"""Portable, authenticated recovery of a lost trailer SQLite database."""

import base64
import binascii
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import connection

from .backup_service import _fernet


def _decrypt_canonical(cipher, token, label):
    """Reject trailing/alternate encodings that Fernet's decoder may ignore."""
    try:
        decoded = base64.b64decode(token, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{label} is not a valid encrypted token.") from exc
    if base64.urlsafe_b64encode(decoded) != token:
        raise ValueError(f"{label} has noncanonical or extra bytes.")
    return cipher.decrypt(token)


def restore_portable_backup(*, archive_path, manifest_path, target_path, admin_identifier, admin_password):
    """Restore from two encrypted files and the separately held key, without source DB access."""
    if not admin_identifier or not admin_password:
        raise PermissionError("A recovery administrator and password are required.")
    if "sqlite3" not in connection.settings_dict.get("ENGINE", ""):
        raise NotImplementedError("Portable recovery currently supports SQLite only.")

    archive = Path(archive_path).expanduser().resolve(strict=True)
    manifest_file = Path(manifest_path).expanduser().resolve(strict=True)
    encrypted = archive.read_bytes()
    cipher = _fernet()
    try:
        manifest = json.loads(_decrypt_canonical(cipher, manifest_file.read_bytes(), "Backup manifest"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Encrypted backup manifest is invalid.") from exc
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
        raise ValueError("Unsupported encrypted backup manifest.")
    if manifest.get("archive_name") != archive.name:
        raise ValueError("Backup manifest does not identify this archive.")
    digest = hashlib.sha256(encrypted).hexdigest()
    if not hmac.compare_digest(str(manifest.get("ciphertext_sha256", "")), digest):
        raise ValueError("Backup archive and manifest do not match.")
    try:
        trailer_id = UUID(str(manifest["trailer_id"]))
        trailer_identity = str(manifest["trailer_identity"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Encrypted backup manifest lacks trailer identity.") from exc
    if not trailer_identity:
        raise ValueError("Encrypted backup manifest lacks trailer identity.")

    target = Path(target_path).expanduser().resolve()
    if target.suffix.lower() not in {".sqlite3", ".sqlite", ".db"}:
        raise ValueError("Restore target must be a SQLite database file.")
    if target.exists() or target.is_symlink():
        raise FileExistsError("Restore target already exists; refusing to overwrite it.")
    if target == archive or target == manifest_file:
        raise ValueError("Restore target must be separate from the backup files.")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    plaintext = _decrypt_canonical(cipher, encrypted, "Backup archive")
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix="btw-restore-", delete=False) as temp:
        os.chmod(temp.name, 0o600)
        temp.write(plaintext)
        temp.flush()
        os.fsync(temp.fileno())
        temp_name = temp.name
    try:
        with sqlite3.connect(temp_name) as restored:
            if restored.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Restored database failed SQLite integrity_check.")
            trailer_row = restored.execute(
                "SELECT id, identity FROM events_trailerinstance WHERE identity = ?",
                (trailer_identity,),
            ).fetchone()
            if not trailer_row or UUID(str(trailer_row[0])) != trailer_id:
                raise ValueError("Encrypted manifest trailer identity does not match the database.")
            user_model = get_user_model()
            username_column = user_model._meta.get_field(user_model.USERNAME_FIELD).column
            user_table = user_model._meta.db_table
            # Table and column names come from trusted Django model metadata, never from the archive.
            user_row = restored.execute(
                f'SELECT id, password, is_active, is_superuser FROM "{user_table}" WHERE "{username_column}" = ?',
                (admin_identifier,),
            ).fetchone()
            from django.contrib.auth.hashers import check_password
            if not user_row or not user_row[2] or not user_row[3] or not check_password(admin_password, user_row[1]):
                raise PermissionError("A valid active administrator credential is required for recovery.")
            content_type_row = restored.execute(
                "SELECT id FROM django_content_type WHERE app_label = 'events' AND model = 'trailerinstance'"
            ).fetchone()
            if not content_type_row:
                raise ValueError("Recovered database is missing trailer audit configuration.")
            restored.execute(
                """INSERT INTO core_auditlog
                (actor_id, action, content_type_id, object_id, timestamp, source, old_data, new_data, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_row[0], "trailer_backup.portable_restore", content_type_row[0],
                    str(trailer_id), datetime.now(timezone.utc).isoformat(),
                    "portable_recovery_command", None,
                    json.dumps({"archive": archive.name, "ciphertext_sha256": digest,
                                "trailer_identity": trailer_identity}),
                    "Authorized restore into a new trailer database after source database loss.",
                ),
            )
        # os.link fails atomically if another process created the destination in the meantime.
        os.link(temp_name, target)
        return target
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
