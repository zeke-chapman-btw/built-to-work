"""Synthetic notification events, current permissions, read state and live due dates."""
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.staffing.models import StaffProfile
from .models import Task, TaskComment, TaskNotification
from . import services, collaboration, notifications, due_dates

class TaskNotifications9G2DTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.creator=U.objects.create_user('synthetic-D-creator',is_staff=True)
        cls.assignee=U.objects.create_user('synthetic-D-assignee',is_staff=True)
        cls.next_user=U.objects.create_user('synthetic-D-next',is_staff=True)
        cls.other=U.objects.create_user('synthetic-D-other',is_staff=True)
        cls.admin=U.objects.create_user('synthetic-D-admin',is_staff=True)
        cls.admin.user_permissions.add(Permission.objects.get(content_type__app_label='operations',codename='manage_tasks'))
        cls.profiles={u.pk:StaffProfile.objects.create(first_name='SYNTHETIC',last_name=u.username,user=u) for u in (cls.creator,cls.assignee,cls.next_user,cls.other)}
    def login(self,user=None):
        self.client.force_login(user or self.assignee,backend='django.contrib.auth.backends.ModelBackend')
    def data(self,t=None,**values):
        data=dict(title=t.title if t else 'SYNTHETIC D task',description=t.description if t else '',assigned_staff=str(t.assigned_staff_id if t else self.profiles[self.assignee.pk].pk),event='',due_date=t.due_date.isoformat() if t and t.due_date else '',priority=t.priority if t else 'normal',revision=t.updated_at.isoformat() if t else 'new')
        data.update(values);return data
    def create(self,actor=None,**values):
        t,f=services.save_task(actor=actor or self.creator,data=self.data(**values))
        self.assertIsNotNone(t,f.errors);return t
    def edit(self,t,actor=None,**values):
        t,f=services.save_task(actor=actor or self.creator,task_id=t.pk,data=self.data(t,**values))
        self.assertIsNotNone(t,f.errors);return t
    def status(self,t,status,actor=None):
        t,f=services.task_status(actor=actor or self.creator,task_id=t.pk,data={'status':status,'revision':t.updated_at.isoformat()})
        self.assertIsNotNone(t,f.errors);return t
    def archive(self,t,restore=False):
        t,f=services.task_archive(actor=self.creator,task_id=t.pk,data={'revision':t.updated_at.isoformat()},restore=restore)
        self.assertIsNotNone(t,f.errors);return t
    def comment(self,t,actor=None,content='SYNTHETIC comment',comment=None):
        c,f=collaboration.save_comment(actor=actor or self.creator,task_id=t.pk,comment_id=comment.pk if comment else None,data={'content':content,'revision':(comment or t).updated_at.isoformat()})
        self.assertIsNotNone(c,f.errors);return c
    def notice(self,t=None):
        return TaskNotification.objects.get(task=t or self.create(),kind='assigned')
    def read_url(self,n):return reverse('operations:updates-read',args=[n.pk])
    def test_assignment_uses_current_assignee_and_generic_message(self):
        t=self.create();n=self.notice(t)
        self.assertEqual(n.recipient,self.assignee);self.assertEqual(n.source_event.actor,self.creator)
        self.assertNotIn(t.title,n.message);self.assertIsNone(n.read_at)
    def test_reassignment_new_user_and_creator_excludes_actor(self):
        t=self.create();self.edit(t,actor=self.assignee,assigned_staff=str(self.profiles[self.next_user.pk].pk))
        rows=TaskNotification.objects.filter(kind='reassigned')
        self.assertSetEqual(set(rows.values_list('recipient_id',flat=True)),{self.creator.pk,self.next_user.pk})
    def test_self_assignment_has_no_notice(self):
        self.create(assigned_staff=str(self.profiles[self.creator.pk].pk))
        self.assertFalse(TaskNotification.objects.exists())
    def test_comment_notifies_other_current_parties_only(self):
        t=self.create();self.comment(t,actor=self.assignee)
        self.assertSetEqual(set(TaskNotification.objects.filter(kind='comment').values_list('recipient_id',flat=True)),{self.creator.pk})
    def test_admin_comment_notifies_creator_and_assignee_without_broadcast(self):
        t=self.create();self.comment(t,actor=self.admin)
        self.assertSetEqual(set(TaskNotification.objects.filter(kind='comment').values_list('recipient_id',flat=True)),{self.creator.pk,self.assignee.pk})
        self.assertFalse(TaskNotification.objects.filter(recipient=self.other).exists())
    def test_creator_assignee_same_user_receives_once(self):
        t=self.create(assigned_staff=str(self.profiles[self.creator.pk].pk));self.comment(t,actor=self.admin)
        self.assertEqual(TaskNotification.objects.count(),1)
        self.assertEqual(TaskNotification.objects.get().recipient,self.creator)
    def test_edited_comment_notice_and_unchanged_comment_noop(self):
        t=self.create();c=self.comment(t);self.comment(t,content='SYNTHETIC edit',comment=c)
        count=TaskNotification.objects.count();c.refresh_from_db();self.comment(t,content=c.content,comment=c)
        self.assertEqual(TaskNotification.objects.count(),count)
        self.assertEqual(TaskNotification.objects.filter(kind='comment').count(),2)
    def test_status_completed_reopened_cancelled_notify(self):
        t=self.create()
        for status in ('completed','todo','cancelled'):
            t=self.status(t,status)
        self.assertEqual(TaskNotification.objects.filter(kind='status',recipient=self.assignee).count(),3)
        self.assertIn('Cancelled',TaskNotification.objects.filter(kind='status').first().message)
    def test_archive_restore_notifies(self):
        t=self.archive(self.create());self.archive(t,True)
        self.assertEqual(TaskNotification.objects.filter(kind__in=['archived','restored']).count(),2)
    def test_priority_due_date_meaningful_but_routine_edits_silent(self):
        t=self.create();t=self.edit(t,title='SYNTHETIC renamed',description='SYNTHETIC details')
        self.assertEqual(TaskNotification.objects.count(),1)
        t=self.edit(t,priority='high');self.edit(t,due_date=timezone.localdate().isoformat())
        self.assertEqual(TaskNotification.objects.filter(kind='changed').count(),2)
    def test_unchanged_save_does_not_create_audit_or_notification(self):
        t=self.create();before=(AuditLog.objects.count(),TaskNotification.objects.count())
        self.edit(t);self.assertEqual((AuditLog.objects.count(),TaskNotification.objects.count()),before)
    def test_duplicate_audit_delivery_is_idempotent(self):
        t=self.create();entry=self.notice(t).source_event
        notifications.notify_from_audit(entry,t);notifications.notify_from_audit(entry,t)
        self.assertEqual(TaskNotification.objects.count(),1)
        with self.assertRaises(IntegrityError),transaction.atomic():
            TaskNotification.objects.create(task=t,source_event=entry,recipient=self.assignee,kind='assigned',message='Duplicate')
    def test_failed_notification_rolls_back_task_and_audit(self):
        before=AuditLog.objects.count()
        with patch.object(TaskNotification.objects,'get_or_create',side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaises(RuntimeError):self.create()
        self.assertFalse(Task.objects.exists());self.assertEqual(AuditLog.objects.count(),before)
    def test_failed_notification_rolls_back_comment_and_audit(self):
        t=self.create();before=AuditLog.objects.count()
        with patch.object(TaskNotification.objects,'get_or_create',side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaises(RuntimeError):self.comment(t)
        self.assertFalse(TaskComment.objects.exists());self.assertEqual(AuditLog.objects.count(),before)
    def test_inactive_recipient_is_not_notified(self):
        t=self.create();self.assignee.is_active=False;self.assignee.save()
        self.comment(t);self.assertFalse(TaskNotification.objects.filter(kind='comment').exists())
    def test_former_assignee_loses_feed_count_and_read_access(self):
        t=self.create();n=self.notice(t);self.edit(t,assigned_staff=str(self.profiles[self.next_user.pk].pk))
        self.login();response=self.client.get(reverse('operations:updates'))
        self.assertNotContains(response,t.title);self.assertEqual(response.context['task_updates']['unread'],0)
        self.assertEqual(self.client.post(self.read_url(n)).status_code,404)
        self.client.post(reverse('operations:updates-read-all'));n.refresh_from_db();self.assertIsNone(n.read_at)
    def test_creator_keeps_notification_access_after_reassignment(self):
        t=self.create(assigned_staff=str(self.profiles[self.creator.pk].pk));self.comment(t,actor=self.admin)
        n=TaskNotification.objects.get();self.edit(t,assigned_staff=str(self.profiles[self.next_user.pk].pk))
        self.assertTrue(notifications.visible_notifications(self.creator).filter(pk=n.pk).exists())
    def test_cross_recipient_read_denied_even_task_admin(self):
        n=self.notice();self.login(self.admin)
        self.assertEqual(self.client.post(self.read_url(n)).status_code,404)
        self.assertFalse(notifications.visible_notifications(self.admin).exists())
    def test_read_is_post_only_idempotent_and_count_updates(self):
        n=self.notice();self.login()
        self.assertEqual(self.client.get(self.read_url(n)).status_code,405)
        self.assertEqual(self.client.get(reverse('operations:updates-read-all')).status_code,405)
        self.assertEqual(notifications.shell_context(self.assignee)['task_updates']['unread'],1)
        self.assertEqual(self.client.post(self.read_url(n)).status_code,302);n.refresh_from_db();stamp=n.read_at
        self.client.post(self.read_url(n));n.refresh_from_db();self.assertEqual(n.read_at,stamp)
        self.assertEqual(notifications.shell_context(self.assignee)['task_updates']['unread'],0)
    def test_mark_all_only_own_visible_rows(self):
        t=self.create();self.comment(t,actor=self.assignee);self.login()
        self.client.post(reverse('operations:updates-read-all'))
        self.assertFalse(TaskNotification.objects.filter(recipient=self.assignee,read_at__isnull=True).exists())
        self.assertTrue(TaskNotification.objects.filter(recipient=self.creator,read_at__isnull=True).exists())
    def test_read_actions_require_csrf(self):
        n=self.notice();strict=Client(enforce_csrf_checks=True);strict.force_login(self.assignee,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(strict.post(self.read_url(n)).status_code,403)
        self.assertEqual(strict.post(reverse('operations:updates-read-all')).status_code,403)
    def test_feed_requires_staff_and_bell_is_staff_only(self):
        self.assertEqual(self.client.get(reverse('operations:updates')).status_code,302)
        u=get_user_model().objects.create_user('synthetic-D-public');self.login(u)
        self.assertEqual(self.client.get(reverse('operations:updates')).status_code,403)
        self.assertEqual(notifications.shell_context(u),{})
        self.login();self.assertContains(self.client.get(reverse('internal:home')),'data-notification-bell')
    def test_feed_latest_first_stable_and_paginated(self):
        for i in range(23):self.create(title='SYNTHETIC D '+str(i))
        self.login();page=self.client.get(reverse('operations:updates')).context['notification_page']
        expected=list(notifications.visible_notifications(self.assignee).values_list('pk',flat=True))
        self.assertEqual([n.pk for n in page],expected[:20]);self.assertEqual(page.paginator.count,23)
        page2=self.client.get(reverse('operations:updates')+'?page=2').context['notification_page']
        self.assertEqual([n.pk for n in page2],expected[20:])
    def test_safe_read_redirects_and_home_return(self):
        n=self.notice();self.login()
        for value in ('https://evil.invalid/','//evil.invalid/','../../','²'):
            response=self.client.post(self.read_url(n),{'return_to':value,'page':value})
            self.assertEqual(response.status_code,302);self.assertEqual(response['Location'],reverse('operations:updates'))
        self.assertRedirects(self.client.post(self.read_url(n),{'return_to':'home'}),reverse('internal:home'))
    def test_due_calendar_boundaries_and_excluded_lifecycle(self):
        today=timezone.localdate();t=self.create()
        for delta,expected in ((-1,'overdue'),(0,'soon'),(1,'soon'),(3,'soon'),(4,'')):
            t.due_date=today+timedelta(days=delta);self.assertEqual(due_dates.due_state(t,today),expected)
        t.due_date=today-timedelta(days=1)
        for status in ('completed','cancelled'):
            t.status=status;self.assertEqual(due_dates.due_state(t,today),'')
        t.status='todo';t.archived_at=timezone.now();self.assertEqual(due_dates.due_state(t,today),'')
        t.archived_at=None;t.due_date=None;self.assertEqual(due_dates.due_state(t,today),'')
    def test_due_attention_current_parties_and_limit(self):
        for i in range(7):self.create(title='SYNTHETIC due '+str(i),due_date=(timezone.localdate()-timedelta(days=i+1)).isoformat())
        attention=due_dates.due_attention(self.assignee)
        self.assertEqual(attention['overdue_count'],7);self.assertEqual(len(attention['overdue']),5)
        self.assertEqual(due_dates.due_attention(self.other)['overdue_count'],0)
        self.assertEqual(due_dates.due_attention(self.admin)['overdue_count'],0)
    def test_due_soon_in_workspace_detail_and_updates_without_writes(self):
        t=self.create(due_date=(timezone.localdate()+timedelta(days=3)).isoformat());self.login()
        before=(TaskNotification.objects.count(),AuditLog.objects.count())
        for unused in range(2):
            self.assertContains(self.client.get('/operations/tasks/?view=all&due=soon'),t.title)
            self.assertContains(self.client.get(reverse('operations:task',args=[t.pk])),'Due soon')
            response=self.client.get(reverse('operations:updates'));self.assertContains(response,t.title)
            self.assertEqual(response.context['due_attention']['soon_count'],1)
        self.assertEqual((TaskNotification.objects.count(),AuditLog.objects.count()),before)
    def test_empty_updates_and_connected_home_without_redesign(self):
        self.login(self.other);response=self.client.get(reverse('operations:updates'))
        self.assertContains(response,'No task updates')
        t=self.create();self.login();home=self.client.get(reverse('internal:home'))
        self.assertContains(home,t.title);self.assertContains(home,reverse('operations:updates'))
        self.assertContains(home,'Quick Actions');self.assertContains(home,'Analytics')
    def test_read_does_not_dismiss_live_due_attention(self):
        t=self.create(due_date=timezone.localdate().isoformat());self.login()
        self.client.post(reverse('operations:updates-read-all'))
        self.assertEqual(notifications.shell_context(self.assignee)['task_updates']['unread'],0)
        self.assertEqual(due_dates.due_attention(self.assignee)['soon_count'],1)
    def test_actorless_history_is_not_backfilled_into_notifications(self):
        t=self.create();n=self.notice(t);count=TaskNotification.objects.count()
        AuditLog.objects.create(content_type=n.source_event.content_type,object_id=str(t.pk),action='operations.task.status',old_data={'status':'todo'},new_data={'status':'cancelled'})
        self.assertEqual(TaskNotification.objects.count(),count)
    def test_failed_notification_rolls_back_status_change(self):
        t=self.create();before=AuditLog.objects.count()
        with patch.object(TaskNotification.objects,'get_or_create',side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaises(RuntimeError):self.status(t,'completed')
        t.refresh_from_db();self.assertEqual(t.status,'todo');self.assertIsNone(t.completed_at)
        self.assertEqual(AuditLog.objects.count(),before)
