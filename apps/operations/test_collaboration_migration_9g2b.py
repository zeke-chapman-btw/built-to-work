"""No legacy Event, task, participant, or audit data is rewritten by 0003."""
from datetime import UTC,datetime
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from apps.core.models import AuditLog
from django.contrib.contenttypes.models import ContentType

class CollaborationMigration9G2BTests(TransactionTestCase):
    def test_forward_and_reverse_preserve_legacy_event_and_audit(self):
        executor=MigrationExecutor(connection);latest=executor.loader.graph.leaf_nodes()
        old=[('operations','0002_task_archived_at_task_archived_by_and_more')]
        try:
            executor.migrate(old);apps=executor.loader.project_state(old).apps
            event=apps.get_model('events','Event').objects.create(name='SYNTHETIC legacy link',start_at=datetime(2031,1,1,tzinfo=UTC),end_at=datetime(2031,1,2,tzinfo=UTC))
            staff=apps.get_model('staffing','StaffProfile').objects.create(first_name='SYNTHETIC',last_name='Legacy')
            task=apps.get_model('operations','Task').objects.create(title='SYNTHETIC legacy cancelled',event=event,assigned_staff=staff,created_by=None,status='cancelled')
            ct,_=ContentType.objects.get_or_create(app_label='operations',model='task')
            audit=AuditLog.objects.create(content_type=ct,object_id=str(task.pk),action='operations.task.created',new_data={'event_id':str(event.pk),'status':'cancelled'})
            ids=(task.pk,event.pk,staff.pk,audit.pk)
            executor=MigrationExecutor(connection);executor.migrate(latest)
            apps=executor.loader.project_state(latest).apps
            row=apps.get_model('operations','Task').objects.get(pk=ids[0]);self.assertEqual(row.event_id,ids[1]);self.assertEqual(row.status,'cancelled');self.assertIsNone(row.created_by_id)
            self.assertEqual(apps.get_model('operations','TaskEventLink').objects.filter(task_id=ids[0]).count(),0)
            self.assertEqual(AuditLog.objects.get(pk=ids[3]).new_data,{'event_id':str(ids[1]),'status':'cancelled'})
            # Reversal is exercised only on this disposable schema, before new link/comment data.
            executor=MigrationExecutor(connection);executor.migrate(old);apps=executor.loader.project_state(old).apps
            row=apps.get_model('operations','Task').objects.get(pk=ids[0]);self.assertEqual((row.event_id,row.assigned_staff_id),(ids[1],ids[2]));self.assertEqual(row.status,'cancelled')
            self.assertTrue(AuditLog.objects.filter(pk=ids[3]).exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
