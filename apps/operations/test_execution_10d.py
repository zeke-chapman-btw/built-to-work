"""Synthetic event checklist ownership, lifecycle, history and task invariants."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.test import TestCase,RequestFactory
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.staffing.models import StaffProfile,StaffAssignment
from .models import Task,TaskNotification
from .execution_models import (ChecklistMaster,MasterChecklistCategory,MasterChecklistItem,
    EventExecution,EventChecklistCategory,EventChecklistItem)
from . import execution_services as svc,services as task_svc,collaboration,task_history,task_access


class Execution10DTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User=get_user_model()
        cls.admin=User.objects.create_superuser("synthetic-10d-admin","admin10d@example.test","test-only")
        cls.lead_user=User.objects.create_user("synthetic-10d-lead",is_staff=True)
        cls.worker_user=User.objects.create_user("synthetic-10d-worker",is_staff=True)
        cls.other_user=User.objects.create_user("synthetic-10d-other",is_staff=True)
        cls.lead=StaffProfile.objects.create(first_name="SYNTHETIC",last_name="Event Manager",user=cls.lead_user)
        cls.worker=StaffProfile.objects.create(first_name="SYNTHETIC",last_name="Worker",user=cls.worker_user)
        cls.other=StaffProfile.objects.create(first_name="SYNTHETIC",last_name="Other",user=cls.other_user)
        cls.master,_=ChecklistMaster.objects.get_or_create(key="btw")
        cls.master.categories.update(is_active=False)
        cls.master_category=MasterChecklistCategory.objects.create(master=cls.master,title="SYNTHETIC closeout",phase="closeout")
        cls.source_item=MasterChecklistItem.objects.create(category=cls.master_category,title="SYNTHETIC required pack-up",is_required=True)
        prep=MasterChecklistCategory.objects.create(master=cls.master,title="SYNTHETIC preparation",phase="prepare")
        MasterChecklistItem.objects.create(category=prep,title="SYNTHETIC optional preparation")
        cls.event=Event.objects.create(name="SYNTHETIC 10D event",start_at=timezone.now()+timedelta(days=2),
            end_at=timezone.now()+timedelta(days=2,hours=4),timezone_name="America/New_York",status="upcoming")
        cls.execution=svc.ensure_execution(event=cls.event,actor=cls.admin)
        cls.execution,_=svc.set_manager(actor=cls.admin,event_id=cls.event.pk,data={
            "revision":cls.execution.updated_at.isoformat(),"primary_manager":str(cls.lead.pk)})
        StaffAssignment.objects.create(event=cls.event,staff=cls.worker,coverage_role="staff")
        cls.item=EventChecklistItem.objects.get(category__execution=cls.execution,source_item=cls.source_item)
        cls.item.assigned_staff=cls.worker;cls.item.save()

    def action_data(self,**extra):
        self.execution.refresh_from_db();self.event.refresh_from_db()
        data={"revision":self.execution.updated_at.isoformat(),"event_revision":self.event.updated_at.isoformat(),"confirm":"on","reason":"SYNTHETIC operation"}
        data.update(extra);return data

    def item_data(self,**extra):
        self.item.refresh_from_db()
        data={"revision":self.item.updated_at.isoformat(),"confirm":"on","reason":"SYNTHETIC item change"}
        data.update(extra);return data

    def end(self):
        result,form=svc.event_action(actor=self.lead_user,event_id=self.event.pk,action="end",data=self.action_data())
        self.assertIsNotNone(result,form.errors);self.execution=result;return result

    def complete(self,actor=None):
        result,form=svc.item_action(actor=actor or self.worker_user,event_id=self.event.pk,item_id=self.item.pk,action="complete",data=self.item_data())
        self.assertIsNotNone(result,form.errors);return result

    def close(self):
        self.complete();self.end()
        result,form=svc.event_action(actor=self.lead_user,event_id=self.event.pk,action="close",data=self.action_data())
        self.assertIsNotNone(result,form.errors);self.execution=result;return result

    def test_snapshot_is_independent_and_keeps_required_master_origin(self):
        self.assertEqual(self.execution.categories.count(),2)
        self.assertTrue(self.item.required_from_master)
        self.assertEqual(self.item.original_title,self.source_item.title)
        self.source_item.title="SYNTHETIC future wording";self.source_item.is_required=False;self.source_item.save()
        self.item.refresh_from_db();self.assertNotEqual(self.item.title,self.source_item.title);self.assertTrue(self.item.is_required)
        second=Event.objects.create(name="SYNTHETIC future event",start_at=self.event.start_at,end_at=self.event.end_at)
        copied=svc.ensure_execution(event=second,actor=self.admin)
        future=EventChecklistItem.objects.get(category__execution=copied,source_item=self.source_item)
        self.assertEqual(future.title,self.source_item.title);self.assertFalse(future.is_required)

    def test_initialization_is_idempotent(self):
        before=EventChecklistItem.objects.count()
        again=svc.ensure_execution(event=self.event,actor=self.admin)
        self.assertEqual(again.pk,self.execution.pk);self.assertEqual(before,EventChecklistItem.objects.count())

    def test_primary_role_is_event_scoped_without_granting_global_event_permission(self):
        self.assertFalse(self.lead_user.has_perm("events.view_event"))
        self.assertTrue(svc.can_view(self.lead_user,self.event))
        other=Event.objects.create(name="SYNTHETIC unrelated",start_at=self.event.start_at,end_at=self.event.end_at)
        self.assertFalse(svc.can_view(self.lead_user,other))
        self.assertIn(self.event,task_access.events_for(self.lead_user));self.assertNotIn(other,task_access.events_for(self.lead_user))

    def test_only_admin_can_appoint_primary_manager(self):
        with self.assertRaises(PermissionDenied):svc.set_manager(actor=self.worker_user,event_id=self.event.pk,data={})
        with self.assertRaises(PermissionDenied):svc.set_manager(actor=self.lead_user,event_id=self.event.pk,data={})

    def test_worker_can_complete_only_own_item(self):
        completed=self.complete();self.assertEqual(completed.completed_by_id,self.worker_user.pk)
        self.assertIsNotNone(completed.completed_at)
        with self.assertRaises(PermissionDenied):svc.item_action(actor=self.other_user,event_id=self.event.pk,item_id=self.item.pk,action="undo",data=self.item_data())

    def test_event_manager_can_complete_on_behalf_and_actor_is_recorded(self):
        completed=self.complete(self.lead_user)
        self.assertEqual(completed.assigned_staff_id,self.worker.pk);self.assertEqual(completed.completed_by_id,self.lead_user.pk)
        audit=AuditLog.objects.get(action="event.execution.item.complete",object_id=str(self.item.pk))
        self.assertEqual(audit.actor_id,self.lead_user.pk);self.assertEqual(audit.new_data["assigned_staff_id"],str(self.worker.pk))

    def test_worker_cannot_restructure_or_mark_na(self):
        with self.assertRaises(PermissionDenied):svc.save_item(actor=self.worker_user,event_id=self.event.pk,item_id=self.item.pk,data={})
        with self.assertRaises(PermissionDenied):svc.item_action(actor=self.worker_user,event_id=self.event.pk,item_id=self.item.pk,action="na",data=self.item_data())

    def test_required_master_item_cannot_be_archived(self):
        with self.assertRaises(ValidationError):svc.item_action(actor=self.lead_user,event_id=self.event.pk,item_id=self.item.pk,action="archive",data=self.item_data())
        self.item.refresh_from_db();self.assertIsNone(self.item.archived_at)

    def test_required_master_flag_cannot_be_removed_at_database_level(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():EventChecklistItem.objects.filter(pk=self.item.pk).update(is_required=False)

    def test_na_requires_reason_and_excludes_item_from_progress(self):
        saved,form=svc.item_action(actor=self.lead_user,event_id=self.event.pk,item_id=self.item.pk,action="na",data=self.item_data(reason=""))
        self.assertIsNone(saved);self.assertIn("reason",form.errors)
        saved,form=svc.item_action(actor=self.lead_user,event_id=self.event.pk,item_id=self.item.pk,action="na",data=self.item_data(reason="SYNTHETIC not needed"))
        indicator=next(x for x in svc.progress(self.execution) if x["key"]=="closeout")
        self.assertEqual(indicator["total"],0);self.assertEqual(indicator["na"],1);self.assertIsNone(indicator["percent"])
        self.assertEqual(saved.na_reason,"SYNTHETIC not needed")

    def test_progress_never_rounds_incomplete_work_up_to_100(self):
        category=EventChecklistCategory.objects.create(execution=self.execution,title="SYNTHETIC live",phase="live")
        now=timezone.now()
        EventChecklistItem.objects.bulk_create([
            EventChecklistItem(category=category,title=f"SYNTHETIC item {i}",state="done" if i<249 else "todo",
                completed_at=now if i<249 else None,completed_by=self.lead_user if i<249 else None)
            for i in range(250)])
        indicator=next(x for x in svc.progress(self.execution) if x["key"]=="live")
        self.assertEqual(indicator["percent"],99);self.assertEqual(indicator["completed"],249)

    def test_end_reuses_completed_lifecycle_and_creates_one_owned_task(self):
        ended=self.end();self.event.refresh_from_db()
        self.assertEqual(self.event.status,"completed");self.assertIsNotNone(ended.actual_ended_at)
        task=ended.closeout_task;self.assertEqual(task.assigned_staff_id,self.lead.pk)
        self.assertEqual(task.event_id,self.event.pk);self.assertEqual(task.status,"todo")
        again=svc.record_ended(event=self.event,actor=self.lead_user)
        self.assertEqual(again.closeout_task_id,task.pk);self.assertEqual(Task.objects.count(),1)

    def test_end_does_not_require_preparation_or_closeout_completion(self):
        ended=self.end();self.assertIsNotNone(ended.actual_ended_at)
        self.item.refresh_from_db();self.assertEqual(self.item.state,"todo")

    def test_scheduled_end_alone_does_not_end_or_create_a_task(self):
        self.event.start_at=timezone.now()-timedelta(hours=2);self.event.end_at=timezone.now()-timedelta(hours=1);self.event.save()
        self.assertIn(self.event,svc.end_reminder_candidates())
        self.assertEqual(self.event.status,"upcoming");self.assertEqual(Task.objects.count(),0)

    def test_formal_closeout_requires_recorded_end(self):
        with self.assertRaises(ValidationError):svc.event_action(actor=self.lead_user,event_id=self.event.pk,action="close",data=self.action_data())

    def test_formal_closeout_rejects_outstanding_required_closeout_items(self):
        self.end()
        with self.assertRaises(ValidationError):svc.event_action(actor=self.lead_user,event_id=self.event.pk,action="close",data=self.action_data())
        self.execution.refresh_from_db();self.assertIsNone(self.execution.closed_at)
        self.assertEqual(self.execution.closeout_task.status,"todo")

    def test_formal_closeout_completes_task_and_locks_ordinary_item_edits(self):
        closed=self.close();self.assertIsNotNone(closed.closed_at)
        self.assertEqual(closed.closeout_task.status,"completed")
        with self.assertRaises(ValidationError):svc.item_action(actor=self.lead_user,event_id=self.event.pk,item_id=self.item.pk,action="undo",data=self.item_data())

    def test_na_required_item_allows_closeout_without_deleting_it(self):
        saved,form=svc.item_action(actor=self.lead_user,event_id=self.event.pk,item_id=self.item.pk,action="na",data=self.item_data())
        self.assertIsNotNone(saved,form.errors);self.end()
        closed,form=svc.event_action(actor=self.lead_user,event_id=self.event.pk,action="close",data=self.action_data())
        self.assertIsNotNone(closed,form.errors)
        self.assertTrue(EventChecklistItem.objects.filter(pk=self.item.pk,state="na").exists())

    def test_reopen_requires_reason_and_reuses_task_without_resuming_event(self):
        closed=self.close();task_id=closed.closeout_task_id
        saved,form=svc.event_action(actor=self.lead_user,event_id=self.event.pk,action="reopen",data=self.action_data(reason=""))
        self.assertIsNone(saved);self.assertIn("reason",form.errors)
        reopened,form=svc.event_action(actor=self.lead_user,event_id=self.event.pk,action="reopen",data=self.action_data())
        self.assertIsNotNone(reopened,form.errors);self.assertIsNone(reopened.closed_at)
        self.assertEqual(reopened.closeout_task_id,task_id);self.assertEqual(reopened.closeout_task.status,"todo")
        self.event.refresh_from_db();self.assertEqual(self.event.status,"completed")
        self.assertTrue(AuditLog.objects.filter(action="event.execution.reopened",object_id=str(reopened.pk)).exists())

    def test_closeout_task_cannot_be_completed_archived_or_unlinked_through_generic_task_controls(self):
        ended=self.end();task=ended.closeout_task
        for operation in [
            lambda:task_svc.task_status(actor=self.lead_user,task_id=task.pk,data={"revision":task.updated_at.isoformat(),"status":"completed","reason":"SYNTHETIC"}),
            lambda:task_svc.task_archive(actor=self.lead_user,task_id=task.pk,data={"revision":task.updated_at.isoformat(),"reason":"SYNTHETIC"}),
            lambda:collaboration.remove_link(actor=self.lead_user,task_id=task.pk,kind="primary",link_id=task.pk,data={"revision":task.updated_at.isoformat(),"reason":"SYNTHETIC"}),
        ]:
            with self.assertRaises(ValidationError):operation()
        task.refresh_from_db();self.assertEqual(task.status,"todo");self.assertEqual(task.event_id,self.event.pk)

    def test_manager_reassignment_reassigns_same_closeout_task(self):
        ended=self.end();task_id=ended.closeout_task_id
        saved,form=svc.set_manager(actor=self.admin,event_id=self.event.pk,data={"revision":ended.updated_at.isoformat(),"primary_manager":str(self.other.pk),"reason":"SYNTHETIC handover"})
        self.assertIsNotNone(saved,form.errors);self.assertEqual(saved.closeout_task_id,task_id)
        self.assertEqual(saved.closeout_task.assigned_staff_id,self.other.pk)

    def test_no_new_task_notifications_are_generated_by_closeout_automation(self):
        self.close()
        svc.event_action(actor=self.lead_user,event_id=self.event.pk,action="reopen",data=self.action_data())
        self.assertEqual(TaskNotification.objects.count(),0)

    def test_closeout_task_audit_appears_in_existing_task_timeline(self):
        ended=self.end()
        history=task_history.entries_for(self.lead_user,ended.closeout_task)
        self.assertTrue(history.filter(action="event.execution.task.created").exists())

    def test_stale_item_and_execution_actions_leave_records_unchanged(self):
        with self.assertRaises(ValidationError):svc.item_action(actor=self.worker_user,event_id=self.event.pk,item_id=self.item.pk,action="complete",data=self.item_data(revision="stale"))
        with self.assertRaises(ValidationError):svc.event_action(actor=self.lead_user,event_id=self.event.pk,action="end",data=self.action_data(revision="stale"))
        self.assertFalse(Task.objects.exists());self.item.refresh_from_db();self.assertEqual(self.item.state,"todo")

    def test_ordinary_staff_cannot_edit_master(self):
        with self.assertRaises(PermissionDenied):svc.save_master(actor=self.worker_user,data={},kind="item")

    def test_reading_health_is_read_only_and_roles_are_enforced(self):
        self.client.force_login(self.worker_user,backend="django.contrib.auth.backends.ModelBackend")
        before=(AuditLog.objects.count(),Task.objects.count(),EventChecklistItem.objects.count())
        response=self.client.get(reverse("operations:health",args=[self.event.pk]))
        self.assertEqual(response.status_code,200);self.assertContains(response,"Event Health Monitor")
        self.assertEqual(before,(AuditLog.objects.count(),Task.objects.count(),EventChecklistItem.objects.count()))
        self.client.force_login(self.other_user,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("operations:health",args=[self.event.pk])).status_code,403)

    def test_primary_manager_has_execution_and_own_event_tasks_without_global_task_access(self):
        self.client.force_login(self.lead_user,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("operations:execution",args=[self.event.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse("operations:event",args=[self.event.pk,"tasks"])).status_code,200)
        self.assertFalse(self.lead_user.has_perm("operations.manage_tasks"))

    def test_required_item_edit_and_na_form_preserve_event_context(self):
        self.client.force_login(self.lead_user,backend="django.contrib.auth.backends.ModelBackend")
        for route,args in [("operations:checklist-item-edit",[self.event.pk,self.item.pk]),
            ("operations:checklist-item-action",[self.event.pk,self.item.pk,"na"])]:
            response=self.client.get(reverse(route,args=args))
            self.assertEqual(response.status_code,200);self.assertContains(response,self.event.name)

    def test_admin_event_creation_initializes_snapshot_without_creating_closeout_task(self):
        from django.contrib import admin
        obj=Event(name="SYNTHETIC admin new event",start_at=self.event.start_at,end_at=self.event.end_at)
        request=RequestFactory().get("/admin/");request.user=self.admin
        admin.site._registry[Event].save_model(request,obj,None,False)
        self.assertTrue(EventExecution.objects.filter(event=obj).exists())
        self.assertEqual(Task.objects.count(),0)

    def test_closed_manager_handover_preserves_original_closeout_attribution(self):
        self.close()
        original=self.execution.closed_by_id
        self.execution.refresh_from_db()
        saved,form=svc.set_manager(actor=self.admin,event_id=self.event.pk,data={
            "revision":self.execution.updated_at.isoformat(),"primary_manager":str(self.other.pk),"reason":"SYNTHETIC handover"})
        self.assertIsNotNone(saved,form.errors)
        saved.closeout_task.refresh_from_db()
        self.assertEqual(saved.closeout_task.completed_by_id,original)
        self.assertEqual(saved.closeout_task.status,"completed")
        self.assertIsNotNone(saved.closed_at)

    def test_historical_completed_event_requires_a_known_actual_end_and_records_provenance(self):
        self.event.status="completed";self.event.save()
        result,form=svc.event_action(actor=self.admin,event_id=self.event.pk,action="adopt_end",
            data=self.action_data(actual_end="2020-06-01T17:30",reason="SYNTHETIC historical source"))
        self.assertIsNotNone(result,form.errors)
        self.assertEqual(result.actual_ended_at.isoformat(),"2020-06-01T21:30:00+00:00")
        self.assertEqual(Task.objects.filter(event=self.event).count(),1)
        self.assertTrue(AuditLog.objects.filter(action="event.execution.ended",reason="SYNTHETIC historical source").exists())
        self.assertTrue(AuditLog.objects.filter(action="event.execution.ended").exists())

    def test_historical_end_rejects_missing_source_future_and_ambiguous_times(self):
        self.event.status="completed";self.event.save()
        for actual,reason in [("2020-06-01T17:30",""),("2099-06-01T17:30","SYNTHETIC future"),("2020-11-01T01:30","SYNTHETIC ambiguous")]:
            with self.subTest(actual=actual):
                with self.assertRaises(ValidationError):
                    svc.event_action(actor=self.admin,event_id=self.event.pk,action="adopt_end",
                        data=self.action_data(actual_end=actual,reason=reason))
        self.assertEqual(Task.objects.filter(event=self.event).count(),0)
        self.execution.refresh_from_db();self.assertIsNone(self.execution.actual_ended_at)
