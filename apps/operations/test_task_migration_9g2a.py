"""Legacy row and audit preservation across the additive task migration."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from apps.core.models import AuditLog
from django.contrib.contenttypes.models import ContentType

class TaskMigration9G2ATests(TransactionTestCase):
    def test_cancelled_unowned_unlinked_task_and_audit_survive_migration(self):
        executor=MigrationExecutor(connection)
        latest=executor.loader.graph.leaf_nodes()
        old_target=[('operations','0001_initial')]
        try:
            executor.migrate(old_target)
            apps=executor.loader.project_state(old_target).apps
            p=apps.get_model('staffing','StaffProfile').objects.create(first_name='SYNTHETIC',last_name='Legacy migration')
            t=apps.get_model('operations','Task').objects.create(title='SYNTHETIC legacy cancelled',description='Retained text',assigned_staff=p,status='cancelled',created_by=None)
            ct,_=ContentType.objects.get_or_create(app_label='operations',model='task')
            a=AuditLog.objects.create(content_type=ct,object_id=str(t.pk),action='operations.task.status',old_data={'status':'todo'},new_data={'status':'cancelled'})
            pk,staff_pk,audit_pk=t.pk,p.pk,a.pk
            executor=MigrationExecutor(connection)
            executor.migrate(latest)
            apps=executor.loader.project_state(latest).apps
            t=apps.get_model('operations','Task').objects.get(pk=pk)
            self.assertEqual(t.status,'cancelled');self.assertEqual(t.assigned_staff_id,staff_pk);self.assertIsNone(t.created_by_id)
            self.assertIsNone(t.archived_at);self.assertIsNone(t.archived_by_id);self.assertEqual(t.description,'Retained text')
            a=AuditLog.objects.get(pk=audit_pk)
            self.assertEqual(a.old_data,{'status':'todo'});self.assertEqual(a.new_data,{'status':'cancelled'})
        finally:
            MigrationExecutor(connection).migrate(latest)
