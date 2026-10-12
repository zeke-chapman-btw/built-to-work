from django.urls import reverse
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet, InvalidToken
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase

from apps.events.backup_service import create_database_backup, restore_to_replacement
from apps.events.models import BackupRecord, TrailerInstance


class TrailerBackupTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.key = Fernet.generate_key().decode("ascii")
        self.env = patch.dict(os.environ, {
            "BTW_BACKUP_DIR": self.storage.name,
            "BTW_TRAILER_BACKUP_KEY": self.key,
        })
        self.env.start()
        self.trailer = TrailerInstance.objects.create(
            identity="backup-test-trailer", display_name="Backup test trailer",
            is_active=True, credential_hash="test-hash",
        )

    def tearDown(self):
        self.env.stop()
        self.storage.cleanup()
        super().tearDown()

    def test_backup_is_encrypted_and_restores_to_new_database_with_audit(self):
        record = create_database_backup(self.trailer)
        encrypted = Path(record.storage_path).read_bytes()
        self.assertEqual(record.status, BackupRecord.Status.SUCCEEDED)
        self.assertNotIn(b"SQLite format 3", encrypted)

        user_model = get_user_model()
        identity = {user_model.USERNAME_FIELD: "backup-admin@example.test"}
        admin = user_model._default_manager.create_superuser(**identity, password="test-only")
        target = Path(self.storage.name) / "replacement.sqlite3"
        restored = restore_to_replacement(record, str(target), admin)
        self.assertEqual(restored, target.resolve())
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        from apps.core.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="trailer_backup.restored_to_replacement").exists())

    def test_restore_refuses_active_database_and_existing_target(self):
        record = create_database_backup(self.trailer)
        user_model = get_user_model()
        admin = user_model._default_manager.create_superuser(
            **{user_model.USERNAME_FIELD: "backup-admin2@example.test"}, password="test-only"
        )
        with self.assertRaises(ValueError):
            restore_to_replacement(record, str(connection.settings_dict["NAME"]), admin)
        existing = Path(self.storage.name) / "existing.sqlite3"
        existing.write_bytes(b"do not replace")
        with self.assertRaises(FileExistsError):
            restore_to_replacement(record, str(existing), admin)
        self.assertEqual(existing.read_bytes(), b"do not replace")

    def test_bad_encryption_key_is_rejected(self):
        record = create_database_backup(self.trailer)
        user_model = get_user_model()
        admin = user_model._default_manager.create_superuser(
            **{user_model.USERNAME_FIELD: "backup-admin3@example.test"}, password="test-only"
        )
        with patch.dict(os.environ, {"BTW_TRAILER_BACKUP_KEY": Fernet.generate_key().decode("ascii")}):
            with self.assertRaises(InvalidToken):
                restore_to_replacement(record, str(Path(self.storage.name) / "bad.sqlite3"), admin)
    def test_retention_expires_old_ciphertext_and_preserves_metadata(self):
        with patch.dict(os.environ, {
            "BTW_BACKUP_FREQUENCY_MINUTES": "0",
            "BTW_BACKUP_RETENTION_COUNT": "1",
        }):
            first = create_database_backup(self.trailer)
            first_path = Path(first.storage_path)
            second = create_database_backup(self.trailer)
        first.refresh_from_db()
        self.assertEqual(first.status, BackupRecord.Status.EXPIRED)
        self.assertFalse(first_path.exists())
        self.assertEqual(first.storage_path, "")
        self.assertEqual(second.status, BackupRecord.Status.SUCCEEDED)
from django.contrib.auth import get_user_model
from django.test import TestCase


class OperationsDashboardTests(TestCase):
    def test_dashboard_rejects_anonymous_users(self):
        response = self.client.get(reverse("events:operations-dashboard"), secure=True)
        self.assertEqual(response.status_code, 302)

    def test_dashboard_requires_sync_status_permission(self):
        user = get_user_model().objects.create_user(
            **{get_user_model().USERNAME_FIELD: "staff-ops@example.test"}, password="test-only", is_staff=True
        )
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("events:operations-dashboard"), secure=True)
        self.assertEqual(response.status_code, 403, response.get("Location"))

    def test_superuser_can_view_operational_summary(self):
        user_model = get_user_model()
        user = user_model.objects.create_superuser(
            **{user_model.USERNAME_FIELD: "ops-admin@example.test"}, password="test-only"
        )
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("events:operations-dashboard"), secure=True)
        self.assertEqual(response.status_code, 200, response.get("Location"))
        self.assertContains(response, "Trailer Operations")
        self.assertContains(response, "Reserved ticket ranges")

