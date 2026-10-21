"""Owner refinements retain reporting access and the authoritative task workflow."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from apps.events.models import Event
from apps.event_reports.services import REPORT_PERMS
from apps.operations.models import Task, TaskNotification
from apps.operations.templatetags.task_ui import global_task_create_url
from apps.participants.models import Participant
from apps.staffing.models import StaffProfile
from .reporting import report_destinations
from .navigation import visible_workspaces, visible_workspace_links


class FinalRefinementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.staff = U.objects.create_user('synthetic-refinement-staff', is_staff=True)
        cls.admin = U.objects.create_superuser('synthetic-refinement-admin', email='review@example.test', password=None)
        cls.person = Participant.objects.create(first_name='SYNTHETIC', last_name='Refinement')
        cls.profile = StaffProfile.objects.create(first_name='SYNTHETIC', last_name='Reviewer', user=cls.staff)
        cls.event = Event.objects.create(name='SYNTHETIC refinement event', status='upcoming',
            start_at=timezone.now()+timedelta(days=2), end_at=timezone.now()+timedelta(days=3))

    def setUp(self):
        self.client.force_login(self.staff, backend='django.contrib.auth.backends.ModelBackend')

    def grant(self, *names):
        for name in names:
            app, code = name.split('.')
            self.staff.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
        for name in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
            self.staff.__dict__.pop(name, None)

    def context(self, user=None, **extra):
        request = RequestFactory().get('/internal/')
        request.user = user if user is not None else self.staff
        return {'request': request, **extra}

    def test_single_official_logo_is_in_header_and_sidebar_has_none(self):
        html = self.client.get(reverse('internal:home')).content.decode()
        self.assertEqual(html.count('class="brand-logo"'), 1)
        self.assertIn('built-to-work-linear.svg', html.split('<header class="topbar">')[1])
        self.assertNotIn('brand-logo', html.split('<aside')[1].split('</aside>')[0])
        self.assertEqual(html.count('aria-label="Built to Work home"'), 1)

    def test_home_labels_and_central_analytics_destination(self):
        response = self.client.get(reverse('internal:home'))
        self.assertContains(response, '>Notifications</h2>')
        self.assertContains(response, '>Analytics</h2>')
        self.assertContains(response, 'View all notifications')
        self.assertContains(response, 'href="'+reverse('internal:reports')+'">View more</a>')
        self.assertNotContains(response, 'Recent Updates')
        self.assertNotContains(response, 'At a Glance')
        self.assertEqual(len(response.context['home_metrics']), 2)

    def test_notifications_workspace_retains_live_attention_and_existing_urls(self):
        Task.objects.create(title='SYNTHETIC due task', created_by=self.staff, assigned_staff=self.profile,
            priority='normal', status='todo', due_date=timezone.localdate()-timedelta(days=1))
        response = self.client.get(reverse('operations:updates'))
        self.assertEqual(reverse('operations:updates'), '/operations/tasks/updates/')
        self.assertContains(response, '<h1>Notifications</h1>', html=True)
        self.assertContains(response, 'Live task attention')
        self.assertContains(response, '1 overdue')
        self.assertEqual(TaskNotification.objects.count(), 0)

    def test_central_landing_is_honest_without_report_permissions(self):
        response = self.client.get(reverse('internal:reports'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Reports &amp; Analytics')
        self.assertContains(response, 'No reports available for your account')
        self.assertEqual(response.context['report_destinations'], [])
        self.assertNotContains(response, 'href="'+reverse('event_reports:index')+'"')
        self.assertNotContains(response, 'href="'+reverse('workdays:reports')+'"')
        self.assertEqual(self.client.get(reverse('event_reports:index')).status_code, 403)
        self.assertEqual(self.client.get(reverse('workdays:reports')).status_code, 403)

    def test_partial_event_permission_does_not_expose_event_report(self):
        self.grant('event_reports.view_event_reports')
        self.assertEqual(report_destinations(self.staff), [])
        self.assertFalse(any(x['url_name']=='internal:reports' for x in visible_workspace_links(self.staff, 'operations')))

    def test_partial_workday_permission_does_not_expose_report(self):
        self.grant('workdays.manage_workdays')
        self.assertEqual(report_destinations(self.staff), [])

    def test_workday_only_access_and_revocation(self):
        self.grant('workdays.manage_workdays', 'events.view_event')
        self.assertEqual([r['url'] for r in report_destinations(self.staff)], [reverse('workdays:reports')])
        self.assertEqual(self.client.get(reverse('workdays:reports')).status_code, 200)
        self.staff.user_permissions.clear()
        for name in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
            self.staff.__dict__.pop(name, None)
        self.assertEqual(report_destinations(self.staff), [])

    def test_full_existing_report_policy_exposes_existing_destinations(self):
        self.grant(*REPORT_PERMS)
        self.assertEqual({r['url'] for r in report_destinations(self.staff)},
            {reverse('event_reports:index'), reverse('workdays:reports')})
        self.assertEqual(reverse('event_reports:index'), '/reports/')
        self.assertEqual(self.client.get(reverse('event_reports:index')).status_code, 200)
        self.assertEqual([i['id'] for i in visible_workspaces(self.staff, reverse('internal:reports')) if i['active']], ['operations'])

    def test_staff_shell_pages_share_one_primary_floating_action(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        routes = ['internal:home', 'internal:reports', 'internal:settings', 'events:event_list',
            'participants:staff-directory', 'operations:tasks', 'operations:updates', 'event_reports:index', 'workdays:reports']
        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-global-task-create', count=1)
                self.assertContains(response, '>+ Create Task</a>', count=1)
        for workspace in ('operations', 'customers', 'administration'):
            self.assertContains(self.client.get(reverse('internal:workspace', args=[workspace])), 'data-global-task-create', count=1)
        self.assertNotContains(self.client.get(reverse('operations:tasks')), '+ Add Task')

    def test_excluded_users_cannot_render_global_action_even_with_override(self):
        users = [AnonymousUser(), get_user_model()(is_active=True, is_staff=False), get_user_model()(is_active=False, is_staff=True)]
        for user in users:
            self.assertEqual(global_task_create_url(self.context(user, task_creation_enabled=True)), '')
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('admin:login')), 'data-global-task-create')

    def test_page_flag_suppresses_action_and_bottom_clearance_class(self):
        html = Template('{% extends "internal/base.html" %}').render(Context(self.context(task_creation_enabled=False)))
        self.assertNotIn('data-global-task-create', html)
        self.assertNotIn('has-task-create', html)
        self.assertEqual(global_task_create_url(self.context()), reverse('operations:quick-create'))

    def test_full_page_creation_suppresses_nested_creation_action(self):
        response = self.client.get(reverse('operations:quick-create'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-global-task-create')

    def test_authorized_context_is_preserved_and_restricted_context_is_not_leaked(self):
        self.assertEqual(global_task_create_url(self.context(event=self.event)), reverse('operations:quick-create'))
        self.assertEqual(global_task_create_url(self.context(participant=self.person)), reverse('operations:quick-create'))
        self.grant('events.view_event', 'participants.view_participant')
        self.assertEqual(global_task_create_url(self.context(event=self.event)), reverse('operations:quick-create-event', args=[self.event.pk]))
        self.assertEqual(global_task_create_url(self.context(participant=self.person)), reverse('operations:quick-create-participant', args=[self.person.pk]))
        self.person.archived_at = timezone.now(); self.person.save(update_fields=['archived_at'])
        self.assertEqual(global_task_create_url(self.context(participant=self.person)), reverse('operations:quick-create'))

    def test_task_workspace_return_query_is_retained(self):
        url = reverse('operations:quick-create')+'?return_to=%2Foperations%2Ftasks%2F%3Fview%3Dmine'
        self.assertEqual(global_task_create_url(self.context(quick_create_url=url)), url)

    def test_modal_creation_from_multiple_origin_pages_uses_same_service(self):
        for origin in ('internal:home', 'events:event_list', 'participants:staff-directory', 'internal:reports'):
            with self.subTest(origin=origin):
                url = reverse('operations:quick-create')
                response = self.client.get(url, HTTP_X_TASK_MODAL='1')
                token = response.context['form'].initial['creation_token']
                result = self.client.post(url, {'title':'SYNTHETIC '+origin, 'description':'', 'assigned_staff':self.profile.pk,
                    'priority':'normal', 'due_date':'', 'creation_token':token}, HTTP_X_TASK_MODAL='1', HTTP_REFERER='http://testserver'+reverse(origin))
                self.assertEqual(result.status_code, 201)
                self.assertEqual(Task.objects.get(title='SYNTHETIC '+origin).created_by, self.staff)
                self.assertNotIn('Location', result)
        self.assertEqual(TaskNotification.objects.count(), 0)

    def test_contextual_modal_creation_preserves_participant_and_event(self):
        self.grant('events.view_event', 'participants.view_participant')
        for route, pk in [('operations:quick-create-event', self.event.pk), ('operations:quick-create-participant', self.person.pk)]:
            url = reverse(route, args=[pk])
            response = self.client.get(url, HTTP_X_TASK_MODAL='1')
            form = response.context['form']
            result = self.client.post(url, {'title':'SYNTHETIC '+route, 'description':'', 'assigned_staff':self.profile.pk,
                'priority':'normal', 'due_date':'', 'creation_token':form.initial['creation_token']}, HTTP_X_TASK_MODAL='1')
            self.assertEqual(result.status_code, 201)
            task = Task.objects.get(title='SYNTHETIC '+route)
            if route.endswith('event'):
                self.assertEqual(task.event_id, self.event.pk)
            else:
                self.assertTrue(task.participant_links.filter(participant=self.person).exists())

    def test_invalid_modal_form_creates_nothing(self):
        response = self.client.post(reverse('operations:quick-create'), {'title':''}, HTTP_X_TASK_MODAL='1')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Task.objects.count(), 0)
        self.assertContains(response, 'data-task-errors', status_code=400)
