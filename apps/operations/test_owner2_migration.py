"""The new typed Customer association does not rewrite existing Task data."""
from datetime import datetime,UTC
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TaskCustomerMigrationTests(TransactionTestCase):
    def test_populated_legacy_task_and_typed_links_survive(self):
        executor=MigrationExecutor(connection)
        latest=executor.loader.graph.leaf_nodes()
        old=[('operations','0008_starter_master_checklist_10d')]
        try:
            executor.migrate(old)
            apps=executor.loader.project_state(old).apps
            User=apps.get_model('accounts','User');Profile=apps.get_model('staffing','StaffProfile')
            Event=apps.get_model('events','Event');Person=apps.get_model('participants','Participant')
            Task=apps.get_model('operations','Task')
            user=User.objects.create(username='synthetic-owner2-migration',is_active=True,is_staff=True)
            profile=Profile.objects.create(first_name='SYNTHETIC',last_name='Migration',user=user)
            event=Event.objects.create(name='SYNTHETIC old Event',code='SYN-OWNER2-MIGRATION',start_at=datetime(2026,11,9,13,tzinfo=UTC),end_at=datetime(2026,11,9,21,tzinfo=UTC))
            person=Person.objects.create(first_name='SYNTHETIC',last_name='Old Task Person')
            task=Task.objects.create(title='SYNTHETIC populated old Task',description='Keep original data',assigned_staff=profile,created_by=user)
            apps.get_model('operations','TaskEventLink').objects.create(task=task,event=event)
            apps.get_model('operations','TaskParticipantLink').objects.create(task=task,participant=person)
            expected=(task.pk,task.title,task.description,task.assigned_staff_id,task.created_by_id,task.status)
            executor=MigrationExecutor(connection);executor.migrate(latest)
            current=executor.loader.project_state(latest).apps
            saved=current.get_model('operations','Task').objects.get(pk=task.pk)
            self.assertEqual((saved.pk,saved.title,saved.description,saved.assigned_staff_id,saved.created_by_id,saved.status),expected)
            self.assertEqual(current.get_model('operations','TaskEventLink').objects.filter(task_id=task.pk,event_id=event.pk).count(),1)
            self.assertEqual(current.get_model('operations','TaskParticipantLink').objects.filter(task_id=task.pk,participant_id=person.pk).count(),1)
            self.assertEqual(current.get_model('operations','TaskCustomerLink').objects.count(),0)
        finally:
            MigrationExecutor(connection).migrate(latest)
