from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ParticipantIdentityMigrationTests(TransactionTestCase):
    """Existing Participant primary keys and per-person UUIDs survive the 7A migration."""

    migrate_from = [("participants", "0001_initial")]
    migrate_to = [("participants", "0002_participantcareerprofile_testparticipantrun_and_more")]

    def test_existing_rows_receive_distinct_uuids_without_changing_primary_keys(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldParticipant = old_apps.get_model("participants", "Participant")
        first = OldParticipant.objects.create(first_name="Existing", last_name="One")
        second = OldParticipant.objects.create(first_name="Existing", last_name="Two")
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            NewParticipant = new_apps.get_model("participants", "Participant")
            migrated_first = NewParticipant.objects.get(pk=first.pk)
            migrated_second = NewParticipant.objects.get(pk=second.pk)
            self.assertIsNotNone(migrated_first.identity_uuid)
            self.assertIsNotNone(migrated_second.identity_uuid)
            self.assertNotEqual(migrated_first.identity_uuid, migrated_second.identity_uuid)
            self.assertEqual(migrated_first.pk, first.pk)
            self.assertEqual(migrated_second.pk, second.pk)
        finally:
            MigrationExecutor(connection).migrate(self.migrate_to)
