"""Upgrade existing event/staff/task rows without guessing historical execution state."""
from datetime import timedelta
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

class ExecutionMigration10DTests(TransactionTestCase):
    def test_upgrade_preserves_existing_records_and_does_not_backfill_historical_end(self):
        executor=MigrationExecutor(connection)
        latest=executor.loader.graph.leaf_nodes()
        # Migrate only the isolated test database; restore the full graph even on failure.
        old_targets=[target for target in latest if target[0]!="operations"]+[("operations","0006_operations_templates")]
        # The actual predecessor name is obtained from the verified graph, not inferred.
        old_targets[-1]=next(node for node in executor.loader.graph.nodes if node[0]=="operations" and node[1].startswith("0006_"))
        try:
            executor.migrate(old_targets)
            old=executor.loader.project_state(old_targets).apps
            Event=old.get_model("events","Event");Staff=old.get_model("staffing","StaffProfile");Task=old.get_model("operations","Task")
            now=timezone.now()
            event=Event.objects.create(name="SYNTHETIC pre-10D completed event",start_at=now-timedelta(days=3),end_at=now-timedelta(days=3,hours=-4),status="completed",timezone_name="America/New_York")
            staff=Staff.objects.create(first_name="SYNTHETIC",last_name="Historical staff")
            task=Task.objects.create(title="SYNTHETIC existing task",event_id=event.pk,assigned_staff_id=staff.pk,status="todo")
            identities={("events","Event"):event.pk,("staffing","StaffProfile"):staff.pk,("operations","Task"):task.pk}
            before={key:old.get_model(*key).objects.values().get(pk=pk) for key,pk in identities.items()}
            executor=MigrationExecutor(connection);executor.migrate(latest)
            new=executor.loader.project_state(latest).apps
            for key,pk in identities.items():
                self.assertEqual(new.get_model(*key).objects.values().get(pk=pk),before[key])
            self.assertFalse(new.get_model("operations","EventExecution").objects.filter(event_id=event.pk).exists())
            self.assertEqual(new.get_model("operations","MasterChecklistItem").objects.count(),8)
            self.assertFalse(new.get_model("operations","MasterChecklistItem").objects.filter(is_required=True).exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
