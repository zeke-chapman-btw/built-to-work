"""Behavioral owner-cleanup checks. All writes use isolated synthetic fixtures."""
from datetime import date, datetime, timedelta, UTC
from unittest.mock import patch
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, connections, DatabaseError
from django.test import TestCase, SimpleTestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.events.forms import EventForm
from apps.events.section_editors import SectionForm, save_section
from apps.operations.models import Task, TaskCustomerLink, TaskParticipantLink
from apps.operations.forms import TaskForm
from apps.operations.services import save_task
from apps.operations.task_links import display_links
from apps.operations.task_access import tasks_for
from apps.operations.date_shortcuts import date_shortcuts
from apps.participants.models import Participant
from apps.customers.models import CustomerOrganization
from apps.staffing.models import StaffProfile


class DateShortcutTests(SimpleTestCase):
    def test_weekend_and_year_boundary_resolved_dates(self):
        cases=[
            (date(2026,12,31),date(2027,1,1),date(2027,1,4),date(2027,1,1)),
            (date(2026,11,6),date(2026,11,7),date(2026,11,9),date(2026,11,9)),
            (date(2026,11,7),date(2026,11,8),date(2026,11,9),date(2026,11,9)),
            (date(2026,11,8),date(2026,11,9),date(2026,11,9),date(2026,11,9)),
            (date(2028,2,28),date(2028,2,29),date(2028,3,6),date(2028,2,29)),
        ]
        for today,tomorrow,monday,business in cases:
            with self.subTest(today=today):
                self.assertEqual(dict(date_shortcuts(today)),{'Tomorrow':tomorrow,'Next Week':monday,'Next Business Day':business})

    def test_active_timezone_and_dst_use_calendar_dates(self):
        with timezone.override('America/Los_Angeles'),patch('django.utils.timezone.now',return_value=datetime(2027,1,1,1,tzinfo=UTC)):
            self.assertEqual(dict(date_shortcuts())['Tomorrow'],date(2027,1,1))
        for today,expected in [(date(2026,3,6),date(2026,3,9)),(date(2026,10,30),date(2026,11,2))]:
            self.assertEqual(dict(date_shortcuts(today))['Next Business Day'],expected)


class FixtureMixin:
    def make_fixtures(self):
        U=get_user_model()
        self.admin=U.objects.create_superuser('synthetic-owner2-admin','owner2@example.test','test-password')
        self.worker=U.objects.create_user('synthetic-owner2-worker',password='test-password',is_staff=True)
        self.other=U.objects.create_user('synthetic-owner2-other',password='test-password',is_staff=True)
        self.staff=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Owner2 Admin',user=self.admin)
        self.worker_staff=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Owner2 Worker',user=self.worker)
        self.other_staff=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Owner2 Other',user=self.other)
        self.event=Event.objects.create(name='SYNTHETIC Owner2 Event',start_at=datetime(2026,11,9,13,tzinfo=UTC),
            end_at=datetime(2026,11,11,21,tzinfo=UTC),timezone_name='America/New_York',status='upcoming',
            location_name='SYNTHETIC Venue',city='Athens',state='GA',contact_name='SYNTHETIC Original',
            housing_information='SYNTHETIC unchanged housing',logistics_instructions='SYNTHETIC unchanged logistics')
        self.person=Participant.objects.create(first_name='SYNTHETIC',last_name='Owner2 Person')
        self.company=CustomerOrganization.objects.create(name='SYNTHETIC Owner2 Customer')
        self.second_company=CustomerOrganization.objects.create(name='SYNTHETIC Owner2 Replacement')
        self.task=Task.objects.create(title='SYNTHETIC Owner2 Task',assigned_staff=self.worker_staff,created_by=self.admin)

    def task_data(self,task=None,actor=None,**changes):
        form=TaskForm(instance=task or self.task,actor=actor or self.admin)
        data={name:form.initial.get(name,field.initial) for name,field in form.fields.items()}
        data={k:('' if v is None else [str(x) for x in v] if isinstance(v,(tuple,list)) else v) for k,v in data.items()}
        data.update(changes)
        return data


