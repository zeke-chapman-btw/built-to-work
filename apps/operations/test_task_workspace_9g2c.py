"""Disposable fixtures for workspace, quick creation, privacy and navigation."""
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.participants.models import Participant
from apps.staffing.models import StaffProfile, StaffAssignment
from .models import Task, TaskParticipantLink, TaskEventLink, TaskComment
from . import services, collaboration, workspace, quick_create

class TaskWorkspace9G2CTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.users={name:U.objects.create_user('synthetic-9g2c-'+name,is_staff=True) for name in ('owner','assignee','other','next','admin','restricted')}
        cls.profiles={name:StaffProfile.objects.create(first_name='SYNTHETIC',last_name=name,user=u) for name,u in cls.users.items()}
        cls.users['admin'].user_permissions.add(Permission.objects.get(content_type__app_label='operations',codename='manage_tasks'))
        perms=Permission.objects.filter(content_type__app_label__in=('events','participants'),codename__in=('view_event','view_participant'))
        for name in ('owner','assignee','admin'):cls.users[name].user_permissions.add(*perms)
        cls.event=Event.objects.create(name='SYNTHETIC Alpha',status='upcoming',start_at=datetime(2031,1,1,tzinfo=UTC),end_at=datetime(2031,1,2,tzinfo=UTC))
        cls.extra=Event.objects.create(name='SYNTHETIC Beta',status='upcoming',start_at=cls.event.start_at,end_at=cls.event.end_at)
        cls.archived_event=Event.objects.create(name='SYNTHETIC Archived',status='archived',start_at=cls.event.start_at,end_at=cls.event.end_at)
        cls.person=Participant.objects.create(first_name='SYNTHETIC Alice',last_name='Review',contact_email='private-synthetic@example.invalid',contact_phone='5550100')
        cls.person2=Participant.objects.create(first_name='SYNTHETIC Bob',last_name='Review')
        cls.archived_person=Participant.objects.create(first_name='SYNTHETIC Archived',last_name='Review',archived_at=timezone.now())
        cls.retired=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Retired',is_active=False)
        cls.unlinked=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Unlinked')
    def login(self,name='owner',client=None):
        (client or self.client).force_login(self.users[name],backend='django.contrib.auth.backends.ModelBackend')
    def task(self,creator='owner',assignee='assignee',**kw):
        if kw.get('status')=='completed':kw.setdefault('completed_at',timezone.now());kw.setdefault('completed_by',self.users[creator or assignee])
        return Task.objects.create(created_by=self.users[creator] if creator else None,assigned_staff=self.profiles[assignee],title=kw.pop('title','SYNTHETIC daily work'),**kw)
    def page(self,**kw):return self.client.get(reverse('operations:tasks'),kw)
    def ids(self,response):return {t.pk for t in response.context['tasks']}
    def data(self,**kw):
        self.login()
        form=self.client.get(reverse('operations:quick-create')).context['form']
        data={'title':'SYNTHETIC quick task','description':'Disposable description','assigned_staff':str(self.profiles['owner'].pk),'priority':'normal','due_date':'','revision':'new','creation_token':form.initial['creation_token']}
        data.update(kw);return data
    def post(self,data,url=None,modal=True):
        return self.client.post(url or reverse('operations:quick-create'),data,HTTP_X_TASK_MODAL='1' if modal else '')
    def test_mine_default_and_created_responsibility(self):
        mine=self.task(creator='other',assignee='owner');sent=self.task();unrelated=self.task(creator='other',assignee='next')
        self.login();self.assertEqual(self.ids(self.page()),{mine.pk});self.assertEqual(self.ids(self.page(view='created')),{sent.pk});self.assertEqual(self.ids(self.page(view='all')),{mine.pk,sent.pk})
        self.assertNotIn(unrelated.pk,self.ids(self.page(view='all')))
    def test_explicit_admin_all_and_is_staff_not_admin(self):
        t=self.task(creator='other',assignee='next');self.login();self.assertNotIn(t.pk,self.ids(self.page(view='all')))
        self.login('admin');self.assertIn(t.pk,self.ids(self.page(view='all')));self.assertContains(self.page(view='all'),'id="task-assignee"')
    def test_reassignment_previous_assignee_loses_and_creator_retains(self):
        t=self.task();Task.objects.filter(pk=t.pk).update(assigned_staff=self.profiles['next'])
        self.login('assignee');self.assertNotIn(t.pk,self.ids(self.page(view='all')))
        self.assertEqual(self.client.get(reverse('operations:task',args=[t.pk])).status_code,404)
        self.login();self.assertIn(t.pk,self.ids(self.page(view='created')))
        self.login('next');self.assertIn(t.pk,self.ids(self.page()))
    def test_legacy_missing_creator_retains_assignment(self):
        t=self.task(creator=None,assignee='owner');self.login();self.assertEqual(self.ids(self.page()),{t.pk});self.assertEqual(self.ids(self.page(view='created')),set());t.refresh_from_db();self.assertIsNone(t.created_by)
    def test_archived_is_separate_and_cancelled_preserved(self):
        active=self.task(assignee='owner',status='cancelled');archived=self.task(assignee='owner',archived_at=timezone.now())
        self.login();self.assertEqual(self.ids(self.page()),{active.pk});self.assertEqual(self.ids(self.page(view='archived')),{archived.pk});self.assertEqual(self.ids(self.page(status='cancelled')),{active.pk})
    def test_filters_priority_status_search_and_admin_assignment(self):
        match=self.task(priority='high',description='prepare orientation',status='in_progress');self.task(priority='low')
        self.login();self.assertEqual(self.ids(self.page(view='all',q='orientation',priority='high',status='in_progress')),{match.pk})
        self.assertNotContains(self.page(view='all'),'id="task-assignee"')
        self.login('admin');self.assertEqual(self.ids(self.page(view='all',assignee=str(self.profiles['assignee'].pk),priority='high')),{match.pk})
        self.assertEqual(self.ids(self.page(view='all',assignee='bad')),set())
    def test_due_filter_order_and_overdue_presentation(self):
        today=timezone.localdate();late=self.task(assignee='owner',title='SYNTHETIC late',due_date=today-timedelta(days=1));today_task=self.task(assignee='owner',due_date=today);done=self.task(assignee='owner',status='completed',due_date=today-timedelta(days=5));undated=self.task(assignee='owner')
        self.login();r=self.page();self.assertEqual(list(r.context['tasks'])[0].pk,late.pk);self.assertContains(r,'Overdue');self.assertEqual(self.ids(self.page(due='overdue')),{late.pk});self.assertEqual(self.ids(self.page(due='today')),{today_task.pk});self.assertEqual(self.ids(self.page(due='undated')),{undated.pk});self.assertFalse(next(t for t in r.context['tasks'] if t.pk==done.pk).is_overdue)
    def test_pagination_keeps_filters_and_safe_return_context(self):
        for i in range(23):self.task(assignee='owner',title='SYNTHETIC needle '+str(i),priority='high')
        self.login();r=self.page(q='needle',priority='high',page=2);self.assertEqual(len(r.context['tasks']),3);self.assertContains(r,'priority=high');self.assertContains(r,'return_to=')
        t=list(r.context['tasks'])[0];detail=self.client.get(t.detail_url);self.assertIn('q=needle',detail.context['task_back_url']);self.assertIn('page=2',detail.context['task_back_url'])
    def test_return_navigation_rejects_external_and_malformed_paths(self):
        for value in ('https://example.invalid/','//example.invalid/','http://[invalid','/admin/','/operations/tasks/'+chr(92)+'evil','/operations/tasks/'+chr(13)+chr(10)+'evil'):
            self.assertEqual(workspace.return_path(value),reverse('operations:tasks'))
        self.assertEqual(workspace.return_path('/operations/tasks/?view=all&secret=drop&q=safe#bad'),'/operations/tasks/?view=all&q=safe')
    def test_quick_get_modal_and_full_fallback_assets(self):
        self.login();url=reverse('operations:quick-create');full=self.client.get(url);self.assertContains(full,'data-task-form');self.assertContains(full,'task_create.js');self.assertContains(full,'csrfmiddlewaretoken');self.assertContains(full,'task-create-dialog')
        self.assertContains(full,'SYNTHETIC Alpha');self.assertContains(full,'SYNTHETIC Alice Review');self.assertContains(full,'id="id_assigned_staff_helptext"')
        fragment=self.client.get(url,HTTP_X_TASK_MODAL='1');self.assertContains(fragment,'data-task-form');self.assertNotContains(fragment,'<html');self.assertEqual(fragment['Cache-Control'],'no-store')
    def test_quick_create_self_assignment_and_success_destination(self):
        r=self.post(self.data(return_to='/operations/tasks/?view=created&priority=high'));self.assertEqual(r.status_code,201,r.content);t=Task.objects.get();self.assertEqual(t.created_by,self.users['owner']);self.assertEqual(t.assigned_staff,self.profiles['owner']);self.assertIn('return_to=',r.json()['url']);self.assertEqual(AuditLog.objects.count(),1)
    def test_full_page_fallback_redirects_to_created_task(self):
        r=self.post(self.data(),modal=False);self.assertEqual(r.status_code,302);self.assertIn(str(Task.objects.get().pk),r.url)
    def test_invalid_preserves_values_and_has_no_writes(self):
        r=self.post(self.data(title='',description='Retain these words',participants=[str(self.person.pk)]));self.assertEqual(r.status_code,400);self.assertContains(r,'Retain these words',status_code=400);self.assertContains(r,'data-task-errors',status_code=400);self.assertEqual(str(r.context['form']['participants'].value()[0]),str(self.person.pk));self.assertEqual(Task.objects.count(),0);self.assertEqual(AuditLog.objects.count(),0)
    def test_eligible_assignees_and_ineligible_post_denied(self):
        self.login();f=self.client.get(reverse('operations:quick-create')).context['form'];ids=set(f.fields['assigned_staff'].queryset.values_list('pk',flat=True));self.assertIn(self.profiles['owner'].pk,ids);self.assertNotIn(self.retired.pk,ids);self.assertNotIn(self.unlinked.pk,ids)
        for p in (self.retired,self.unlinked):self.assertEqual(self.post(self.data(assigned_staff=str(p.pk))).status_code,400)
        self.assertEqual(Task.objects.count(),0)
    def test_csrf_protection_preserved(self):
        strict=Client(enforce_csrf_checks=True);self.login(client=strict);self.assertEqual(strict.post(reverse('operations:quick-create'),self.data()).status_code,403)
    def test_duplicate_retry_one_task_and_one_creation_audit(self):
        data=self.data();first=self.post(data);data['title']='Must not overwrite';second=self.post(data);self.assertEqual(first.status_code,201);self.assertEqual(second.status_code,200);self.assertTrue(second.json()['replayed']);self.assertEqual(Task.objects.count(),1);self.assertEqual(Task.objects.get().title,'SYNTHETIC quick task');self.assertEqual(AuditLog.objects.count(),1)
    def test_missing_tampered_expired_and_cross_actor_tokens(self):
        for token in ('','forged',quick_create.new_token(self.users['other'])):
            self.assertEqual(self.post(self.data(creation_token=token)).status_code,400)
        data=self.data()
        with patch('django.core.signing.time.time',return_value=timezone.now().timestamp()+7300):self.assertEqual(self.post(data).status_code,400)
        self.assertEqual(Task.objects.count(),0)
    def test_multiple_events_and_participants_and_no_private_lookup_fields(self):
        r=self.post(self.data(events=[str(self.event.pk),str(self.extra.pk)],participants=[str(self.person.pk),str(self.person2.pk)]));self.assertEqual(r.status_code,201,r.content);t=Task.objects.get();self.assertIsNone(t.event);self.assertEqual(t.event_links.count(),2);self.assertEqual(t.participant_links.count(),2)
        lookup=self.client.get(reverse('operations:task-record-lookup'),{'kind':'participants','q':'Alice'});self.assertEqual(len(lookup.json()['results']),1);self.assertNotContains(lookup,'private-synthetic');self.assertNotContains(lookup,'5550100');self.assertEqual(set(lookup.json()['results'][0]),{'id','label'})
    def test_selected_search_result_beyond_initial_twenty_is_validated(self):
        for i in range(25):Participant.objects.create(first_name='SYNTHETIC '+str(i),last_name='A first')
        r=self.post(self.data(participants=[str(self.person.pk)]));self.assertEqual(r.status_code,201,r.content);self.assertEqual(TaskParticipantLink.objects.get().participant,self.person)
        lookup=self.client.get(reverse('operations:task-record-lookup'),{'kind':'participants'});self.assertEqual(len(lookup.json()['results']),20)
    def test_event_context_primary_is_fixed_with_extra_links(self):
        r=self.post(self.data(event=str(self.extra.pk),events=[str(self.extra.pk)]),reverse('operations:quick-create-event',args=[self.event.pk]));self.assertEqual(r.status_code,201,r.content);t=Task.objects.get();self.assertEqual(t.event,self.event);self.assertEqual(list(t.event_links.values_list('event_id',flat=True)),[self.extra.pk])
    def test_participant_context_mandatory_even_if_deselected(self):
        r=self.post(self.data(participants=[str(self.person2.pk)]),reverse('operations:quick-create-participant',args=[self.person.pk]));self.assertEqual(r.status_code,201,r.content);self.assertEqual(set(TaskParticipantLink.objects.values_list('participant_id',flat=True)),{self.person.pk,self.person2.pk})
    def test_forged_context_archived_and_restricted_ids_not_revealed(self):
        self.login('restricted')
        for url in (reverse('operations:quick-create-event',args=[self.event.pk]),reverse('operations:quick-create-participant',args=[self.person.pk])):
            response=self.client.get(url);self.assertEqual(response.status_code,404);self.assertNotContains(response,'SYNTHETIC Alpha',status_code=404);self.assertNotContains(response,'SYNTHETIC Alice',status_code=404)
        self.login()
        for url in (reverse('operations:quick-create-event',args=[self.archived_event.pk]),reverse('operations:quick-create-participant',args=[self.archived_person.pk])):self.assertEqual(self.client.get(url).status_code,404)
    def test_forged_links_and_scoped_lookup(self):
        data=self.data(events=[str(self.event.pk)],participants=[str(self.person.pk)]);self.login('restricted');data['creation_token']=quick_create.new_token(self.users['restricted']);data['assigned_staff']=str(self.profiles['restricted'].pk)
        self.assertEqual(self.post(data).status_code,400);self.assertEqual(Task.objects.count(),0)
        for kind in ('events','participants'):self.assertEqual(self.client.get(reverse('operations:task-record-lookup'),{'kind':kind}).json()['results'],[])
    def test_link_audit_failure_rolls_back_creation(self):
        data=self.data(events=[str(self.event.pk)])
        with patch('apps.operations.collaboration.record',side_effect=ValidationError('Synthetic audit failure')):r=self.post(data)
        self.assertEqual(r.status_code,400);self.assertEqual(Task.objects.count(),0);self.assertEqual(TaskEventLink.objects.count(),0);self.assertEqual(AuditLog.objects.count(),0)
    def test_task_indicators_redact_linked_records(self):
        t=self.task(creator='restricted',assignee='restricted',event=self.event);TaskParticipantLink.objects.create(task=t,participant=self.person)
        self.login('restricted');r=self.page();self.assertContains(r,'restricted');self.assertNotContains(r,'SYNTHETIC Alpha');self.assertNotContains(r,'SYNTHETIC Alice')
    def test_comments_history_and_return_context_survive(self):
        t=self.task();comment,form=collaboration.save_comment(actor=self.users['owner'],task_id=t.pk,data={'revision':t.updated_at.isoformat(),'content':'SYNTHETIC retained comment'});self.assertIsNotNone(comment,form.errors)
        self.login();back='/operations/tasks/?view=created&priority=high';r=self.client.get(reverse('operations:task',args=[t.pk]),{'return_to':back});self.assertContains(r,'SYNTHETIC retained comment');self.assertEqual(r.context['task_back_url'],back);self.assertContains(r,'return_to=')
        t.refresh_from_db();r=self.client.post(reverse('operations:task-status',args=[t.pk]),{'revision':t.updated_at.isoformat(),'status':'completed','return_to':back});self.assertEqual(r.status_code,302);self.assertIn('return_to=',r.url);self.assertEqual(TaskComment.objects.get().content,'SYNTHETIC retained comment')
    def test_context_tags_only_render_authorized_actions(self):
        from django.template import Template, Context
        from django.test import RequestFactory
        template=Template('{% load task_ui %}{% task_create_action event=event as action %}{{ action }}')
        request=RequestFactory().get('/');request.user=self.users['owner'];self.assertIn('/quick-create/event/',template.render(Context({'request':request,'event':self.event})))
        request.user=self.users['restricted'];self.assertEqual(template.render(Context({'request':request,'event':self.event})), '')
    def test_tasks_navigation_is_unique_active_workspace(self):
        from apps.internal_ui.navigation import visible_workspaces
        for path in ('/operations/tasks/','/operations/tasks/quick-create/','/operations/my-tasks/'):
            nav=visible_workspaces(self.users['owner'],path);self.assertEqual([row['id'] for row in nav if row['active']],['tasks'])
    def test_additional_event_filter_includes_task_and_no_privacy_leak(self):
        t=self.task();TaskEventLink.objects.create(task=t,event=self.extra);self.login('admin');self.assertEqual(self.ids(self.page(view='all',event=str(self.extra.pk))),{t.pk})
        self.login('restricted');self.assertNotContains(self.page(view='all'),'SYNTHETIC Beta')
