"""Forward schema rehearsal over populated legacy rows, with no reclassification."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

class CurrentPreferenceMigrationTests(TransactionTestCase):
    def test_nullable_additions_preserve_bool_text_originals_and_catalog(self):
        executor=MigrationExecutor(connection)
        latest=executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([('recruiting','0005_build15_employer')])
            old=executor.loader.project_state([('recruiting','0005_build15_employer')]).apps
            Person=old.get_model('participants','Participant')
            Career=old.get_model('participants','ParticipantCareerProfile')
            Profile=old.get_model('recruiting','RecruitingProfile')
            Catalog=old.get_model('recruiting','Classification')
            choice=Catalog.objects.create(kind='employment',key='synthetic-legacy',label='SYNTHETIC retained custom vocabulary')
            ids=[]
            for index,value in enumerate([True,False,None]):
                person=Person.objects.create(first_name='SYNTHETIC',last_name=f'Preserved {index}')
                Career.objects.create(participant=person,employment_status='SYNTHETIC legacy employment',preferred_work_type='part_time',intake_data={'retained_original_marker':'unchanged'})
                Profile.objects.create(participant=person,willing_to_relocate=value,employment_classification=choice,revision=3)
                ids.append((person.pk,value))
            count=Catalog.objects.count()
            form=old.get_model('participants','RegistrationForm').objects.create(name='SYNTHETIC retained original',slug='synthetic-followup-original')
            version=old.get_model('participants','RegistrationFormVersion').objects.create(form=form,version=1,status='published',snapshot={'questions':[{'key':'original','label':'SYNTHETIC original question','type':'short_text'}]})
            original=old.get_model('participants','RegistrationSubmission').objects.create(participant_id=ids[0][0],form_version=version,status='submitted',submitted_at=timezone.now(),answers={'original':'SYNTHETIC immutable answer'},answer_snapshot={'original':'SYNTHETIC immutable answer'})
            executor=MigrationExecutor(connection)
            executor.migrate([('recruiting','0006_current_preference_choices')])
            new=executor.loader.project_state([('recruiting','0006_current_preference_choices')]).apps
            Profile=new.get_model('recruiting','RecruitingProfile')
            Career=new.get_model('participants','ParticipantCareerProfile')
            for pk,value in ids:
                profile=Profile.objects.get(participant_id=pk)
                self.assertIsNone(profile.current_employment_status)
                self.assertIsNone(profile.relocation_preference)
                self.assertEqual(profile.willing_to_relocate,value)
                self.assertEqual(profile.employment_classification_id,choice.pk)
                self.assertEqual(profile.revision,3)
                career=Career.objects.get(participant_id=pk)
                self.assertEqual(career.employment_status,'SYNTHETIC legacy employment')
                self.assertEqual(career.preferred_work_type,'part_time')
                self.assertEqual(career.intake_data,{'retained_original_marker':'unchanged'})
            self.assertEqual(new.get_model('recruiting','Classification').objects.count(),count)
            retained=new.get_model('participants','RegistrationSubmission').objects.get(pk=original.pk)
            self.assertEqual(retained.answers,{'original':'SYNTHETIC immutable answer'})
            self.assertEqual(retained.answer_snapshot,original.answer_snapshot)
            self.assertEqual(retained.form_version_id,version.pk)
        finally:
            MigrationExecutor(connection).migrate(latest)
