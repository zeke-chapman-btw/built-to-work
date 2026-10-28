"""Reference-aligned Operations presentation over authoritative existing resources."""
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection, DatabaseError
from django.test import TestCase, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse, resolve
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.games.models import GameDefinition, EventGameConfiguration
from apps.operations.execution_models import EventExecution, EventChecklistItem
from apps.operations.models import Task
from apps.operations.overview_presentation import overview_rows, resource_overview_rows
from apps.operations.templatetags.section_ui import operations_navigation
from apps.staffing.models import StaffProfile, StaffAssignment
from apps.stations.models import Station
from apps.simulators.models import SimulatorCaptureProfile, EventSimulatorConfiguration


class OperationsReferenceRefinementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.admin=U.objects.create_superuser('synthetic-ops-reference',email='ops@example.invalid',password=None)
        cls.staff=U.objects.create_user('synthetic-ops-reference-staff',is_staff=True)
        cls.person=StaffProfile.objects.create(user=cls.staff,first_name='SYNTHETIC',last_name='Assigned staff')
        now=timezone.now()
        cls.event=Event.objects.create(name='SYNTHETIC Current reference event',status='upcoming',start_at=now-timedelta(hours=1),end_at=now+timedelta(hours=2),city='SYNTHETIC City')
        cls.future=Event.objects.create(name='SYNTHETIC Upcoming reference event',status='upcoming',start_at=now+timedelta(days=2),end_at=now+timedelta(days=3))
        cls.game=GameDefinition.objects.create(key='synthetic-reference-game',display_name='SYNTHETIC Private game definition',implementation_key='duck_hunt',implementation_version='7')
        EventGameConfiguration.objects.create(event=cls.event,game=cls.game)
        station=Station.objects.create(code='synthetic-reference-sim',name='SYNTHETIC Simulator',station_type='simulator')
        profile=SimulatorCaptureProfile.objects.create(name='SYNTHETIC Calibration',simulator_type='SYNTHETIC')
        EventSimulatorConfiguration.objects.create(event=cls.event,station=station,profile=profile,simulator_type='SYNTHETIC')
        StaffAssignment.objects.create(event=cls.event,staff=cls.person,status='confirmed')

    def login(self,user=None):
        self.client.force_login(user or self.admin,backend='django.contrib.auth.backends.ModelBackend')

    def nav(self,url,user=None):
        request=RequestFactory().get(url);request.user=user or self.admin;request.resolver_match=resolve(request.path)
        return operations_navigation({'request':request})

    def test_direct_tabs_follow_reference_order_and_keep_more_destinations(self):
        url=reverse('internal:workspace',args=['operations'])
        nav=self.nav(url)
        self.assertEqual([x['label'] for x in nav['primary_tabs']],['Overview','Stations','Quizzes','Games','Forms','Consents'])
        self.assertTrue(nav['primary_tabs'][0]['active'])
        self.assertEqual(next(x for x in nav['primary_tabs'] if x['label']=='Games')['url'],url+'?resource=games')
        remaining=[x for group in nav['groups'] for x in group['links']]
        for route in ['operations:execution-index','operations:templates','operations:trailers','admin:games_gamedefinition_changelist']:
            self.assertIn(reverse(route),[x['url'] for x in remaining])

    def test_tabs_keep_permission_boundaries_and_active_deep_links(self):
        nav=self.nav(reverse('internal:workspace',args=['operations']),self.staff)
        self.assertEqual([x['label'] for x in nav['primary_tabs']],['Overview'])
        nav=self.nav(reverse('participants:intake-config-index'))
        self.assertEqual([x['label'] for x in nav['primary_tabs'] if x['active']],['Forms'])
        nav=self.nav(reverse('assessments:question_set_list'))
        self.assertEqual([x['label'] for x in nav['primary_tabs'] if x['active']],['Quizzes'])
        nav=self.nav(reverse('internal:workspace',args=['operations'])+'?resource=games')
        self.assertEqual([x['label'] for x in nav['primary_tabs'] if x['active']],['Games'])

    def test_resource_states_reuse_configuration_without_certifying_readiness(self):
        rows=resource_overview_rows(self.admin,overview_rows(self.admin,[self.event,self.future]))
        states={item['label']:item['state'] for item in rows[0]['resource_checks']}
        self.assertEqual(states,{'Forms':'Unknown','Quizzes':'Not set','Games':'Configured','Simulators':'Configured'})
        self.assertTrue(all(item['state']!='Ready' for row in rows for item in row['resource_checks']))
        self.assertEqual(next(x for x in rows[1]['resource_checks'] if x['label']=='Simulators')['state'],'Not set')
        self.assertEqual(EventExecution.objects.count(),0)

    def test_no_assignment_and_source_failure_stay_unknown(self):
        with patch('apps.participants.intake_models.IntakeFormAssignment.objects.filter',side_effect=DatabaseError('synthetic source unavailable')):
            rows=resource_overview_rows(self.admin,overview_rows(self.admin,[self.event]))
        self.assertEqual(rows[0]['resource_checks'][0]['state'],'Unknown')
        with patch('apps.operations.setup_presentation.setup_context',side_effect=DatabaseError('synthetic source unavailable')):
            rows=resource_overview_rows(self.admin,overview_rows(self.admin,[self.event]))
        self.assertEqual([x['state'] for x in rows[0]['resource_checks']][1:],['Unknown','Unknown','Unknown'])

    def test_restricted_assignee_does_not_query_or_expose_resource_configuration(self):
        with CaptureQueriesContext(connection) as queries:
            rows=resource_overview_rows(self.staff,overview_rows(self.staff,[self.event]))
        self.assertEqual([x['state'] for x in rows[0]['resource_checks']],['Restricted']*4)
        self.assertTrue(all(x['url'] is None for x in rows[0]['resource_checks']))
        sql=' '.join(q['sql'] for q in queries)
        for table in ['participants_intakeformassignment','games_eventgameconfiguration','simulators_eventsimulatorconfiguration']:
            self.assertNotIn('FROM "'+table+'"',sql)
        self.login(self.staff)
        response=self.client.get(reverse('internal:workspace',args=['operations']))
        self.assertContains(response,self.event.name);self.assertNotContains(response,self.future.name)
        self.assertNotContains(response,self.game.display_name)

    def test_landing_is_compact_and_detailed_execution_stays_reachable(self):
        self.login();response=self.client.get(reverse('internal:workspace',args=['operations']))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Event Readiness');self.assertContains(response,'ops-resource-event-row')
        self.assertContains(response,'ops-resource-checks');self.assertContains(response,'About these statuses')
        self.assertNotContains(response,'ops-overview-progress');self.assertNotContains(response,'Tools &amp; configuration')
        self.assertNotContains(response,'>Ready<')
        self.assertContains(response,reverse('events:event_detail',args=[self.event.pk]))
        self.assertContains(response,reverse('operations:execution-index'))
        self.assertContains(response,'Simulator Scoring')
        self.assertContains(response,reverse('events:event_detail',args=[self.future.pk])+'?section=results')
        self.assertEqual(self.client.get(reverse('operations:health',args=[self.event.pk])).status_code,200)

    def test_games_catalogue_is_read_only_and_uses_existing_guard(self):
        url=reverse('internal:workspace',args=['operations'])+'?resource=games'
        self.login();response=self.client.get(url)
        self.assertEqual(response.status_code,200);self.assertContains(response,self.game.display_name)
        self.assertNotContains(response,'duck_hunt');self.assertContains(response,'7')
        self.assertContains(response,reverse('admin:games_gamedefinition_changelist'))
        self.assertContains(response,'name="resource" value="games"')
        self.assertContains(response,'href="'+url+'">Clear')
        filtered=self.client.get(url+'&q=no-match-synthetic')
        self.assertEqual(filtered.status_code,200)
        self.assertContains(filtered,'No matching games.')
        self.assertNotContains(filtered,self.game.display_name)
        self.assertEqual(self.client.post(url,{'display_name':'should not save'}).status_code,405)
        self.game.refresh_from_db();self.assertEqual(self.game.display_name,'SYNTHETIC Private game definition')
        self.login(self.staff);self.assertEqual(self.client.get(url).status_code,403)

    def test_overview_and_games_get_never_write_domain_or_audit_records(self):
        from apps.participants.models import IntakeFormAssignment
        self.login()
        models=(AuditLog,EventExecution,EventChecklistItem,Task,GameDefinition,EventGameConfiguration,EventSimulatorConfiguration,IntakeFormAssignment)
        before=[list(m.objects.order_by('pk').values()) for m in models]
        for suffix in ['', '?resource=games']:
            self.assertEqual(self.client.get(reverse('internal:workspace',args=['operations'])+suffix).status_code,200)
        self.assertEqual(before,[list(m.objects.order_by('pk').values()) for m in models])
