"""Build 10A presentation and access regressions using synthetic records."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.events.models import Event
from apps.staffing.models import StaffProfile, StaffAssignment
from apps.operations.models import Task, EventNote
from apps.internal_ui.event_presentation import operations_home, event_tabs, event_tools
from apps.internal_ui.navigation import visible_workspaces, visible_workspace_links

class Build10ATests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.staff=U.objects.create_user('synthetic-10a-staff',is_staff=True)
        cls.admin=U.objects.create_superuser('synthetic-10a-admin',email='review@example.test',password=None)
        cls.profile=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Staff',user=cls.staff)
        now=timezone.now()
        cls.next=Event.objects.create(name='SYNTHETIC Next Mixed Case',status='upcoming',start_at=now+timedelta(days=2),end_at=now+timedelta(days=2,hours=4))
        cls.later=Event.objects.create(name='SYNTHETIC Later',status='upcoming',start_at=now+timedelta(days=3),end_at=now+timedelta(days=3,hours=4))
        cls.active=Event.objects.create(name='SYNTHETIC Active',status='upcoming',start_at=now-timedelta(hours=1),end_at=now+timedelta(hours=3))
    def grant(self,*names):
        for name in names:
            app,code=name.split('.')
            self.staff.user_permissions.add(Permission.objects.get(content_type__app_label=app,codename=code))
        for key in ('_perm_cache','_user_perm_cache','_group_perm_cache'):self.staff.__dict__.pop(key,None)
    def login(self,user=None):self.client.force_login(user or self.staff,backend='django.contrib.auth.backends.ModelBackend')
    def test_events_sidebar_opens_listing_directly(self):
        self.grant('events.view_event')
        item=next(x for x in visible_workspaces(self.staff) if x['id']=='events')
        self.assertEqual(item['url'],reverse('events:event_list'))
    def test_staff_without_event_permission_keeps_safe_schedule(self):
        item=next(x for x in visible_workspaces(self.staff) if x['id']=='events')
        self.assertEqual(item['url'],reverse('staffing:schedule'))
        self.login();self.assertEqual(self.client.get(reverse('events:event_list')).status_code,403)
    def test_legacy_workspace_deep_link_remains(self):
        self.login();self.assertEqual(self.client.get(reverse('internal:workspace',args=['events'])).status_code,200)
    def test_featured_is_chronological_future_not_current_or_assigned_priority(self):
        self.grant('events.view_event');StaffAssignment.objects.create(event=self.later,staff=self.profile)
        result=operations_home(self.staff)
        self.assertEqual(result['featured']['event'],self.next)
        self.assertNotEqual(result['featured']['event'],self.active)
    def test_featured_excludes_terminal_draft_and_future_completed(self):
        self.grant('events.view_event')
        self.next.status='cancelled';self.next.save()
        self.assertEqual(operations_home(self.staff)['featured']['event'],self.later)
    def test_no_event_leak_for_unassigned_staff(self):
        self.login();response=self.client.get(reverse('internal:workspace',args=['operations']))
        self.assertContains(response,'No authorized upcoming events')
        self.assertNotContains(response,self.next.name)
        self.assertNotContains(response,self.active.name)
    def test_assigned_staff_only_sees_own_confirmed_future_events(self):
        StaffAssignment.objects.create(event=self.later,staff=self.profile)
        result=operations_home(self.staff)
        self.assertEqual(result['featured']['event'],self.later)
        self.assertEqual(result['featured']['url'],reverse('staffing:staff-event',args=[self.later.pk]))
        self.assertEqual(result['other_events'],[])
    def test_removed_assignment_is_not_access(self):
        StaffAssignment.objects.create(event=self.next,staff=self.profile,status='removed')
        self.assertIsNone(operations_home(self.staff)['featured'])
    def test_no_upcoming_event_empty_state(self):
        self.grant('events.view_event');Event.objects.filter(pk__in=[self.next.pk,self.later.pk]).update(status='completed')
        self.assertIsNone(operations_home(self.staff)['featured'])
    def test_restricted_tabs_and_tools_are_absent(self):
        self.grant('events.view_event')
        self.assertEqual([x['id'] for x in event_tabs(self.staff,self.next)],['overview','operations'])
        self.assertEqual(event_tools(self.staff,self.next),[])
        self.login()
        for section in ['results','registrations']:
            response=self.client.get(reverse('events:event_detail',args=[self.next.pk])+'?section='+section)
            self.assertNotContains(response,'aria-current="page">'+section.title())
    def test_operations_is_functional_without_health_monitor_grant(self):
        self.grant('events.view_event','events.change_event');self.login()
        response=self.client.get(reverse('events:event_detail',args=[self.next.pk])+'?section=operations')
        self.assertContains(response,'Public registration settings')
        self.assertNotContains(response,'Station assignments')
        self.assertContains(response,'Health Monitor')
        self.assertNotContains(response,'Ready with Exceptions')
        self.assertEqual(response.context['section'],'operations')
    def test_event_visibility_is_required_for_operations_section(self):
        self.login();self.assertEqual(self.client.get(reverse('events:event_detail',args=[self.next.pk])+'?section=operations').status_code,403)
    def test_assigned_staff_do_not_gain_health_or_management_tools(self):
        StaffAssignment.objects.create(event=self.next,staff=self.profile)
        self.assertEqual(event_tools(self.staff,self.next),[])
        self.assertIn('operations',[x['id'] for x in event_tabs(self.staff,self.next)])
        self.assertEqual(next(x['url'] for x in event_tabs(self.staff,self.next) if x['id']=='operations'),reverse('operations:execution',args=[self.next.pk]))
    def test_complete_manager_tabs_reuse_existing_routes(self):
        tabs={x['id']:x for x in event_tabs(self.admin,self.next)}
        self.assertEqual(tabs['staff']['url'],reverse('staffing:event',args=[self.next.pk]))
        self.assertEqual(tabs['tasks']['url'],reverse('operations:event',args=[self.next.pk,'tasks']))
        self.assertEqual(tabs['expenses']['url'],reverse('event_reports:detail',args=[self.next.pk])+'?section=expenses')
        self.assertEqual(tabs['clock']['url'],reverse('workdays:event',args=[self.next.pk]))
        self.assertEqual(len(tabs),11)
    def test_event_name_and_lifecycle_confirmation_are_preserved(self):
        self.login(self.admin);response=self.client.get(reverse('events:event_detail',args=[self.next.pk]))
        self.assertContains(response,'class="record-title">'+self.next.name)
        self.assertContains(response,'Event actions')
        self.assertContains(response,reverse('events:event_action',args=[self.next.pk,'cancel']))
        self.next.refresh_from_db();self.assertEqual(self.next.status,'upcoming')
    def test_existing_event_sections_render_for_admin(self):
        self.login(self.admin)
        for section in ['overview','operations','results','registrations']:
            self.assertEqual(self.client.get(reverse('events:event_detail',args=[self.next.pk])+'?section='+section).status_code,200)
        for section in ['tasks','notes','documents']:
            self.assertEqual(self.client.get(reverse('operations:event',args=[self.next.pk,section])).status_code,200)
    def test_notes_and_documents_remain_for_assigned_staff_without_manager_access(self):
        StaffAssignment.objects.create(event=self.next,staff=self.profile)
        self.login()
        self.assertEqual(self.client.get(reverse('operations:event',args=[self.next.pk,'notes'])).status_code,200)
        self.assertEqual(self.client.get(reverse('staffing:event',args=[self.next.pk])).status_code,403)
    def test_dashboard_link_matches_actual_permission(self):
        self.grant('events.view_operationalincident')
        links=visible_workspace_links(self.staff,'operations')
        self.assertNotIn('events:operations-dashboard',[x['url_name'] for x in links])
        self.grant('events.view_syncoutboxitem')
        self.assertNotIn('events:operations-dashboard',[x['url_name'] for x in visible_workspace_links(self.staff,'operations')])
        self.assertIn('events:operations-dashboard',[x['url_name'] for x in visible_workspace_links(self.staff,'platform-tools')])
    def test_workday_navigation_requires_complete_manager_predicate(self):
        self.grant('workdays.manage_workdays')
        self.assertNotIn('workdays:index',[x['url_name'] for x in visible_workspace_links(self.staff,'administration')])
        self.grant('events.view_event')
        self.assertIn('workdays:index',[x['url_name'] for x in visible_workspace_links(self.staff,'administration')])
    def test_personal_history_discoverable_and_no_manager_grant(self):
        self.login();response=self.client.get(reverse('internal:settings'))
        self.assertContains(response,reverse('workdays:mine'))
        self.assertEqual(self.client.get(reverse('workdays:index')).status_code,403)
    def test_add_task_labels_and_no_legacy_action(self):
        self.login(self.admin)
        for url in [reverse('internal:home'),reverse('operations:quick-create'),reverse('events:event_detail',args=[self.next.pk])]:
            response=self.client.get(url)
            self.assertContains(response,'Add Task')
            self.assertNotContains(response,'Create Task');self.assertNotContains(response,'Create task')
    def test_search_filters_and_deep_links_preserved(self):
        self.login(self.admin)
        response=self.client.get(reverse('events:event_list'),{'view':'upcoming','q':'Next Mixed','event_type':'','date_from':'','date_to':''})
        self.assertContains(response,self.next.name);self.assertNotContains(response,self.later.name)
        self.assertContains(response,'Filters');self.assertContains(response,'Clear filters')
        self.assertContains(response,'name="q"');self.assertContains(response,'name="view"')
    def test_read_only_event_user_cannot_edit_or_change_lifecycle(self):
        self.grant('events.view_event');self.login()
        self.assertEqual(self.client.post(reverse('events:event_action',args=[self.next.pk,'cancel']),{'confirm':'on','reason':'synthetic'}).status_code,403)
        self.next.refresh_from_db();self.assertEqual(self.next.status,'upcoming')
    def test_operations_home_does_not_write_evidence_tasks_or_notes(self):
        self.grant('events.view_event');self.login()
        before=(Task.objects.count(),EventNote.objects.count())
        self.client.get(reverse('internal:workspace',args=['operations']))
        self.assertEqual(before,(Task.objects.count(),EventNote.objects.count()))

    def test_event_has_one_floating_contextual_task_action(self):
        self.login(self.admin)
        response=self.client.get(reverse('events:event_detail',args=[self.next.pk]))
        self.assertContains(response,'data-global-task-create',count=1)
        self.assertContains(response,'button-primary global-task-create',count=1)
        self.assertContains(response,reverse('operations:quick-create-event',args=[self.next.pk]))
    def test_workday_admin_navigation_has_only_one_active_workspace(self):
        active=[x['id'] for x in visible_workspaces(self.admin,reverse('workdays:index')) if x['active']]
        self.assertEqual(active,['administration'])
    def test_featured_simulator_link_reuses_existing_results(self):
        self.login(self.admin)
        response=self.client.get(reverse('internal:workspace',args=['operations']))
        self.assertContains(response,'Simulator Scoring')
        self.assertContains(response,reverse('events:event_detail',args=[self.next.pk])+'?section=results')

    def test_legacy_event_workflows_are_discoverable_from_events(self):
        self.login(self.admin)
        response=self.client.get(reverse('events:event_list'))
        for route in ['staffing:schedule','staffing:queue','operations:events','events:ticket_recovery']:
            self.assertContains(response,reverse(route))
        tools=event_tools(self.admin,self.next)
        self.assertIn(reverse('events:public-settings',args=[self.next.pk]),[t['url'] for t in tools])

    def test_event_report_and_workdays_keep_event_workspace_active(self):
        for route in ['event_reports:detail','workdays:event']:
            path=reverse(route,args=[self.next.pk])
            self.assertEqual([x['id'] for x in visible_workspaces(self.admin,path) if x['active']],['events'])