class TaskEditorTests(FixtureMixin,TestCase):
    def setUp(self):
        self.make_fixtures()

    def test_self_assignment_and_creator_access_after_other_assignment(self):
        form=TaskForm(actor=self.worker)
        self.assertEqual(form.self_assignee.pk,self.worker_staff.pk)
        task,checked=save_task(actor=self.admin,task_id=self.task.pk,data=self.task_data(assigned_staff=str(self.staff.pk)))
        self.assertIsNotNone(task,checked.errors)
        self.assertEqual(task.assigned_staff_id,self.staff.pk)
        task,checked=save_task(actor=self.admin,task_id=task.pk,data=self.task_data(task,assigned_staff=str(self.other_staff.pk)))
        self.assertIsNotNone(task,checked.errors)
        self.assertTrue(tasks_for(self.admin).filter(pk=task.pk).exists())
        self.assertTrue(tasks_for(self.other).filter(pk=task.pk).exists())
        self.assertFalse(tasks_for(self.worker).filter(pk=task.pk).exists())
        own,checked=save_task(actor=self.worker,data=self.task_data(Task(),actor=self.worker,
            title='SYNTHETIC self-created',assigned_staff=str(self.worker_staff.pk),revision='new'))
        self.assertIsNotNone(own,checked.errors);self.assertEqual(own.created_by_id,self.worker.pk)
        own,checked=save_task(actor=self.worker,task_id=own.pk,data=self.task_data(own,actor=self.worker,assigned_staff=str(self.other_staff.pk)))
        self.assertIsNotNone(own,checked.errors)
        self.assertTrue(tasks_for(self.worker).filter(pk=own.pk).exists())
        self.assertTrue(tasks_for(self.other).filter(pk=own.pk).exists())

    def test_missing_or_inactive_profile_does_not_manufacture_identity(self):
        orphan=get_user_model().objects.create_user('synthetic-owner2-unlinked',is_staff=True)
        before=StaffProfile.objects.count()
        self.assertIsNone(TaskForm(actor=orphan).self_assignee)
        self.worker_staff.is_active=False;self.worker_staff.save(update_fields=['is_active'])
        self.assertIsNone(TaskForm(actor=self.worker).self_assignee)
        self.assertEqual(StaffProfile.objects.count(),before)

    def test_first_profile_link_preserves_the_existing_account_and_authority(self):
        from apps.staffing import services as staffing
        account=get_user_model().objects.create_user('synthetic-owner2-first-profile',is_active=True,is_staff=True)
        before=(get_user_model().objects.count(),account.is_staff,account.is_superuser,account.security_revision)
        profile,form=staffing.save_profile(actor=self.admin,data={'first_name':'SYNTHETIC','last_name':'First Profile','is_active':'on'})
        self.assertIsNotNone(profile,form.errors)
        profile,form=staffing.link_account(actor=self.admin,profile_id=profile.pk,
            data={'revision':profile.updated_at.isoformat(),'user':str(account.pk),'confirm':'on'})
        self.assertIsNotNone(profile,form.errors)
        account.refresh_from_db()
        self.assertEqual((get_user_model().objects.count(),account.is_staff,account.is_superuser,account.security_revision),before)
        self.assertEqual(StaffProfile.objects.filter(user=account).count(),1)
        self.assertEqual(TaskForm(actor=account).self_assignee.pk,profile.pk)

    def test_link_create_replace_remove_and_unrelated_task_fields(self):
        original=(self.task.created_by_id,self.task.status,self.task.assigned_staff_id)
        data=self.task_data(customers=[str(self.company.pk)],participants=[str(self.person.pk)],events=[str(self.event.pk)])
        task,form=save_task(actor=self.admin,task_id=self.task.pk,data=data)
        self.assertIsNotNone(task,form.errors)
        self.assertTrue(task.customer_links.filter(organization=self.company).exists())
        self.assertTrue(task.participant_links.filter(participant=self.person).exists())
        from apps.operations.task_history import entries_for, present_history
        visible=present_history(self.admin,entries_for(self.admin,task))
        restricted=present_history(self.worker,entries_for(self.worker,task))
        self.assertIn(('Linked customers','Not set',self.company.name),[c for row in visible for c in row['changes']])
        self.assertNotIn(self.company.name,str(restricted))
        self.assertIn(('Linked customers','Not set','Restricted customer'),[c for row in restricted for c in row['changes']])
        task,form=save_task(actor=self.admin,task_id=task.pk,data=self.task_data(task,customers=[str(self.second_company.pk)]))
        self.assertIsNotNone(task,form.errors)
        self.assertEqual(list(task.customer_links.values_list('organization_id',flat=True)),[self.second_company.pk])
        task,form=save_task(actor=self.admin,task_id=task.pk,data=self.task_data(task,customers=[],participants=[],events=[]))
        self.assertIsNotNone(task,form.errors)
        self.assertFalse(task.customer_links.exists());self.assertFalse(task.participant_links.exists());self.assertFalse(task.event_links.exists())
        self.assertEqual((task.created_by_id,task.status,task.assigned_staff_id),original)
        self.assertTrue(AuditLog.objects.filter(action='operations.task.updated',new_data__linked_customer_ids=[]).exists())

    def test_unauthorized_links_rejected_and_do_not_expand_visibility(self):
        task,form=save_task(actor=self.worker,task_id=self.task.pk,data=self.task_data(actor=self.worker,
            title='SYNTHETIC forbidden mutation',customers=[str(self.company.pk)]))
        self.assertIsNone(task);self.assertIn('customers',form.errors)
        self.task.refresh_from_db();self.assertEqual(self.task.title,'SYNTHETIC Owner2 Task')
        TaskCustomerLink.objects.create(task=self.task,organization=self.company)
        self.assertFalse(tasks_for(self.other).filter(pk=self.task.pk).exists())
        links=display_links(self.worker,self.task)
        self.assertEqual(links['customers'][0]['label'],'Restricted customer');self.assertIsNone(links['customers'][0]['url'])

    def test_hidden_existing_links_are_retained_on_unrelated_save(self):
        TaskCustomerLink.objects.create(task=self.task,organization=self.company)
        TaskParticipantLink.objects.create(task=self.task,participant=self.person)
        task,form=save_task(actor=self.worker,task_id=self.task.pk,data=self.task_data(actor=self.worker,title='SYNTHETIC own task edit'))
        self.assertIsNotNone(task,form.errors)
        self.assertTrue(task.customer_links.exists());self.assertTrue(task.participant_links.exists())

    def test_more_than_fifty_existing_links_round_trip_without_truncation(self):
        people=Participant.objects.bulk_create([Participant(first_name='SYNTHETIC',last_name=f'Linked {n}') for n in range(55)])
        TaskParticipantLink.objects.bulk_create([TaskParticipantLink(task=self.task,participant=p) for p in people])
        task,form=save_task(actor=self.admin,task_id=self.task.pk,data=self.task_data(title='SYNTHETIC preserved links'))
        self.assertIsNotNone(task,form.errors);self.assertEqual(task.participant_links.count(),55)

    def test_lookup_is_bounded_disambiguates_and_suppresses_customer_enumeration(self):
        CustomerOrganization.objects.bulk_create([CustomerOrganization(name='SYNTHETIC Same Company') for _ in range(25)])
        self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")
        response=self.client.get(reverse('operations:task-record-lookup'),{'kind':'customers','q':'Same Company'})
        rows=response.json()['results'];self.assertEqual(len(rows),20);self.assertEqual(len({r['label'] for r in rows}),20)
        self.client.force_login(self.worker,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse('operations:task-record-lookup'),{'kind':'customers','q':'SYNTHETIC'}).json()['results'],[])

    def test_invalid_manual_date_and_stale_link_edit_preserve_data(self):
        task,form=save_task(actor=self.admin,task_id=self.task.pk,data=self.task_data(due_date='2026-02-30'))
        self.assertIsNone(task);self.assertIn('due_date',form.errors)
        stale=self.task_data(customers=[str(self.company.pk)])
        task,form=save_task(actor=self.admin,task_id=self.task.pk,data=self.task_data(due_date='2026-12-31'))
        self.assertIsNotNone(task,form.errors)
        with self.assertRaises(ValidationError):save_task(actor=self.admin,task_id=task.pk,data=stale)
        task.refresh_from_db();self.assertEqual(task.due_date,date(2026,12,31));self.assertFalse(task.customer_links.exists())

    def test_workspace_counts_filters_sort_and_return_state(self):
        self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")
        response=self.client.get(reverse('operations:tasks'),{'view':'all','q':'Owner2 Task','sort':'title'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['tasks'].paginator.count,1)
        self.assertContains(response,'<span class="btw-view-label">All tasks</span> <span class="tk-count">1</span>',html=True)
        self.assertNotContains(response,'All accessible tasks')
        self.assertIn('sort=title',response.context['query'])


class EventFocusedEditorTests(FixtureMixin,TestCase):
    def setUp(self):self.make_fixtures()

    def section_data(self,section,**changes):
        form=SectionForm(event=self.event,section=section)
        data={name:form.initial.get(name,'') for name in form.fields};data.update(changes);return data

    def test_focused_contact_save_retains_schedule_housing_and_registration(self):
        before={field:getattr(self.event,field) for field in EventForm.Meta.fields if not field.startswith('contact_')}
        event,form=save_section(actor=self.admin,event_id=self.event.pk,section='contact',data=self.section_data('contact',contact_name='SYNTHETIC Changed'))
        self.assertIsNotNone(event,form.errors);self.assertEqual(event.contact_name,'SYNTHETIC Changed')
        self.assertEqual({field:getattr(event,field) for field in before},before)

    def test_focused_housing_and_logistics_save_independently(self):
        event,form=save_section(actor=self.admin,event_id=self.event.pk,section='housing',data=self.section_data('housing',housing_information='SYNTHETIC new housing'))
        self.assertIsNotNone(event,form.errors);self.event.refresh_from_db()
        event,form=save_section(actor=self.admin,event_id=event.pk,section='logistics',data=self.section_data('logistics',logistics_instructions='SYNTHETIC new logistics'))
        self.assertIsNotNone(event,form.errors);self.assertEqual(event.housing_information,'SYNTHETIC new housing')

    def test_schedule_window_and_dst_ambiguity_rejected_without_data_loss(self):
        for start,end in [('2026-11-10T10:00','2026-11-09T10:00'),('2026-11-01T01:30','2026-11-02T10:00')]:
            event,form=save_section(actor=self.admin,event_id=self.event.pk,section='schedule',data=self.section_data('schedule',start_at=start,end_at=end))
            self.assertIsNone(event);self.assertTrue(form.errors)
        self.event.refresh_from_db();self.assertEqual(self.event.start_at,datetime(2026,11,9,13,tzinfo=UTC))

    def test_stale_and_cross_card_payloads_are_rejected(self):
        data=self.section_data('contact');Event.objects.filter(pk=self.event.pk).update(updated_at=timezone.now()+timedelta(seconds=1))
        event,form=save_section(actor=self.admin,event_id=self.event.pk,section='contact',data=data)
        self.assertIsNone(event);self.assertIn('Reload',str(form.errors))
        self.event.refresh_from_db()
        event,form=save_section(actor=self.admin,event_id=self.event.pk,section='contact',data=self.section_data('contact',housing_information='Forbidden'))
        self.assertIsNone(event);self.assertIn('Only fields',str(form.errors))

    def test_schedule_changes_cannot_drop_confirmed_assignment_days(self):
        from apps.staffing.models import StaffAssignment,AssignmentDay
        from apps.staffing.calendar import day_window
        assignment=StaffAssignment.objects.create(event=self.event,staff=self.worker_staff,status='confirmed')
        AssignmentDay.objects.create(assignment=assignment,**day_window(self.event,date(2026,11,9)))
        event,form=save_section(actor=self.admin,event_id=self.event.pk,section='schedule',data=self.section_data('schedule',start_at='2026-11-10T08:00'))
        self.assertIsNone(event);self.assertTrue(form.errors)
        self.event.refresh_from_db();self.assertEqual(self.event.start_at,datetime(2026,11,9,13,tzinfo=UTC))
        self.assertEqual(AssignmentDay.objects.filter(assignment=assignment,date=date(2026,11,9)).count(),1)

    def test_restricted_staff_cannot_edit_or_enumerate_focused_event(self):
        with self.assertRaises(PermissionDenied):save_section(actor=self.worker,event_id=self.event.pk,section='contact',data=self.section_data('contact'))
        self.client.force_login(self.worker,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse('events:section-edit',args=[self.event.pk,'contact'])).status_code,403)

    def test_directory_return_context_is_retained_and_external_returns_rejected(self):
        self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")
        origin=reverse('events:event_list')+'?view=all&location=Athens&sort=name'
        from urllib.parse import quote
        listing=self.client.get(origin)
        self.assertContains(listing,'?return_to='+quote(origin,safe='/'))
        detail=reverse('events:event_detail',args=[self.event.pk])
        response=self.client.get(detail,{'return_to':origin})
        self.assertContains(response,'data-event-list-back href="'+origin.replace('&','&amp;')+'"')
        response=self.client.get(detail,{'return_to':'//attacker.invalid/'})
        self.assertContains(response,'data-event-list-back href="'+reverse('events:event_list')+'"')
        self.assertNotContains(response,'href="//attacker.invalid/')

    def test_all_list_view_has_date_city_details_and_critical_lifecycle(self):
        self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")
        self.event.status='cancelled';self.event.save(update_fields=['status'])
        response=self.client.get(reverse('events:event_list'),{'view':'all','location':'Athens, GA','details':'progress'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.context['page_obj'].paginator.count,1)
        self.assertNotContains(response,'Checked In');self.assertNotContains(response,'SYNTHETIC Venue')
        self.assertContains(response,'Details in progress');self.assertContains(response,'Cancelled')
        self.assertNotContains(response,'Related tools')


class HomeAndNotificationsTests(FixtureMixin,TestCase):
    def setUp(self):self.make_fixtures();self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")

    def test_home_order_quick_actions_and_honest_metric_scopes(self):
        response=self.client.get('/internal/')
        self.assertEqual(response.status_code,200)
        html=response.content.decode()
        self.assertLess(html.index('id="dashboard-tasks-title"'),html.index('id="dashboard-actions-title"'))
        self.assertLess(html.index('id="dashboard-updates-title"'),html.index('id="dashboard-actions-title"'))
        for old in ['More shortcuts','About checks','About these counts','>Needs Attention<']:self.assertNotIn(old,html)
        for action in response.context['quick_actions']:self.assertContains(response,action['url'])
        metrics=response.context['home_summary_metrics'];self.assertEqual(len(metrics),4)
        self.assertEqual(next(m for m in metrics if m['label']=='Hires')['value'],0)
        self.assertIn('explicitly confirmed',next(m for m in metrics if m['label']=='Hires')['scope'])

    def test_source_failure_is_unavailable_not_zero(self):
        from apps.internal_ui.home_data import operational_metrics
        with patch('apps.customers.access.organizations',side_effect=DatabaseError('synthetic unavailable')):
            metric=next(m for m in operational_metrics(self.admin) if m['label']=='Open positions')
        self.assertIsNone(metric['value']);self.assertEqual(metric['state'],'unavailable')

    def test_bell_unread_read_and_current_visibility(self):
        from apps.operations.models import TaskNotification
        from apps.operations.notifications import visible_notifications,mark_read,mark_all_read,shell_context
        from django.contrib.contenttypes.models import ContentType
        source=AuditLog.objects.create(actor=self.other,action='synthetic.owner2.notice',content_type=ContentType.objects.get_for_model(Task),object_id=str(self.task.pk))
        notice=TaskNotification.objects.create(source_event=source,recipient=self.worker,task=self.task,kind='changed',message='SYNTHETIC notice')
        self.assertEqual(shell_context(self.worker)['task_updates']['unread'],1)
        self.assertEqual(mark_read(self.worker,notice.pk),1)
        self.assertEqual(shell_context(self.worker)['task_updates']['unread'],0)
        TaskNotification.objects.filter(pk=notice.pk).update(read_at=None)
        self.task.assigned_staff=self.other_staff;self.task.save(update_fields=['assigned_staff'])
        self.assertFalse(visible_notifications(self.worker).exists());self.assertEqual(mark_all_read(self.worker),0)
        notice.refresh_from_db();self.assertIsNone(notice.read_at)


class StaffClockIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.workdays.tests import Workday9ETests
        Workday9ETests.setUpTestData.__func__(cls)

    def test_staff_checkin_remains_reachable_and_does_not_approve_compensation(self):
        from apps.workdays.models import Workday, MileageTrip
        from apps.staffing.models import StaffAssignment
        with patch('django.utils.timezone.now',return_value=datetime(2030,7,1,14,tzinfo=UTC)):
            self.client.force_login(self.employee,backend='django.contrib.auth.backends.ModelBackend')
            url=reverse('workdays:checkin',args=[self.event.pk])
            self.assertEqual(self.client.get(url).status_code,200)
            response=self.client.post(url,{'work_date':'2030-07-01','role':str(self.role.pk),'drove':'on'})
            self.assertEqual(response.status_code,302)
            self.assertEqual(self.client.get(url).status_code,200)
        day=Workday.objects.get(event=self.event,staff=self.staff)
        self.assertEqual(day.source,'staff');self.assertEqual(day.approval,'pending');self.assertIsNone(day.approved_at)
        self.assertEqual(StaffAssignment.objects.get(event=self.event,staff=self.staff).status,'confirmed')
        trip=MileageTrip.objects.get(event=self.event,staff=self.staff)
        self.assertEqual(trip.status,'pending');self.assertIsNone(trip.approved_at)

    def test_manager_clock_keeps_roster_mileage_and_no_duplicate_route_selector(self):
        self.client.force_login(self.manager,backend='django.contrib.auth.backends.ModelBackend')
        before=AuditLog.objects.count()
        response=self.client.get(reverse('workdays:event',args=[self.event.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Checked in');self.assertContains(response,'Mileage trips')
        self.assertContains(response,reverse('workdays:trip-add',args=[self.event.pk]))
        self.assertNotContains(response,'aria-label="Staff sections"')
        self.assertEqual(AuditLog.objects.count(),before)


@skipUnless(connection.vendor=='postgresql','Task link replacement row-lock race requires PostgreSQL')
class TaskAssociationConcurrencyTests(FixtureMixin,TransactionTestCase):
    def setUp(self):self.make_fixtures()
    def test_one_stale_customer_replacement_loses_no_task_fields(self):
        payload=self.task_data();barrier=Barrier(2)
        def writer(company):
            try:
                barrier.wait(timeout=10)
                task,form=save_task(actor=self.admin,task_id=self.task.pk,data={**payload,'title':'SYNTHETIC '+company.name,'customers':[str(company.pk)]})
                return 'saved' if task else 'invalid'
            except ValidationError:return 'conflict'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(writer,[self.company,self.second_company]))
        self.assertCountEqual(results,['saved','conflict'])
        self.task.refresh_from_db();links=list(self.task.customer_links.select_related('organization'))
        self.assertEqual(len(links),1);self.assertEqual(self.task.title,'SYNTHETIC '+links[0].organization.name)
