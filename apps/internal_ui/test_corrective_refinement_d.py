"""Pass D: read-only facts, original permissions/evidence and compact UI contracts."""
from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, SimpleTestCase
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.operations.execution_models import EventExecution, EventChecklistCategory, EventChecklistItem
from apps.operations.overview_presentation import operational_events, overview_rows, end_attention_events
from apps.operations.execution_services import can_end_event
from apps.operations.models import Task
from apps.staffing.models import StaffProfile
from apps.participants.templatetags.people_ui import people_score, people_activity_label
from apps.operations.templatetags.event_ui import event_status_label


class NamedControls(HTMLParser):
    def __init__(self):
        super().__init__(); self.names=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag in ('input','select','textarea') and attrs.get('name'):
            self.names.append(attrs['name'])


class CorrectiveDDisplayTests(SimpleTestCase):
    def test_score_display_keeps_human_precision_and_original_evidence(self):
        for raw, label in [('83.330000','83.33%'),('100.000000','100%'),('0.000000','0%'),('83.339999','83.34%')]:
            value=Decimal(raw)
            self.assertEqual(people_score(value),label)
            self.assertEqual(str(value),raw)
        self.assertEqual(people_score(None),'Unavailable')
        self.assertEqual(people_score(Decimal('NaN')),'Unavailable')
        rendered=render_to_string('participants/score_summary.html',{'result':dict(total_score=Decimal('83.330000'),get_status_display='Accepted',get_mode_display='Official')})
        self.assertIn('83.33%',rendered);self.assertIn('83.330000',rendered)
        self.assertIn('Score source',rendered);self.assertNotIn('Unavailable%',rendered)

    def test_audit_label_is_readable_without_mutating_source(self):
        raw='Portal Profile_Updated'
        self.assertEqual(people_activity_label(raw),'Portal profile updated')
        self.assertEqual(raw,'Portal Profile_Updated')

    def test_temporal_labels_preserve_lifecycle_at_exact_boundaries(self):
        now=timezone.now()
        event=Event(status='upcoming',start_at=now,end_at=now+timedelta(hours=2))
        with patch('django.utils.timezone.now',return_value=now):
            self.assertEqual(event_status_label(event),'Current')
            event.start_at=now+timedelta(seconds=1)
            self.assertEqual(event_status_label(event),'Upcoming')
            event.start_at=now-timedelta(days=1);event.end_at=now-timedelta(seconds=1)
            self.assertEqual(event_status_label(event),'Scheduled window passed')
        self.assertEqual(event.status,'upcoming')
        for status,label in [('completed','Ended'),('draft','Draft'),('cancelled','Cancelled'),('archived','Archived')]:
            event.status=status;self.assertEqual(event_status_label(event),label)


class CorrectiveDWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model();cls.admin=U.objects.create_superuser('synthetic-d-admin',email='d@example.invalid',password=None)
        cls.manager=U.objects.create_user('synthetic-d-manager',is_staff=True)
        cls.other=U.objects.create_user('synthetic-d-other',is_staff=True)
        cls.profile=StaffProfile.objects.create(user=cls.manager,first_name='Synthetic',last_name='Manager')
        now=timezone.now()
        def event(name,start,end,status='upcoming'):
            return Event.objects.create(name=name,status=status,start_at=now+timedelta(days=start),end_at=now+timedelta(days=end))
        cls.current=event('SYNTHETIC Current',-1,1);cls.past=event('SYNTHETIC End follow-up',-5,-4)
        cls.future=event('SYNTHETIC Future',3,4);cls.closed=event('SYNTHETIC Closed',-10,-9,'completed')
        cls.pending=event('SYNTHETIC Pending closeout',-8,-7,'completed')
        cls.history=event('SYNTHETIC Historical',-30,-29,'completed')
        cls.cancelled=event('SYNTHETIC Cancelled far future',1000,1001,'cancelled')
        cls.archived=event('SYNTHETIC Archived',-40,-39,'archived')
        cls.snapshot=EventExecution.objects.create(event=cls.current,primary_manager=cls.profile)
        cls.past_snapshot=EventExecution.objects.create(event=cls.past,primary_manager=cls.profile)
        EventExecution.objects.create(event=cls.pending,actual_ended_at=now-timedelta(days=7))
        EventExecution.objects.create(event=cls.closed,actual_ended_at=now-timedelta(days=9),closed_at=now-timedelta(days=8))
        category=EventChecklistCategory.objects.create(execution=cls.snapshot,title='Synthetic preparation',phase='prepare')
        EventChecklistItem.objects.create(category=category,title='Completed check',state='done',completed_at=now)
        EventChecklistItem.objects.create(category=category,title='Open check')
        EventChecklistItem.objects.create(category=category,title='Not applicable',state='na',na_reason='Synthetic case')
        cls.task=Task.objects.create(title='SYNTHETIC D task',created_by=cls.manager,assigned_staff=cls.profile,priority='high')

    def login(self,user=None):
        self.client.force_login(user or self.admin,backend='django.contrib.auth.backends.ModelBackend')

    def test_operational_order_retains_only_known_active_or_closeout_work(self):
        self.assertEqual(list(operational_events(self.admin).values_list('pk',flat=True)),
            [self.current.pk,self.past.pk,self.pending.pk,self.future.pk])

    def test_uninitialized_and_empty_snapshots_never_become_ready(self):
        rows=overview_rows(self.admin,[self.current,self.future,self.past])
        self.assertIsNone(rows[1]['execution']);self.assertEqual(rows[1]['indicators'],[])
        ready=rows[0]['indicators'][0]
        self.assertEqual((ready['completed'],ready['total'],ready['na']),(1,2,1))
        self.assertIsNone(rows[2]['indicators'][0]['percent'])
        self.login()
        response=self.client.get(reverse('internal:workspace',args=['operations']))
        # Detailed checklist facts remain on Execution; the compact landing shows resource evidence.
        execution=self.client.get(reverse('operations:execution-index'))
        self.assertContains(execution,'Checklist not initialized · readiness not established.')
        self.assertContains(execution,'No applicable items')
        self.assertContains(execution,'1 / 2 complete')
        self.assertNotContains(response,'100% ready')

    def test_operations_get_does_not_initialize_or_write_domain_records(self):
        self.login()
        models=(EventExecution,EventChecklistCategory,EventChecklistItem,Task,AuditLog)
        before=[m.objects.count() for m in models]
        original=list(Event.objects.order_by('pk').values())
        for url in [reverse('internal:workspace',args=['operations']),reverse('operations:execution-index'),reverse('internal:home')]:
            self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(before,[m.objects.count() for m in models])
        self.assertEqual(original,list(Event.objects.order_by('pk').values()))

    def test_default_execution_list_exposes_all_lifecycle_history_explicitly(self):
        self.login()
        url=reverse('operations:execution-index')
        default=self.client.get(url)
        self.assertContains(default,self.current.name);self.assertNotContains(default,self.cancelled.name)
        all_events=self.client.get(url,{'view':'all'})
        for event in [self.history,self.cancelled,self.archived]:self.assertContains(all_events,event.name)
        for status,event in [('completed',self.history),('cancelled',self.cancelled),('archived',self.archived)]:
            filtered=self.client.get(url,{'status':status})
            self.assertContains(filtered,event.name)

    def test_existing_manager_and_retired_manager_end_attention_boundary(self):
        self.assertEqual(list(end_attention_events(self.manager)),[self.past])
        self.assertTrue(can_end_event(self.manager,self.past.pk))
        self.assertFalse(end_attention_events(self.other).exists())
        self.profile.is_active=False;self.profile.save(update_fields=['is_active'])
        self.assertFalse(end_attention_events(self.manager).exists())

    def test_home_attention_is_exact_existing_candidate_and_guard(self):
        from apps.internal_ui.home_data import home_data
        rows=home_data(self.manager)['home_attention']
        end=next(x for x in rows if x['id']=='event-end')
        self.assertEqual(end['count'],1)
        self.assertEqual(end['url'],reverse('operations:execution-index')+'?view=attention')
        self.assertFalse(any(x['id']=='event-end' for x in home_data(self.other)['home_attention']))
        self.login(self.manager)
        response=self.client.get(end['url'])
        self.assertContains(response,self.past.name);self.assertNotContains(response,self.future.name)

    def test_unauthorized_overview_and_readiness_information_is_absent(self):
        self.assertEqual(list(operational_events(self.other)),[])
        self.login(self.other)
        response=self.client.get(reverse('operations:execution-index'))
        self.assertNotContains(response,self.current.name);self.assertNotContains(response,self.future.name)
        self.assertEqual(self.client.get(reverse('operations:execution',args=[self.current.pk])).status_code,403)

    def test_task_disclosure_retains_every_filter_once_and_private_tasks(self):
        self.login(self.manager)
        response=self.client.get(reverse('operations:tasks'),{'view':'mine','priority':'high'})
        self.assertEqual(response.status_code,200)
        parser=NamedControls();parser.feed(response.content.decode())
        for name in ['view','q','status','due','priority']:self.assertEqual(parser.names.count(name),1)
        self.assertNotIn('assignee',parser.names);self.assertNotIn('event',parser.names)
        self.assertContains(response,'class="filter-advanced" open')
        self.assertContains(response,self.task.title)
        self.assertContains(response,'priority%3Dhigh')

    def test_admin_filter_values_and_reset_remain_available(self):
        self.login()
        response=self.client.get(reverse('operations:tasks'),{'view':'all','status':'todo','due':'undated','priority':'high','assignee':str(self.profile.pk)})
        self.assertContains(response,self.task.title)
        parser=NamedControls();parser.feed(response.content.decode())
        for name in ['assignee','event','status','due','priority']:self.assertEqual(parser.names.count(name),1)
        self.assertContains(response,'Active filters');self.assertContains(response,'Clear filters')

    def test_operations_tools_stay_permission_aware_below_readiness(self):
        self.login()
        response=self.client.get(reverse('internal:workspace',args=['operations']))
        html=response.content.decode()
        self.assertLess(html.index('aria-label="Operations sections"'),html.index('Event Readiness'))
        for route in ['operations:execution-index','participants:intake-config-index','participants:consent-index']:
            self.assertContains(response,reverse(route))

    def test_original_audit_action_is_retained_in_timeline(self):
        from apps.participants.models import Participant
        from apps.participants.workspace_views import factual_timeline
        person=Participant.objects.create(kind='person',first_name='SYNTHETIC',last_name='Audit')
        log=AuditLog.objects.create(content_type=ContentType.objects.get_for_model(Participant),object_id=str(person.pk),action='participant.portal.profile_updated',actor=self.admin)
        ctx={key:False for key in ['can_view_registrations','can_view_documents','can_view_quiz_results','can_view_game_results','can_view_simulator_results','can_view_verification','can_view_internal_review','can_view_notes']}
        rows=factual_timeline(self.admin,person,[person.pk],ctx)
        row=next(x for x in rows if x['key']==str(log.pk))
        self.assertEqual(row['source_action'],'participant.portal.profile_updated')
        log.refresh_from_db();self.assertEqual(log.action,'participant.portal.profile_updated')
