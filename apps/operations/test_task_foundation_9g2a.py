"""Synthetic permissions, lifecycle, audit, legacy and UI regressions for 9G2-A."""
from datetime import UTC, datetime
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, Client
from django.urls import reverse
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.staffing.models import StaffProfile, StaffAssignment
from .models import Task
from . import services as svc, task_access

class TaskFoundation9G2ATests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.creator=U.objects.create_user('synthetic-9g2a-creator',is_staff=True)
        cls.assignee=U.objects.create_user('synthetic-9g2a-assignee',is_staff=True)
        cls.next_user=U.objects.create_user('synthetic-9g2a-next',is_staff=True)
        cls.other=U.objects.create_user('synthetic-9g2a-other',is_staff=True)
        cls.admin=U.objects.create_user('synthetic-9g2a-admin',is_staff=True)
        cls.admin.user_permissions.add(Permission.objects.get(content_type__app_label='operations',codename='manage_tasks'))
        cls.profiles={u.pk:StaffProfile.objects.create(first_name='SYNTHETIC',last_name=u.username,user=u) for u in [cls.creator,cls.assignee,cls.next_user,cls.other]}
        cls.event=Event.objects.create(name='RESTRICTED EVENT NAME',start_at=datetime(2031,1,1,tzinfo=UTC),end_at=datetime(2031,1,2,tzinfo=UTC),status='upcoming')

    def login(self,user):
        self.client.force_login(user,backend='django.contrib.auth.backends.ModelBackend')

    def create(self, actor=None, **values):
        data=dict(title='SYNTHETIC task',description='Synthetic description',assigned_staff=str(self.profiles[self.assignee.pk].pk),event='',due_date='',priority='normal',revision='new')
        data.update(values)
        obj,form=svc.save_task(actor=actor or self.creator,data=data)
        self.assertIsNotNone(obj,form.errors)
        return obj

    def edit_data(self,t,**values):
        data=dict(title=t.title,description=t.description,assigned_staff=str(t.assigned_staff_id),event=str(t.event_id or ''),due_date=t.due_date.isoformat() if t.due_date else '',priority=t.priority,revision=t.updated_at.isoformat())
        data.update(values)
        return data

    def change_status(self,t,user,status):
        obj,form=svc.task_status(actor=user,task_id=t.pk,data={'revision':t.updated_at.isoformat(),'status':status})
        self.assertIsNotNone(obj,form.errors)
        return obj

    def archive(self,t,user,restore=False):
        obj,form=svc.task_archive(actor=user,task_id=t.pk,data={'revision':t.updated_at.isoformat()},restore=restore)
        self.assertIsNotNone(obj,form.errors)
        return obj

    def test_creator_assignee_and_explicit_admin_can_manage_every_field(self):
        for actor in [self.creator,self.assignee,self.admin]:
            with self.subTest(actor=actor.username):
                t=self.create();original=t.created_by_id
                t,form=svc.save_task(actor=actor,task_id=t.pk,data=self.edit_data(t,title='Changed',description='Updated details',priority='high',due_date='2031-02-01'))
                self.assertIsNotNone(t,form.errors);self.assertEqual((t.title,t.description,t.priority),('Changed','Updated details','high'));self.assertEqual(t.created_by_id,original)
                t=self.change_status(t,actor,'completed');self.assertEqual(t.completed_by,actor)
                t=self.change_status(t,actor,'todo');self.assertIsNone(t.completed_at);self.assertIsNone(t.completed_by)
                t=self.archive(t,actor);self.assertEqual(t.archived_by,actor)
                t=self.archive(t,actor,True);self.assertIsNone(t.archived_at)
                self.login(actor);self.assertContains(self.client.get(reverse('operations:task',args=[t.pk])),'Activity history')

    def test_regular_staff_is_not_task_administrator(self):
        self.assertFalse(task_access.task_admin(self.other));self.assertTrue(task_access.task_admin(self.admin))
        t=self.create();self.assertFalse(svc.tasks_for(self.other).filter(pk=t.pk).exists())

    def test_unrelated_staff_denied_all_direct_task_endpoints(self):
        t=self.create();self.login(self.other)
        for name in ['task','task-edit','task-status','task-archive','task-restore']:
            url=reverse('operations:'+name,args=[t.pk])
            self.assertEqual(self.client.get(url).status_code,404,name)
            self.assertEqual(self.client.post(url,{}).status_code,403 if name == 'task' else 404,name)
        with self.assertRaises(PermissionDenied):svc.save_task(actor=self.other,task_id=t.pk,data=self.edit_data(t))
        with self.assertRaises(PermissionDenied):self.change_status(t,self.other,'completed')
        with self.assertRaises(PermissionDenied):self.archive(t,self.other)

    def test_reassignment_transfers_access_and_retains_creator(self):
        t=self.create();t,form=svc.save_task(actor=self.assignee,task_id=t.pk,data=self.edit_data(t,assigned_staff=str(self.profiles[self.next_user.pk].pk)))
        self.assertIsNotNone(t,form.errors)
        self.assertTrue(svc.tasks_for(self.creator).filter(pk=t.pk).exists());self.assertTrue(svc.tasks_for(self.next_user).filter(pk=t.pk).exists());self.assertFalse(svc.tasks_for(self.assignee).filter(pk=t.pk).exists())
        self.login(self.assignee);self.assertEqual(self.client.get(reverse('operations:task',args=[t.pk])).status_code,404)
        with self.assertRaises(PermissionDenied):svc.save_task(actor=self.assignee,task_id=t.pk,data=self.edit_data(t))
        log=AuditLog.objects.get(action='operations.task.reassigned',object_id=str(t.pk))
        self.assertEqual(log.old_data['assigned_staff_id'],str(self.profiles[self.assignee.pk].pk));self.assertEqual(log.new_data['assigned_staff_id'],str(self.profiles[self.next_user.pk].pk))
        self.assertEqual(AuditLog.objects.filter(object_id=str(t.pk)).count(),2)

    def test_creator_keeps_access_when_they_were_previous_assignee(self):
        t=self.create(assigned_staff=str(self.profiles[self.creator.pk].pk));t,form=svc.save_task(actor=self.creator,task_id=t.pk,data=self.edit_data(t,assigned_staff=str(self.profiles[self.next_user.pk].pk)))
        self.assertTrue(svc.tasks_for(self.creator).filter(pk=t.pk).exists())

    def test_self_assignment_and_no_due_date(self):
        t=self.create(assigned_staff=str(self.profiles[self.creator.pk].pk));self.assertIsNone(t.due_date)
        self.login(self.creator);self.assertContains(self.client.get('/operations/tasks/?view=all'),t.title)
        self.assertNotContains(self.client.get('/operations/tasks/?view=overdue'),t.title)

    def test_new_assignments_require_active_linked_staff_login(self):
        U=get_user_model()
        inactive=U.objects.create_user('synthetic-inactive',is_staff=True,is_active=False)
        public=U.objects.create_user('synthetic-public')
        candidates=[StaffProfile.objects.create(first_name='SYNTHETIC',last_name='No account'),StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Inactive account',user=inactive),StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Public account',user=public)]
        p=self.profiles[self.other.pk];p.is_active=False;p.save();candidates.append(p)
        t=self.create()
        for profile in candidates:
            data=self.edit_data(t,assigned_staff=str(profile.pk));data['revision']='new'
            obj,form=svc.save_task(actor=self.creator,data=data);self.assertIsNone(obj);self.assertIn('assigned_staff',form.errors)
            obj,form=svc.save_task(actor=self.creator,task_id=t.pk,data=self.edit_data(t,assigned_staff=str(profile.pk)));self.assertIsNone(obj)

    def test_legacy_missing_creator_and_unlinked_assignee_admin_only(self):
        p=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Legacy')
        t=Task.objects.create(title='LEGACY CANCELLED',assigned_staff=p,created_by=None,status='cancelled')
        self.assertFalse(svc.tasks_for(self.other).filter(pk=t.pk).exists())
        updated,form=svc.save_task(actor=self.admin,task_id=t.pk,data=self.edit_data(t,title='LEGACY retained'))
        self.assertIsNotNone(updated,form.errors);self.assertIsNone(updated.created_by_id);self.assertEqual(updated.assigned_staff_id,p.pk);self.assertEqual(updated.status,'cancelled')
        t=self.archive(updated,self.admin);t=self.archive(t,self.admin,True);self.assertEqual(t.status,'cancelled')

    def test_legacy_missing_creator_accessible_to_linked_assignee(self):
        p=self.profiles[self.assignee.pk];p.is_active=False;p.save()
        t=Task.objects.create(title='LEGACY',assigned_staff=p,created_by=None)
        self.assertTrue(svc.tasks_for(self.assignee).filter(pk=t.pk).exists())
        t,form=svc.save_task(actor=self.assignee,task_id=t.pk,data=self.edit_data(t,title='LEGACY updated'))
        self.assertIsNotNone(t,form.errors);self.assertIsNone(t.created_by_id)

    def test_original_creator_cannot_be_spoofed(self):
        t=self.create(created_by=str(self.other.pk));self.assertEqual(t.created_by,self.creator)
        t,form=svc.save_task(actor=self.assignee,task_id=t.pk,data=self.edit_data(t,created_by=str(self.other.pk)))
        t.refresh_from_db();self.assertEqual(t.created_by,self.creator)

    def test_archive_separate_from_status_and_hidden_from_lists(self):
        t=self.change_status(self.create(),self.assignee,'completed');completed=t.completed_at;t=self.archive(t,self.assignee)
        self.assertEqual(t.status,'completed');self.assertEqual(t.completed_at,completed)
        self.login(self.creator)
        for url in ['/operations/tasks/','/operations/tasks/?view=all','/operations/tasks/?view=completed']:
            self.assertNotContains(self.client.get(url),t.title)
        self.assertContains(self.client.get('/operations/tasks/?view=archived'),t.title)
        self.assertContains(self.client.get(reverse('operations:task',args=[t.pk])),'Task archived')
        t=self.archive(t,self.creator,True);self.assertContains(self.client.get('/operations/tasks/?view=completed'),t.title)

    def test_history_values_attribution_and_one_entry_per_change(self):
        t=self.create();t,form=svc.save_task(actor=self.assignee,task_id=t.pk,data=self.edit_data(t,title='<script>unsafe</script>',description='New description',due_date='2031-01-05',priority='high'))
        log=AuditLog.objects.get(action='operations.task.updated',object_id=str(t.pk));self.assertEqual(log.actor,self.assignee)
        for key in ['title','description','due_date','priority']:self.assertNotEqual(log.old_data[key],log.new_data[key])
        self.login(self.creator);r=self.client.get(reverse('operations:task',args=[t.pk]));self.assertContains(r,'&lt;script&gt;unsafe&lt;/script&gt;');self.assertNotContains(r,'<script>unsafe</script>')
        self.assertEqual(AuditLog.objects.filter(object_id=str(t.pk)).count(),2)

    def test_noop_edit_does_not_add_history(self):
        t=self.create();before=t.updated_at;obj,form=svc.save_task(actor=self.creator,task_id=t.pk,data=self.edit_data(t));obj.refresh_from_db()
        self.assertEqual(obj.updated_at,before);self.assertEqual(AuditLog.objects.filter(object_id=str(t.pk)).count(),1)

    def test_audit_failure_rolls_back_task_write(self):
        t=self.create()
        with patch('apps.operations.services.audit',side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):svc.save_task(actor=self.creator,task_id=t.pk,data=self.edit_data(t,title='Should roll back'))
        t.refresh_from_db();self.assertEqual(t.title,'SYNTHETIC task')

    def test_completed_reopened_and_archive_events_retained(self):
        t=self.create();t=self.change_status(t,self.assignee,'completed');t=self.change_status(t,self.assignee,'in_progress');t=self.archive(t,self.creator);t=self.archive(t,self.assignee,True)
        actions=list(AuditLog.objects.filter(object_id=str(t.pk)).values_list('action',flat=True))
        for action in ['created','completed','reopened','archived','restored']:self.assertIn('operations.task.'+action,actions)

    def test_stale_archive_and_edit_are_rejected(self):
        t=self.create();self.change_status(t,self.assignee,'in_progress')
        with self.assertRaises(ValidationError):self.archive(t,self.creator)
        with self.assertRaises(ValidationError):svc.save_task(actor=self.creator,task_id=t.pk,data=self.edit_data(t,title='Stale'))

    def test_task_access_does_not_reveal_related_event(self):
        Task.objects.create(title='SYNTHETIC restricted task',assigned_staff=self.profiles[self.assignee.pk],created_by=self.creator,event=self.event)
        t=Task.objects.get(title='SYNTHETIC restricted task');self.login(self.assignee)
        for url in [reverse('operations:task',args=[t.pk]),reverse('operations:task-edit',args=[t.pk]),'/operations/tasks/?view=all']:
            r=self.client.get(url);self.assertEqual(r.status_code,200);self.assertNotContains(r,self.event.name)
        self.assertEqual(self.client.get(reverse('operations:event',args=[self.event.pk,'tasks'])).status_code,403)
        obj,form=svc.save_task(actor=self.creator,data=self.edit_data(t,revision='new'));self.assertIsNone(obj);self.assertIn('event',form.errors)

    def test_associated_event_access_does_not_grant_task_access(self):
        StaffAssignment.objects.create(event=self.event,staff=self.profiles[self.other.pk])
        t=Task.objects.create(title='PRIVATE EVENT TASK',assigned_staff=self.profiles[self.assignee.pk],created_by=self.creator,event=self.event)
        self.login(self.other);self.assertNotContains(self.client.get(reverse('operations:event',args=[self.event.pk,'tasks'])),t.title)
        self.assertEqual(self.client.get(reverse('operations:task',args=[t.pk])).status_code,404)

    def test_event_task_creation_reuses_same_record(self):
        StaffAssignment.objects.create(event=self.event,staff=self.profiles[self.creator.pk]);self.login(self.creator)
        url=reverse('operations:event-task-add',args=[self.event.pk]);r=self.client.get(url);self.assertEqual(r.status_code,200)
        t=self.create();data=self.edit_data(t,revision='new',event='');r=self.client.post(url,data);self.assertEqual(r.status_code,302)
        created=Task.objects.exclude(pk=t.pk).get();self.assertEqual(created.event_id,self.event.pk)
        self.assertContains(self.client.get(reverse('operations:event',args=[self.event.pk,'tasks'])),created.title)
        self.archive(created,self.creator);self.assertNotContains(self.client.get(reverse('operations:event',args=[self.event.pk,'tasks'])),created.title)

    def test_creator_and_assignee_ui_forms_have_management_controls(self):
        t=self.create()
        for actor in [self.creator,self.assignee,self.admin]:
            self.login(actor);r=self.client.get(reverse('operations:task',args=[t.pk]))
            self.assertContains(r,'Edit / Reassign Task');self.assertContains(r,'Archive task');self.assertContains(r,'Activity history')
            for name in ['task-edit','task-status','task-archive']:
                response=self.client.get(reverse('operations:'+name,args=[t.pk]));self.assertEqual(response.status_code,200);self.assertContains(response,'<label');self.assertContains(response,'csrfmiddlewaretoken')

    def test_history_is_read_only_and_paginated(self):
        t=self.create()
        for i in range(22):
            t,form=svc.save_task(actor=self.creator,task_id=t.pk,data=self.edit_data(t,title='SYNTHETIC change '+str(i)))
        self.login(self.creator);r=self.client.get(reverse('operations:task',args=[t.pk]));self.assertEqual(len(r.context['history_rows']),20);self.assertContains(r,'Older activity')
        self.assertEqual(self.client.post(reverse('operations:task',args=[t.pk]),{}).status_code,403)
        self.login(self.other);self.assertEqual(self.client.get(reverse('operations:task',args=[t.pk])+'?history_page=2').status_code,404)

    def test_csrf_and_public_account_boundaries(self):
        t=self.create();c=Client(enforce_csrf_checks=True);c.force_login(self.creator,backend='django.contrib.auth.backends.ModelBackend')
        for name in ['task-edit','task-status','task-archive','task-restore']:self.assertEqual(c.post(reverse('operations:'+name,args=[t.pk]),{}).status_code,403)
        public=get_user_model().objects.create_user('synthetic-nonstaff');self.login(public);self.assertEqual(self.client.get('/operations/tasks/').status_code,403)

    def test_invalid_form_feedback_and_single_page_heading(self):
        self.login(self.creator);r=self.client.post('/operations/tasks/create/',{'revision':'new','title':''})
        self.assertEqual(r.status_code,400);self.assertContains(r,'Please review the highlighted fields',status_code=400)
        self.assertEqual(r.content.count(b'<h1>'),1);self.assertContains(r,'role="alert"',status_code=400)
