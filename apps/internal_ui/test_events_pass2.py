"""Pass 2: native navigation, accepted evidence, permissions and unchanged data."""
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, RequestFactory
from django.urls import reverse, resolve
from apps.events.models import Event, EventRegistration, Attendance
from apps.participants.models import Participant
from apps.core.models import AuditLog
from apps.staffing.models import StaffProfile, StaffAssignment, AssignmentDay
from apps.operations.execution_models import EventExecution
from apps.events.experience_operations import OPERATIONS_PERMISSIONS
from apps.internal_ui.event_presentation import directory_counts, clock_destination, event_tabs
from apps.operations.templatetags.event_ui import event_navigation


class CardParser(HTMLParser):
    def __init__(self):
        super().__init__();self.inside=False;self.cards=[];self.parts=[];self.nested=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='a':
            if self.inside:self.nested=True
            if 'event-directory-card' in attrs.get('class',''):
                self.inside=True;self.parts=[]
    def handle_data(self,data):
        if self.inside:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.inside:
            self.cards.append(' '.join(self.parts));self.inside=False


class EventsPassTwoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User=get_user_model()
        cls.owner=User.objects.create_superuser('SYNTHETIC-P2-Owner','p2@example.test','synthetic-test-only')
        cls.viewer=User.objects.create_user('SYNTHETIC-P2-Viewer',is_staff=True)
        cls.viewer.user_permissions.add(Permission.objects.get(content_type__app_label='events',codename='view_event'))
        cls.results_user=User.objects.create_user('SYNTHETIC-P2-Results',is_staff=True)
        for name in OPERATIONS_PERMISSIONS:
            app,code=name.split('.')
            cls.results_user.user_permissions.add(Permission.objects.get(content_type__app_label=app,codename=code))
        cls.assigned_user=User.objects.create_user('SYNTHETIC-P2-Assigned',is_staff=True)
        cls.profile=StaffProfile.objects.create(user=cls.assigned_user,first_name='SYNTHETIC',last_name='Assigned')
        now=datetime(2030,7,1,12,tzinfo=UTC)
        cls.event=Event.objects.create(name='SYNTHETIC P2 Event',status='upcoming',start_at=now,end_at=now+timedelta(days=1),timezone_name='America/New_York',location_name='SYNTHETIC Venue',city='Example City',housing_information='SYNTHETIC lodging\nSecond line')
        assignment=StaffAssignment.objects.create(event=cls.event,staff=cls.profile,status='confirmed',coverage_role='staff')
        AssignmentDay.objects.create(assignment=assignment,date=now.date(),starts_at=now,ends_at=now+timedelta(hours=4))

    def setUp(self):self.client.force_login(self.owner,backend='django.contrib.auth.backends.ModelBackend')
    def navigation(self,user,path=None,section='overview'):
        request=RequestFactory().get(path or reverse('events:event_detail',args=[self.event.pk]))
        request.user=user;request.resolver_match=resolve(request.path)
        return event_navigation({'request':request,'section':section},self.event)

    def test_approved_primary_order_and_all_previous_tools_have_destinations(self):
        nav=self.navigation(self.owner)
        self.assertEqual([t['label'] for t in nav['primary_event_tabs']],['Overview','Staff','Clock','Registrations & Tickets','Expenses','Reports & Results'])
        self.assertEqual(nav['event_nav_count'],7)
        more={t['label']:t['url'] for t in nav['more_event_tabs']}
        for label in ['Operations','Tasks','Notes','Documents','Quizzes & Tests','Station assignments','Game configuration','Simulator configuration references','Execution checklist','Health Monitor','Closeout','Primary Event Manager']:
            self.assertIn(label,more)
        for tab in event_tabs(self.owner,self.event):
            self.assertIn(tab['url'],[t['url'] for t in nav['primary_event_tabs']+nav['more_event_tabs']]+[t['url'] for t in nav['result_links']])

    def test_overview_and_secondary_selected_state_are_unambiguous(self):
        nav=self.navigation(self.owner)
        self.assertFalse(nav['more_active']);self.assertEqual(nav['active_event_tab'],'overview')
        path=reverse('events:event_detail',args=[self.event.pk])+'?section=operations'
        nav=self.navigation(self.owner,path,'operations')
        self.assertTrue(nav['more_active']);self.assertEqual(nav['active_more_label'],'Operations')
        path=reverse('operations:health',args=[self.event.pk])
        for section in ('health','operations'):
            nav=self.navigation(self.owner,path,section)
            self.assertEqual(nav['active_more_label'],'Health Monitor')
        nav=self.navigation(self.owner,reverse('workdays:event',args=[self.event.pk]))
        self.assertEqual(nav['active_event_tab'],'clock')
        self.assertFalse(nav['more_active'])

    def test_reports_and_results_share_primary_without_sharing_financial_authority(self):
        path=reverse('events:event_detail',args=[self.event.pk])+'?section=results'
        nav=self.navigation(self.results_user,path,'results')
        result=next(t for t in nav['primary_event_tabs'] if t['id']=='report-results')
        self.assertEqual(result['url'],path)
        self.assertNotIn('Expenses',[t['label'] for t in nav['primary_event_tabs']])
        self.client.force_login(self.results_user,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('event_reports:detail',args=[self.event.pk])).status_code,403)
        self.assertEqual(self.client.get(path).status_code,200)

    def test_events_only_role_has_no_staff_or_financial_links(self):
        nav=self.navigation(self.viewer)
        self.assertEqual([t['label'] for t in nav['primary_event_tabs']],['Overview'])
        self.assertEqual(directory_counts(self.viewer,[self.event]),{})
        self.client.force_login(self.viewer,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get('/events/')
        self.assertContains(response,'Registered count unavailable for your role')
        self.assertEqual(self.client.get(reverse('staffing:event',args=[self.event.pk])).status_code,403)

    def test_clock_reuses_existing_owned_or_manager_routes_only(self):
        self.assertEqual(clock_destination(self.owner,self.event)['url'],reverse('workdays:event',args=[self.event.pk]))
        self.assertEqual(clock_destination(self.assigned_user,self.event)['url'],reverse('workdays:checkin',args=[self.event.pk]))
        self.assertIsNone(clock_destination(self.viewer,self.event))
        self.client.force_login(self.assigned_user,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('workdays:checkin',args=[self.event.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('workdays:event',args=[self.event.pk])).status_code,403)

    def test_lifecycle_queries_remain_unchanged_while_owner_transition_is_pending(self):
        published=[]
        for offset in [-100,0,100]:
            e=Event.objects.create(name=f'SYNTHETIC date {offset}',status='upcoming',start_at=self.event.start_at+timedelta(days=offset),end_at=self.event.end_at+timedelta(days=offset))
            published.append(e)
        hidden=Event.objects.create(name='SYNTHETIC draft excluded',status='draft',start_at=self.event.start_at,end_at=self.event.end_at)
        with patch('apps.events.views.timezone') as event_clock:
            event_clock.now.return_value=self.event.start_at
            current=self.client.get('/events/',{'view':'current','q':'SYNTHETIC'})
            upcoming=self.client.get('/events/',{'view':'upcoming','q':'SYNTHETIC'})
            self.assertContains(current,published[1].name)
            self.assertNotContains(current,published[0].name)
            self.assertNotContains(current,published[2].name)
            self.assertContains(upcoming,published[2].name)
            self.assertNotContains(upcoming,published[0].name)
            self.assertNotContains(upcoming,published[1].name)
            self.assertNotContains(current,hidden.name)
            self.assertEqual(current.context['selected_view'],'current')
            self.assertEqual(upcoming.context['selected_view'],'upcoming')
        self.assertEqual(Event.objects.filter(status='upcoming').count(),4)

    def test_counts_are_distinct_valid_active_person_evidence(self):
        person=Participant.objects.create(first_name='SYNTHETIC',last_name='Registered')
        reg=EventRegistration.objects.create(event=self.event,participant=person)
        Attendance.objects.create(event=self.event,registration=reg,participant=person)
        Attendance.objects.create(event=self.event,registration=reg,participant=person,is_void=True)
        void=Participant.objects.create(first_name='SYNTHETIC',last_name='Voided')
        void_reg=EventRegistration.objects.create(event=self.event,participant=void,status='void')
        Attendance.objects.create(event=self.event,registration=void_reg,participant=void)
        demo=Participant.objects.create(first_name='SYNTHETIC',last_name='Test',kind='system_test')
        demo_reg=EventRegistration.objects.create(event=self.event,participant=demo)
        Attendance.objects.create(event=self.event,registration=demo_reg,participant=demo)
        other=Event.objects.create(name='SYNTHETIC Other',status='upcoming',start_at=self.event.start_at,end_at=self.event.end_at)
        wrong=Participant.objects.create(first_name='SYNTHETIC',last_name='Mismatched')
        wrong_reg=EventRegistration.objects.create(event=other,participant=wrong)
        Attendance.objects.create(event=self.event,registration=wrong_reg,participant=wrong)
        self.assertEqual(directory_counts(self.owner,[self.event])[self.event.pk],{'registered':1,'checked_in':1})

    def test_mobile_cards_have_only_four_approved_fields_and_native_links(self):
        response=self.client.get('/events/')
        parser=CardParser();parser.feed(response.content.decode())
        self.assertFalse(parser.nested);self.assertEqual(len(parser.cards),1)
        card=parser.cards[0]
        for text in [self.event.name,'SYNTHETIC Venue','Jul 1, 2030','Details in Progress']:self.assertIn(text,card)
        for text in ['Registered','Checked In','Upcoming','confirmed assignments','View daily coverage']:self.assertNotIn(text,card)
        self.assertContains(response,'<th scope="col">Checked In</th>')
        self.event.details_finalized_at=self.event.start_at;self.event.save()
        response=self.client.get('/events/');parser=CardParser();parser.feed(response.content.decode())
        self.assertIn('Details Finalized',parser.cards[0])

    def test_gets_leave_domain_records_audit_and_finalization_unchanged(self):
        before=(AuditLog.objects.count(),EventExecution.objects.count(),Event.objects.count(),EventRegistration.objects.count(),Participant.objects.count())
        state=(self.event.updated_at,self.event.details_finalized_at,self.event.status)
        for path in ['/events/',reverse('events:event_detail',args=[self.event.pk]),reverse('staffing:event',args=[self.event.pk]),reverse('workdays:event',args=[self.event.pk]),reverse('events:event_edit',args=[self.event.pk])]:
            self.assertEqual(self.client.get(path).status_code,200)
        self.event.refresh_from_db()
        self.assertEqual((self.event.updated_at,self.event.details_finalized_at,self.event.status),state)
        self.assertEqual((AuditLog.objects.count(),EventExecution.objects.count(),Event.objects.count(),EventRegistration.objects.count(),Participant.objects.count()),before)

    def test_existing_info_fields_and_dirty_guard_are_present_without_global_leak(self):
        response=self.client.get(reverse('events:event_detail',args=[self.event.pk]))
        for text in ['Venue','Address','Start','End','Time zone','Accommodation','SYNTHETIC lodging<br>Second line','event-info-pass2']:self.assertContains(response,text)
        self.assertContains(self.client.get(reverse('events:event_edit',args=[self.event.pk])),'data-event-dirty-guard')
        self.assertNotContains(self.client.get('/internal/'),'data-event-dirty-guard')
