"""The additive upgrade preserves v1 evidence and leaves all classifications unset."""
from django.test import TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from .intake_defaults import default_contract
from .intake_contracts import contract_hash

class IdentifierMigrationTests(TransactionTestCase):
    def test_existing_rows_and_frozen_contract_survive_additive_migration(self):
        executor=MigrationExecutor(connection)
        old_target=[('participants','0021_build15_foundation')]
        target=[('participants','0022_primary_identifier_provenance')]
        executor.migrate(old_target)
        try:
            old=executor.loader.project_state(old_target).apps
            Person=old.get_model('participants','Participant');Form=old.get_model('participants','RegistrationForm');Version=old.get_model('participants','RegistrationFormVersion')
            person=Person.objects.create(first_name='SYNTHETIC migration',last_name='Evidence',life_stage='unemployed')
            form=Form.objects.create(slug='synthetic-migration-identifiers',name='Synthetic frozen form',is_smart_intake=True)
            snapshot=default_contract();digest=contract_hash(snapshot)
            version=Version.objects.create(form=form,version=1,status='published',snapshot=snapshot,content_hash=digest)
            executor=MigrationExecutor(connection);executor.migrate(target)
            new=executor.loader.project_state(target).apps
            current=new.get_model('participants','Participant').objects.get(pk=person.pk)
            frozen=new.get_model('participants','RegistrationFormVersion').objects.get(pk=version.pk)
            self.assertEqual(current.primary_identifier,'');self.assertEqual(current.life_stage,'unemployed')
            self.assertEqual(frozen.snapshot,snapshot);self.assertEqual(frozen.content_hash,digest);self.assertEqual(frozen.status,'published')
            # Actual old-code INSERTs omit the new columns during a rolling release/rollback.
            legacy_person=Person.objects.create(first_name='SYNTHETIC old-code insert',last_name='Rolling compatibility')
            self.assertEqual(new.get_model('participants','Participant').objects.get(pk=legacy_person.pk).primary_identifier,'')
            OldState=old.get_model('participants','IntakeFieldState')
            legacy_owner=OldState.objects.create(participant_id=legacy_person.pk,destination='current_situation',is_protected=True,value_hash='a'*64,last_value='')
            self.assertEqual(new.get_model('participants','IntakeFieldState').objects.get(pk=legacy_owner.pk).provenance,{})
        finally:
            MigrationExecutor(connection).migrate(target)
