"""Synthetic record privacy, task collaboration and timeline acceptance tests."""
from datetime import UTC, datetime
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, Client
from django.urls import reverse
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.participants.models import Participant
from apps.staffing.models import StaffProfile
from .models import Task, TaskComment, TaskEventLink, TaskParticipantLink
from . import services as svc, collaboration as collab, task_links, task_history

class TaskCollaboration9G2BTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        for attr in ['creator','assignee','next_user','other','admin','restricted']:
            u=U.objects.create_user('synthetic-9g2b-'+attr,is_staff=True);setattr(cls,attr,u)
        cls.profiles={u.pk:StaffProfile.objects.create(first_name='SYNTHETIC',last_name=u.username,user=u) for u in [cls.creator,cls.assignee,cls.next_user,cls.other,cls.restricted]}
        record_perms=Permission.objects.filter(codename__in=['view_event','view_participant'],content_type__app_label__in=['events','participants'])
        for u in [cls.creator,cls.assignee,cls.next_user,cls.other,cls.admin]:u.user_permissions.add(*record_perms)
        cls.admin.user_permissions.add(Permission.objects.get(content_type__app_label='operations',codename='manage_tasks'))
        cls.e1=Event.objects.create(name='SYNTHETIC Alpha Event',start_at=datetime(2031,1,1,tzinfo=UTC),end_at=datetime(2031,1,2,tzinfo=UTC),status='upcoming')
        cls.e2=Event.objects.create(name='SYNTHETIC Beta Event',start_at=cls.e1.start_at,end_at=cls.e1.end_at,status='upcoming')
        cls.e3=Event.objects.create(name='SYNTHETIC Archived Event',start_at=cls.e1.start_at,end_at=cls.e1.end_at,status='archived')
        cls.p1=Participant.objects.create(first_name='SYNTHETIC Alice',last_name='Followup',contact_email='sensitive-contact@example.invalid')
        cls.p2=Participant.objects.create(first_name='SYNTHETIC Bob',last_name='Followup')

    def login(self,u):self.client.force_login(u,backend='django.contrib.auth.backends.ModelBackend')
    def task(self,**values):
        data=dict(title='SYNTHETIC collaboration',description='',assigned_staff=str(self.profiles[self.assignee.pk].pk),event='',due_date='',priority='normal',revision='new');data.update(values)
        task,form=svc.save_task(actor=self.creator,data=data);self.assertIsNotNone(task,form.errors);return task
    def link(self,t,actor=None,events=(),people=()):
        obj,form=collab.add_links(actor=actor or self.creator,task_id=t.pk,data={'revision':t.updated_at.isoformat(),'events':[str(e.pk) for e in events],'participants':[str(p.pk) for p in people]})
        self.assertIsNotNone(obj,form.errors);return obj
    def comment(self,t,actor=None,text='SYNTHETIC comment',old=None):
        obj,form=collab.save_comment(actor=actor or self.creator,task_id=t.pk,comment_id=old.pk if old else None,data={'revision':old.updated_at.isoformat() if old else t.updated_at.isoformat(),'content':text})
        self.assertIsNotNone(obj,form.errors);return obj
    def unlink(self,t,kind,link_id,actor=None):
        obj,form=collab.remove_link(actor=actor or self.creator,task_id=t.pk,kind=kind,link_id=link_id,data={'revision':t.updated_at.isoformat()});self.assertIsNotNone(obj,form.errors);return obj

    def test_multiple_events_participants_and_mixed_links(self):
        t=self.link(self.task(),events=[self.e1,self.e2],people=[self.p1,self.p2])
        self.assertEqual(task_links.effective_event_ids(t),{self.e1.pk,self.e2.pk});self.assertEqual(t.participant_links.count(),2)
        self.login(self.creator);r=self.client.get(reverse('operations:task',args=[t.pk]))
        for label in [self.e1.name,self.e2.name,str(self.p1),str(self.p2),'Linked records changed']:self.assertContains(r,label)
        self.assertNotContains(r,self.p1.contact_email)

    def test_legacy_event_union_and_event_views_include_additional_links(self):
        t=self.task(event=str(self.e1.pk));t=self.link(t,events=[self.e1,self.e2])
        self.assertEqual(t.event_links.count(),1);self.assertEqual(t.event_id,self.e1.pk)
        self.login(self.admin)
        for event in [self.e1,self.e2]:self.assertContains(self.client.get(reverse('operations:event',args=[event.pk,'tasks'])),t.title)
        self.assertEqual(Task.objects.count(),1)

    def test_duplicate_link_add_is_idempotent_and_constraints_enforced(self):
        t=self.link(self.task(),events=[self.e1],people=[self.p1]);count=AuditLog.objects.filter(object_id=str(t.pk)).count()
        self.link(t,events=[self.e1],people=[self.p1]);self.assertEqual(AuditLog.objects.filter(object_id=str(t.pk)).count(),count)
        with self.assertRaises(IntegrityError),transaction.atomic():TaskEventLink.objects.create(task=t,event=self.e1)
        with self.assertRaises(IntegrityError),transaction.atomic():TaskParticipantLink.objects.create(task=t,participant=self.p1)
        with self.assertRaises(ProtectedError):self.e1.delete()
        with self.assertRaises(ProtectedError):self.p1.delete()

    def test_creator_assignee_and_admin_can_add_remove_links(self):
        for actor in [self.creator,self.assignee,self.admin]:
            with self.subTest(actor=actor.username):
                t=self.link(self.task(),actor=actor,events=[self.e1],people=[self.p1])
                t=self.unlink(t,'participant',t.participant_links.get().pk,actor);t=self.unlink(t,'event',t.event_links.get().pk,actor)
                self.assertEqual(t.event_links.count()+t.participant_links.count(),0)
                self.assertTrue(Participant.objects.filter(pk=self.p1.pk).exists())

    def test_explicit_legacy_primary_unlink_is_audited_not_a_backfill(self):
        t=self.task(event=str(self.e1.pk));t=self.link(t,events=[self.e2]);t=self.unlink(t,'primary',t.pk)
        self.assertIsNone(t.event_id);self.assertEqual(task_links.effective_event_ids(t),{self.e2.pk})
        log=AuditLog.objects.filter(object_id=str(t.pk),action='operations.task.links.updated').latest('pk')
        self.assertIn(str(self.e1.pk),log.old_data['linked_event_ids']);self.assertNotIn(str(self.e1.pk),log.new_data['linked_event_ids'])

    def test_changing_primary_to_additional_event_deduplicates(self):
        t=self.task(event=str(self.e1.pk));t=self.link(t,events=[self.e2])
        data=dict(title=t.title,description='',assigned_staff=str(t.assigned_staff_id),event=str(self.e2.pk),priority=t.priority,due_date='',revision=t.updated_at.isoformat())
        t,form=svc.save_task(actor=self.creator,task_id=t.pk,data=data);self.assertIsNotNone(t,form.errors)
        self.assertEqual(t.event_id,self.e2.pk);self.assertFalse(t.event_links.exists());self.assertEqual(task_links.effective_event_ids(t),{self.e2.pk})

    def test_unrelated_staff_cannot_search_link_remove_or_comment(self):
        t=self.link(self.task(),events=[self.e1],people=[self.p1]);c=self.comment(t)
        self.login(self.other)
        urls=[reverse('operations:task-links',args=[t.pk]),reverse('operations:comment-add',args=[t.pk]),reverse('operations:comment-edit',args=[t.pk,c.pk]),reverse('operations:task-link-remove',args=[t.pk,'event',t.event_links.get().pk])]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code,404);self.assertEqual(self.client.post(url,{}).status_code,404)
        with self.assertRaises(PermissionDenied):self.link(t,actor=self.other,events=[self.e2])
        with self.assertRaises(PermissionDenied):self.comment(t,actor=self.other)

    def test_restricted_record_labels_picker_and_history_do_not_leak(self):
        t=self.task(assigned_staff=str(self.profiles[self.restricted.pk].pk));t=self.link(t,events=[self.e1],people=[self.p1])
        self.login(self.restricted)
        for url in [reverse('operations:task',args=[t.pk]),reverse('operations:task-links',args=[t.pk])+'?participant_q=Alice&event_q=Alpha','/operations/tasks/?view=all']:
            r=self.client.get(url);self.assertEqual(r.status_code,200)
            for private in [self.e1.name,str(self.p1),self.p1.contact_email]:self.assertNotContains(r,private)
        r=self.client.get(reverse('operations:task',args=[t.pk]));self.assertContains(r,'Restricted participant');self.assertContains(r,'Restricted event')
        obj,form=collab.add_links(actor=self.restricted,task_id=t.pk,data={'revision':t.updated_at.isoformat(),'events':[str(self.e2.pk)],'participants':[str(self.p2.pk)]});self.assertIsNone(obj)
        self.assertTrue(form.errors)

    def test_search_is_bounded_and_uses_only_names(self):
        for i in range(25):Participant.objects.create(first_name='SYNTHETIC Search',last_name=str(i))
        t=self.task();self.login(self.creator);r=self.client.get(reverse('operations:task-links',args=[t.pk]),{'participant_q':'Search'})
        self.assertEqual(r.context['form'].fields['participants'].queryset.count(),20)
        r=self.client.get(reverse('operations:task-links',args=[t.pk]),{'participant_q':self.p1.contact_email});self.assertEqual(r.context['form'].fields['participants'].queryset.count(),0)

    def test_archived_records_are_not_new_link_targets(self):
        self.p1.archived_at=datetime.now(UTC);self.p1.save();t=self.task()
        obj,form=collab.add_links(actor=self.creator,task_id=t.pk,data={'revision':t.updated_at.isoformat(),'events':[str(self.e3.pk)],'participants':[str(self.p1.pk)]});self.assertIsNone(obj)

    def test_comment_creation_author_and_task_audit_once(self):
        t=self.task();c=self.comment(t,actor=self.assignee);self.assertEqual(c.author,self.assignee);self.assertIsNotNone(c.created_at)
        log=AuditLog.objects.get(object_id=str(t.pk),action='operations.task.comment.created');self.assertEqual(log.actor,self.assignee);self.assertEqual(log.new_data['comment_text'],c.content)
        self.assertEqual(AuditLog.objects.filter(object_id=str(t.pk)).count(),2)

    def test_own_comment_edit_keeps_author_time_and_prior_content(self):
        t=self.task();c=self.comment(t);created=c.created_at;c=self.comment(t,text='SYNTHETIC edited',old=c)
        self.assertEqual(c.author,self.creator);self.assertEqual(c.created_at,created);self.assertEqual(c.edited_by,self.creator)
        log=AuditLog.objects.get(action='operations.task.comment.edited',object_id=str(t.pk));self.assertEqual(log.old_data['comment_text'],'SYNTHETIC comment');self.assertEqual(log.new_data['comment_text'],'SYNTHETIC edited')

    def test_admin_moderation_attributed_but_task_collaborator_cannot_edit_other(self):
        t=self.task();c=self.comment(t)
        with self.assertRaises(PermissionDenied):self.comment(t,actor=self.assignee,old=c,text='Not allowed')
        self.login(self.assignee);self.assertEqual(self.client.get(reverse('operations:comment-edit',args=[t.pk,c.pk])).status_code,403)
        created=c.created_at;c=self.comment(t,actor=self.admin,old=c,text='SYNTHETIC moderated')
        self.assertEqual(c.author,self.creator);self.assertEqual(c.created_at,created);self.assertEqual(c.edited_by,self.admin)
        self.assertEqual(AuditLog.objects.get(action='operations.task.comment.moderated',object_id=str(t.pk)).actor,self.admin)

    def test_noop_edit_and_stale_comment_revision(self):
        t=self.task();c=self.comment(t);count=AuditLog.objects.filter(object_id=str(t.pk)).count();self.comment(t,old=c)
        self.assertEqual(AuditLog.objects.filter(object_id=str(t.pk)).count(),count)
        self.comment(t,old=c,text='New text')
        with self.assertRaises(ValidationError):self.comment(t,old=c,text='Stale text')

    def test_comment_audit_failure_rolls_back(self):
        t=self.task()
        with patch('apps.operations.collaboration.record',side_effect=RuntimeError('SYNTHETIC failure')):
            with self.assertRaises(RuntimeError):self.comment(t)
        self.assertFalse(t.comments.exists())

    def test_archived_task_comments_readable_but_creation_and_edits_blocked(self):
        t=self.task();c=self.comment(t)
        t,form=svc.task_archive(actor=self.creator,task_id=t.pk,data={'revision':t.updated_at.isoformat()})
        with self.assertRaises(ValidationError):self.comment(t)
        with self.assertRaises(ValidationError):self.comment(t,old=c,text='Blocked')
        self.login(self.assignee);r=self.client.get(reverse('operations:task',args=[t.pk]));self.assertContains(r,c.content);self.assertNotContains(r,'>Edit comment</a>')
        t,form=svc.task_archive(actor=self.creator,task_id=t.pk,data={'revision':t.updated_at.isoformat()},restore=True);self.comment(t,old=c,text='Restored edit')

    def test_completion_and_reassignment_retain_comments_and_remove_old_access(self):
        t=self.task();c=self.comment(t,actor=self.assignee)
        t,form=svc.task_status(actor=self.assignee,task_id=t.pk,data={'revision':t.updated_at.isoformat(),'status':'completed'})
        data=dict(title=t.title,description='',assigned_staff=str(self.profiles[self.next_user.pk].pk),event='',due_date='',priority='normal',revision=t.updated_at.isoformat())
        t,form=svc.save_task(actor=self.creator,task_id=t.pk,data=data)
        self.login(self.assignee);self.assertEqual(self.client.get(reverse('operations:task',args=[t.pk])).status_code,404)
        with self.assertRaises(PermissionDenied):self.comment(t,actor=self.assignee,old=c,text='No longer permitted')
        self.login(self.next_user);self.assertContains(self.client.get(reverse('operations:task',args=[t.pk])),c.content)
        self.assertTrue(TaskComment.objects.filter(pk=c.pk).exists())

    def test_unified_timeline_order_pagination_and_safe_text(self):
        t=self.task();t=self.link(t,events=[self.e1]);c=self.comment(t,text='<script>SYNTHETIC</script>');self.comment(t,old=c,text='SYNTHETIC corrected')
        actions=list(task_history.entries_for(self.creator,t).values_list('action',flat=True))
        self.assertEqual(actions,['operations.task.created','operations.task.links.updated','operations.task.comment.created','operations.task.comment.edited'])
        for i in range(20):self.comment(t,text='SYNTHETIC page '+str(i))
        self.login(self.creator);r=self.client.get(reverse('operations:task',args=[t.pk]));self.assertEqual(len(r.context['history_rows']),20)
        self.assertContains(r,'&lt;script&gt;SYNTHETIC&lt;/script&gt;');self.assertNotContains(r,'<script>SYNTHETIC</script>');self.assertContains(r,'Next activity')
        r=self.client.get(reverse('operations:task',args=[t.pk])+'?history_page=2');self.assertEqual(len(r.context['history_rows']),4)

    def test_historical_audit_without_link_fields_remains_visible_and_redacted(self):
        t=self.task(assigned_staff=str(self.profiles[self.restricted.pk].pk))
        AuditLog.objects.create(actor=self.creator,content_type=ContentType.objects.get_for_model(Task),object_id=str(t.pk),action='operations.task.updated',old_data={'event_id':None},new_data={'event_id':str(self.e1.pk)})
        self.login(self.restricted);r=self.client.get(reverse('operations:task',args=[t.pk]));self.assertContains(r,'Restricted event');self.assertNotContains(r,self.e1.name)

    def test_forged_cross_task_link_and_comment_ids_rejected(self):
        t=self.task();other=self.link(self.task(),events=[self.e1]);comment=self.comment(other)
        self.login(self.creator)
        for url in [reverse('operations:task-link-remove',args=[t.pk,'event',other.event_links.get().pk]),reverse('operations:comment-edit',args=[t.pk,comment.pk])]:
            self.assertEqual(self.client.get(url).status_code,404);self.assertEqual(self.client.post(url,{}).status_code,404)

    def test_csrf_required_for_collaboration_mutations(self):
        t=self.link(self.task(),events=[self.e1]);c=self.comment(t);client=Client(enforce_csrf_checks=True);client.force_login(self.creator,backend='django.contrib.auth.backends.ModelBackend')
        for url in [reverse('operations:task-links',args=[t.pk]),reverse('operations:comment-add',args=[t.pk]),reverse('operations:comment-edit',args=[t.pk,c.pk]),reverse('operations:task-link-remove',args=[t.pk,'event',t.event_links.get().pk])]:self.assertEqual(client.post(url,{}).status_code,403)

    def test_http_link_comment_edit_and_archive_workflow(self):
        t=self.task(event=str(self.e1.pk));self.login(self.creator)
        r=self.client.post(reverse('operations:task-links',args=[t.pk]),{'revision':t.updated_at.isoformat(),'events':[str(self.e2.pk)],'participants':[str(self.p1.pk),str(self.p2.pk)]})
        self.assertEqual(r.status_code,302);t.refresh_from_db()
        r=self.client.post(reverse('operations:comment-add',args=[t.pk]),{'revision':t.updated_at.isoformat(),'content':'SYNTHETIC HTTP original'})
        self.assertEqual(r.status_code,302);c=t.comments.get()
        r=self.client.post(reverse('operations:comment-edit',args=[t.pk,c.pk]),{'revision':c.updated_at.isoformat(),'content':'SYNTHETIC HTTP revised'},follow=True)
        self.assertContains(r,'SYNTHETIC HTTP original');self.assertContains(r,'SYNTHETIC HTTP revised');self.assertContains(r,'Comment edited')
        link=t.participant_links.get(participant=self.p1)
        r=self.client.post(reverse('operations:task-link-remove',args=[t.pk,'participant',link.pk]),{'revision':t.updated_at.isoformat()})
        self.assertEqual(r.status_code,302);self.assertEqual(t.participant_links.count(),1)
        t.refresh_from_db();t,form=svc.task_archive(actor=self.creator,task_id=t.pk,data={'revision':t.updated_at.isoformat()})
        self.assertEqual(self.client.post(reverse('operations:comment-add',args=[t.pk]),{'revision':t.updated_at.isoformat(),'content':'Blocked'}).status_code,409)
        self.assertContains(self.client.get(reverse('operations:task',args=[t.pk])),'SYNTHETIC HTTP revised')
    def test_comment_length_and_empty_validation(self):
        t=self.task()
        for content in ['', 'x'*4001]:
            obj,form=collab.save_comment(actor=self.creator,task_id=t.pk,data={'revision':t.updated_at.isoformat(),'content':content});self.assertIsNone(obj);self.assertIn('content',form.errors)
