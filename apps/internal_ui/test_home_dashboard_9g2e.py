"""Home must preview real, bounded, permission-scoped operational records."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from apps.events.models import Event
from apps.operations.models import Task, TaskEventLink, TaskNotification
from apps.operations import services
from apps.participants.models import Participant
from apps.staffing.models import StaffProfile, StaffAssignment, StaffingPlan


class HomeDashboard9G2ETests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.staff = U.objects.create_user('synthetic-E-staff', is_staff=True)
        cls.creator = U.objects.create_user('synthetic-E-creator', is_staff=True)
        cls.other = U.objects.create_user('synthetic-E-other', is_staff=True)
        cls.profiles = {u.pk: StaffProfile.objects.create(first_name='SYNTHETIC', last_name=u.username, user=u)
                        for u in (cls.staff, cls.creator, cls.other)}

    def setUp(self):
        self.client.force_login(self.staff, backend='django.contrib.auth.backends.ModelBackend')
        self.home = reverse('internal:home')

    def task(self, title='SYNTHETIC E task', **kwargs):
        values = dict(title=title, assigned_staff=self.profiles[self.staff.pk], created_by=self.creator,
                      due_date=timezone.localdate(), status='todo', priority='normal')
        values.update(kwargs)
        if values["status"] == "completed":
            values["completed_at"] = timezone.now()
            values["completed_by"] = self.creator
        return Task.objects.create(**values)

    def grant(self, user, app, codename):
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))

    def event(self, **kwargs):
        now = timezone.now()
        values = dict(name='SYNTHETIC E event', start_at=now+timedelta(days=2), end_at=now+timedelta(days=3), status='upcoming')
        values.update(kwargs)
        return Event.objects.create(**values)

    def test_four_sections_have_required_reading_order_and_real_counts(self):
        self.task()
        response = self.client.get(self.home)
        html = response.content.decode()
        headings = ['id="dashboard-tasks-title"', 'id="dashboard-updates-title"', 'id="dashboard-actions-title"', 'id="dashboard-analytics-title"']
        self.assertEqual(sorted(html.index(h) for h in headings), [html.index(h) for h in headings])
        self.assertEqual(html.count('<h1'), 1)
        self.assertEqual(response.context['home_task_count'], 1)
        self.assertNotContains(response, 'Not connected')
        self.assertNotContains(response, 'Layout preview')

    def test_only_current_users_open_unarchived_assignments_are_previewed(self):
        visible = self.task('SYNTHETIC E visible')
        for status in ('completed', 'cancelled'):
            self.task('SYNTHETIC E hidden '+status, status=status)
        self.task('SYNTHETIC E hidden archived', archived_at=timezone.now())
        self.task('SYNTHETIC E hidden other', assigned_staff=self.profiles[self.other.pk])
        response = self.client.get(self.home)
        self.assertEqual([t.pk for t in response.context['home_tasks']], [visible.pk])
        self.assertNotContains(response, 'SYNTHETIC E hidden')

    def test_admin_does_not_get_every_staff_assignment_in_my_tasks(self):
        self.grant(self.staff, 'operations', 'manage_tasks')
        self.task('SYNTHETIC E other assignment', assigned_staff=self.profiles[self.other.pk])
        response = self.client.get(self.home)
        self.assertEqual(response.context['home_tasks'], [])
        self.assertEqual(response.context['home_task_count'], 0)
        self.assertNotContains(response, 'SYNTHETIC E other assignment')

    def test_preview_limit_does_not_truncate_total_and_due_order_is_existing_workspace_order(self):
        for i in range(8):
            self.task('SYNTHETIC E bounded '+str(i), due_date=timezone.localdate()+timedelta(days=i-2))
        response = self.client.get(self.home)
        self.assertEqual(len(response.context['home_tasks']), 4)
        self.assertEqual(response.context['home_task_count'], 8)
        self.assertEqual([t.title for t in response.context['home_tasks']], ['SYNTHETIC E bounded '+str(i) for i in range(4)])
        self.assertContains(response, 'Overdue')
        self.assertContains(response, 'Due soon')

    def test_inaccessible_linked_event_title_is_redacted(self):
        event = self.event(name='SYNTHETIC E private event title')
        task = self.task()
        TaskEventLink.objects.create(task=task, event=event)
        response = self.client.get(self.home)
        self.assertNotContains(response, event.name)
        self.assertContains(response, 'Associated event (restricted)')
        self.grant(self.staff, 'events', 'view_event')
        self.assertContains(self.client.get(self.home), event.name)

    def test_ungranted_global_metrics_and_creation_actions_are_absent(self):
        self.event()
        Participant.objects.create(first_name='SYNTHETIC', last_name='E private participant')
        response = self.client.get(self.home)
        self.assertEqual(len(response.context['home_metrics']), 2)
        self.assertNotContains(response, 'Upcoming events')
        self.assertNotContains(response, 'Participant records')
        self.assertNotContains(response, reverse('events:event_create'))
        self.assertNotContains(response, reverse('admin:participants_participant_add'))
        self.assertContains(response, 'data-task-create')

    def test_global_totals_require_destination_permission_and_have_explicit_scope(self):
        self.grant(self.staff, 'events', 'view_event')
        self.grant(self.staff, 'participants', 'view_participant')
        self.event()
        self.event(status='draft')
        self.event(start_at=timezone.now()-timedelta(hours=1), end_at=timezone.now()+timedelta(hours=1))
        Participant.objects.create(first_name='SYNTHETIC', last_name='E metric')
        response = self.client.get(self.home)
        metrics = {m['label']: m for m in response.context['home_metrics']}
        self.assertEqual(metrics['Upcoming events']['value'], 1)
        self.assertEqual(metrics['Participant records']['value'], 1)
        self.assertTrue(all(m['scope'] for m in metrics.values()))
        self.staff.user_permissions.clear()
        self.assertEqual(len(self.client.get(self.home).context['home_metrics']), 2)

    def test_saved_quick_actions_remain_ordered_and_can_be_empty(self):
        self.staff.quick_action_ids = ['settings', 'create-task']
        self.staff.save(update_fields=['quick_action_ids'])
        self.assertEqual([a['id'] for a in self.client.get(self.home).context['quick_actions']], ['settings', 'create-task'])
        self.staff.quick_action_ids = []
        self.staff.save(update_fields=['quick_action_ids'])
        response = self.client.get(self.home)
        self.assertEqual(response.context['quick_actions'], [])
        self.assertContains(response, 'No Quick Actions selected.')

    def test_participant_add_action_is_existing_admin_form_and_permission_scoped(self):
        self.grant(self.staff, 'participants', 'add_participant')
        response = self.client.get(self.home)
        self.assertContains(response, reverse('admin:participants_participant_add'))
        self.assertEqual(self.client.get(reverse('admin:participants_participant_add')).status_code, 200)
        self.assertNotContains(response, 'Participant records')

    def assigned_notice(self):
        task, form = services.save_task(actor=self.creator, data=dict(
            title='SYNTHETIC E notice', assigned_staff=str(self.profiles[self.staff.pk].pk),
            description='', event='', due_date='', priority='normal', revision='new'))
        self.assertIsNotNone(task, form.errors)
        return task, TaskNotification.objects.get(task=task, recipient=self.staff)

    def test_existing_notification_can_be_read_from_home_without_new_system(self):
        task, notice = self.assigned_notice()
        response = self.client.get(self.home)
        self.assertEqual(response.context['task_updates']['unread'], 1)
        self.assertContains(response, 'is-unread')
        self.assertContains(response, reverse('operations:updates-read', args=[notice.pk]))
        self.assertRedirects(self.client.post(reverse('operations:updates-read', args=[notice.pk]), {'return_to':'home'}), self.home)
        notice.refresh_from_db()
        self.assertIsNotNone(notice.read_at)
        self.assertEqual(self.client.get(self.home).context['task_updates']['unread'], 0)
        self.assertEqual(TaskNotification.objects.count(), 1)

    def test_read_actions_remain_csrf_protected_and_other_staff_cannot_read_notices(self):
        task, notice = self.assigned_notice()
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.staff, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(csrf.post(reverse('operations:updates-read', args=[notice.pk]), {'return_to':'home'}).status_code, 403)
        self.client.force_login(self.other, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(self.home)
        self.assertNotContains(response, task.title)
        self.assertEqual(self.client.post(reverse('operations:updates-read', args=[notice.pk])).status_code, 404)

    def test_query_count_is_bounded_as_tasks_grow(self):
        self.task()
        self.client.get(self.home)
        with CaptureQueriesContext(connection) as small:
            self.client.get(self.home)
        Task.objects.bulk_create([Task(title='SYNTHETIC E bulk '+str(i), assigned_staff=self.profiles[self.staff.pk], created_by=self.creator) for i in range(80)])
        with CaptureQueriesContext(connection) as large:
            response = self.client.get(self.home)
        self.assertLessEqual(len(large), len(small)+1)
        self.assertLessEqual(len(large), 35)
        self.assertEqual(len(response.context['home_tasks']), 4)

    def test_event_staffing_summary_requires_management_permission(self):
        event = self.event()
        self.grant(self.staff, 'events', 'view_event')
        StaffingPlan.objects.create(event=event, required_staff=3)
        StaffAssignment.objects.create(event=event, staff=self.profiles[self.other.pk])
        url = reverse('events:event_list')
        self.assertNotContains(self.client.get(url), 'confirmed staff')
        self.grant(self.staff, 'staffing', 'manage_event_staffing')
        self.assertContains(self.client.get(url), '1 confirmed staff')
        self.assertContains(self.client.get(url), '3 required')

    def test_advanced_event_filters_expand_for_invalid_or_active_dates(self):
        self.grant(self.staff, 'events', 'view_event')
        response = self.client.get(reverse('events:event_list'), {'view':'all','date_from':'not-a-date'})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '<details class="event-filter-popover" open>', status_code=400)
        response = self.client.get(reverse('events:event_list'), {'view':'all','event_type':'rodeo'})
        self.assertContains(response, '<details class="event-filter-popover" open>')
        self.assertContains(response, 'UTC calendar dates')
