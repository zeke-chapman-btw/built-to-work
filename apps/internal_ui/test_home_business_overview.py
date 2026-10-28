"""Approved Home integrations: existing source parity, boundaries and no domain writes."""
import uuid
from datetime import UTC, datetime, time, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connection, DatabaseError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import (Event, EventRegistration, QrTicket, ParticipantMatchReview,
    OfflineEventPreparation, OfflineRegistrationEnvelope, TrailerInstance)
from apps.operations.models import Task, TaskNotification
from apps.operations.workspace import workspace
from apps.operations.execution_models import EventExecution
from apps.participants.models import Participant, RegistrationForm, RegistrationFormVersion, RegistrationSubmission
from apps.staffing.models import StaffProfile, StaffAssignment
from .home_data import home_data
from .dashboard import available_quick_actions, quick_actions_for
from .event_presentation import directory_counts
from .test_home_dashboard_phase1b import Markup


class HomeBusinessOverviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.admin = U.objects.create_superuser('SYNTHETIC-home-admin', 'synthetic-home@example.test', 'test-only')
        cls.staff = U.objects.create_user('SYNTHETIC-home-staff', is_staff=True)
        cls.other = U.objects.create_user('SYNTHETIC-home-other', is_staff=True)
        cls.profiles = {u.pk: StaffProfile.objects.create(user=u, first_name='SYNTHETIC', last_name=u.username)
                        for u in (cls.admin, cls.staff, cls.other)}
        form = RegistrationForm.objects.create(slug='synthetic-home', name='SYNTHETIC Home source')
        cls.version = RegistrationFormVersion.objects.create(form=form, version=1, snapshot={})

    def grant(self, user, *names):
        for name in names:
            app, code = name.split('.', 1)
            user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
        for key in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
            user.__dict__.pop(key, None)

    def page(self, user=None):
        self.client.force_login(user or self.admin, backend='django.contrib.auth.backends.ModelBackend')
        return self.client.get(reverse('internal:home'))

    def event(self, **extra):
        now = timezone.now()
        values = dict(name='SYNTHETIC Home event', status='upcoming', start_at=now+timedelta(days=2),
                      end_at=now+timedelta(days=3), timezone_name='America/New_York')
        values.update(extra)
        return Event.objects.create(**values)

    def task(self, **extra):
        values = dict(title='SYNTHETIC Home task', assigned_staff=self.profiles[self.staff.pk],
            created_by=self.other, status='todo', due_date=timezone.localdate()-timedelta(days=1))
        values.update(extra)
        if values['status']=='completed':
            values.update(completed_at=timezone.now(), completed_by=self.other)
        return Task.objects.create(**values)

    def review(self, status='open', held=False):
        person = Participant.objects.create(first_name='SYNTHETIC', last_name='Home intake')
        source = RegistrationSubmission.objects.create(form_version=self.version, participant=person,
            status='submitted', submitted_at=timezone.now(), intake_operation_id=uuid.uuid4(),
            answers={'first_name':'SYNTHETIC','last_name':'Home intake'})
        return ParticipantMatchReview.objects.create(source_submission=source, source_participant=person,
            status=status, evidence={'registration_status':'held'} if held else {}, created_at=timezone.now())

    def test_attention_reuses_task_scope_and_exact_overdue_criterion(self):
        self.task()
        self.task(created_by=self.staff, assigned_staff=self.profiles[self.other.pk])
        hidden = self.task(title='SYNTHETIC hidden unrelated', assigned_staff=self.profiles[self.other.pk])
        for values in ({'status':'completed'}, {'status':'cancelled'}, {'archived_at':timezone.now()},
                       {'due_date':timezone.localdate()}, {'due_date':None}):
            self.task(**values)
        response = self.page(self.staff)
        expected = workspace(self.staff, {}, mine=True)['summary']['overdue']
        self.assertEqual(expected, 2)
        self.assertEqual(response.context['home_attention_count'], expected)
        self.assertEqual(response.context['home_attention'][0]['count'], expected)
        self.assertNotContains(response, hidden.title)
        markup = Markup(); markup.feed(response.content.decode())
        task_area = markup.with_class('dashboard-tasks')[0]
        self.assertNotIn('Accessible overdue tasks', ''.join(task_area['text']))

    def test_intake_count_matches_default_queue_including_held_but_not_deferred(self):
        for state, held in [('open',False),('resolved',True),('rejected',True),
                            ('later',False),('later',True),('resolved',False),('rejected',False)]:
            self.review(state, held)
        event = self.event()
        trailer = TrailerInstance.objects.create(identity='SYNTHETIC-HOME', display_name='SYNTHETIC Home trailer')
        prep = OfflineEventPreparation.objects.create(event=event, trailer=trailer)
        envelope = OfflineRegistrationEnvelope.objects.create(preparation=prep, participant_identity=uuid.uuid4(),
            ticket_number='1234567890', consent_document_version=1, consent_hash='synthetic', payload={})
        ParticipantMatchReview.objects.create(envelope=envelope, status='open')
        response = self.page()
        queue = self.client.get(reverse('participants:intake-review-queue'))
        self.assertEqual(queue.context['reviews'].paginator.count, 3)
        self.assertEqual(response.context['home_intake_count'], queue.context['reviews'].paginator.count)
        self.assertEqual(next(a for a in response.context['home_attention'] if a['id']=='intake')['count'], 3)
        self.assertContains(response, '?status=open')
        self.assertNotContains(response, 'Everything is ready')

    def test_intake_permission_guard_prevents_even_reading_the_restricted_count(self):
        self.review()
        for permissions in [(), ('participants.view_participant',), ('events.view_participantmatchreview',)]:
            user = get_user_model().objects.create_user('SYNTHETIC-partial-'+str(len(get_user_model().objects.all())), is_staff=True)
            self.grant(user, *permissions)
            with patch('apps.internal_ui.home_data._intake_review_count', side_effect=AssertionError('restricted query')):
                response = self.page(user)
            self.assertEqual(response.context['home_intake_state'], 'restricted')
            self.assertIsNone(response.context['home_intake_count'])
            self.assertNotContains(response, reverse('participants:intake-review-queue'))
        self.grant(self.staff, 'participants.view_participant', 'events.view_participantmatchreview')
        self.assertEqual(self.page(self.staff).context['home_intake_count'], 1)

    def test_independent_source_failures_preserve_other_attention_and_never_claim_zero(self):
        self.review(); self.task(assigned_staff=self.profiles[self.admin.pk])
        with patch('apps.internal_ui.home_data._intake_review_count', side_effect=DatabaseError('synthetic')):
            response = self.page()
        self.assertEqual([a['id'] for a in response.context['home_attention']], ['tasks'])
        self.assertContains(response, 'Intake review check unavailable')
        self.assertNotContains(response, 'No flagged items')
        with patch('apps.internal_ui.home_data._work_data', side_effect=DatabaseError('synthetic')):
            response = self.page()
        self.assertEqual([a['id'] for a in response.context['home_attention']], ['intake'])
        self.assertContains(response, 'Overdue task check unavailable')
        self.assertIsNone(response.context['home_attention_count'])

    def test_zero_connected_sources_use_limited_truthful_empty_state(self):
        response = self.page()
        self.assertEqual(response.context['home_attention'], [])
        self.assertEqual(response.context['home_intake_count'], 0)
        self.assertContains(response, 'No flagged items from these Home checks.')
        self.assertNotContains(response, 'Everything is ready')

    def test_events_keep_existing_three_item_lifecycle_scope_and_local_dates(self):
        now = timezone.now()
        current = self.event(name='SYNTHETIC current', start_at=now-timedelta(hours=1), end_at=now+timedelta(hours=1))
        future = [self.event(name='SYNTHETIC future '+str(i), start_at=now+timedelta(days=i+1), end_at=now+timedelta(days=i+2)) for i in range(4)]
        self.event(name='SYNTHETIC ended', start_at=now-timedelta(days=2), end_at=now-timedelta(days=1))
        for state in ('draft','cancelled','archived','completed'):
            self.event(name='SYNTHETIC hidden '+state, status=state)
        response = self.page()
        self.assertEqual([e.pk for e in response.context['home_events']], [current.pk,future[0].pk,future[1].pk])
        self.assertContains(response, '>Current</span>')
        self.assertNotContains(response, 'SYNTHETIC ended')
        self.assertNotContains(response, 'SYNTHETIC hidden')

    def test_event_date_block_uses_event_timezone_across_utc_midnight(self):
        fixed = datetime(2026,10,27,16,tzinfo=UTC)
        event = self.event(name='SYNTHETIC midnight', start_at=datetime(2026,10,28,0,30,tzinfo=UTC),
            end_at=datetime(2026,10,28,1,30,tzinfo=UTC), timezone_name='America/New_York')
        with patch('django.utils.timezone.now', return_value=fixed):
            response = self.page()
        self.assertContains(response, 'Oct 27, 2026')
        self.assertContains(response, '8:30 PM')
        self.assertContains(response, '9:30 PM')
        markup=Markup();markup.feed(response.content.decode())
        self.assertIn('27', ''.join(markup.with_class('dashboard-event-date')[0]['text']))
        self.assertNotIn('28', ''.join(markup.with_class('dashboard-event-date')[0]['text']))

    def test_registration_counts_match_existing_evidence_not_tickets_or_void_rows(self):
        event = self.event()
        people = [Participant.objects.create(first_name='SYNTHETIC',last_name='Registered '+str(i)) for i in range(3)]
        active = EventRegistration.objects.create(event=event, participant=people[0])
        EventRegistration.objects.create(event=event, participant=people[0], status='void')
        EventRegistration.objects.create(event=event, participant=people[1])
        EventRegistration.objects.create(event=event, participant=people[2], status='void')
        test = Participant.objects.create(first_name='SYNTHETIC',last_name='System',kind='system_test')
        EventRegistration.objects.create(event=event, participant=test)
        for current in (False,False,True):
            QrTicket.objects.create(registration=active, is_current=current, expires_at=timezone.now()+timedelta(days=4))
        response = self.page()
        row = response.context['home_events'][0]
        expected=directory_counts(self.admin,[event])[event.pk]['registered']
        self.assertEqual(expected,2)
        self.assertEqual(row.directory_registered,expected)
        self.assertEqual(response.context['home_registration_state'],'available')

    def test_registration_zero_restricted_and_failed_reads_remain_distinct(self):
        event=self.event()
        response=self.page()
        self.assertEqual(response.context['home_events'][0].directory_registered,0)
        self.grant(self.staff,'events.view_event')
        response=self.page(self.staff)
        self.assertIsNone(response.context['home_events'][0].directory_registered)
        self.assertEqual(response.context['home_registration_state'],'restricted')
        self.assertContains(response,'Registered count unavailable for your role')
        with patch('apps.internal_ui.event_presentation.directory_counts',side_effect=DatabaseError('synthetic')):
            response=self.page()
        self.assertContains(response,event.name)
        self.assertEqual(response.context['home_registration_state'],'unavailable')
        self.assertIsNone(response.context['home_events'][0].directory_registered)
        self.assertContains(response,'Registered count could not be loaded')

    def test_scoped_events_use_existing_assignee_and_manager_destinations(self):
        assigned=self.event(name='SYNTHETIC assigned')
        managed=self.event(name='SYNTHETIC managed')
        hidden=self.event(name='SYNTHETIC unrelated')
        StaffAssignment.objects.create(event=assigned,staff=self.profiles[self.staff.pk],status='confirmed')
        EventExecution.objects.create(event=managed,primary_manager=self.profiles[self.staff.pk])
        response=self.page(self.staff)
        urls={e.pk:e.home_url for e in response.context['home_events']}
        self.assertEqual(urls[assigned.pk],reverse('staffing:staff-event',args=[assigned.pk]))
        self.assertEqual(urls[managed.pk],reverse('operations:execution',args=[managed.pk]))
        self.assertNotIn(hidden.pk,urls)
        self.assertNotContains(response,'href="'+reverse('events:event_detail',args=[assigned.pk])+'"')
        self.assertEqual(self.client.get(urls[assigned.pk]).status_code,200)
        self.assertEqual(self.client.get(urls[managed.pk]).status_code,200)

    def test_home_get_has_no_writes_or_preference_mutation(self):
        self.review();self.event();self.task()
        self.page()
        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse('internal:home'))
        writes=[q["sql"] for q in queries if q["sql"].lstrip().upper().startswith(("INSERT ","UPDATE ","DELETE ","REPLACE ","CREATE ","ALTER ","DROP "))]
        self.assertEqual(writes,[])
        self.admin.refresh_from_db()
        self.assertIsNone(self.admin.quick_action_ids)

    def test_queries_stay_bounded_as_events_registrations_and_cases_grow(self):
        self.event();self.review();home_data(self.admin)
        with CaptureQueriesContext(connection) as small:home_data(self.admin)
        for i in range(12):
            event=self.event(name='SYNTHETIC more '+str(i))
            person=Participant.objects.create(first_name='SYNTHETIC',last_name='Growth '+str(i))
            EventRegistration.objects.create(event=event,participant=person)
            self.review()
        with CaptureQueriesContext(connection) as large:data=home_data(self.admin)
        self.assertEqual(len(data['home_events']),3)
        self.assertLessEqual(len(large),len(small)+1)
        self.assertLessEqual(len(large),25)

    def test_manager_destination_queries_are_bulk_not_per_event(self):
        first=self.event();EventExecution.objects.create(event=first,primary_manager=self.profiles[self.staff.pk])
        home_data(self.staff)
        with CaptureQueriesContext(connection) as small:home_data(self.staff)
        for i in range(5):EventExecution.objects.create(event=self.event(),primary_manager=self.profiles[self.staff.pk])
        with CaptureQueriesContext(connection) as large:data=home_data(self.staff)
        self.assertEqual(len(data['home_events']),3)
        self.assertLessEqual(len(large),len(small)+1)

    def test_add_participant_uses_staff_flow_or_explicit_admin_fallback_without_new_grants(self):
        self.grant(self.staff,'participants.add_participant')
        self.staff.quick_action_ids=['add-participant','settings'];self.staff.save(update_fields=['quick_action_ids'])
        action=quick_actions_for(self.staff)[0]
        self.assertEqual(action['id'],'add-participant')
        self.assertEqual(action['url'],reverse('admin:participants_participant_add'))
        self.assertIn('administration',action['label'])
        self.assertFalse(self.staff.has_perm('participants.view_participant'))
        self.grant(self.staff,'participants.view_participant')
        action=quick_actions_for(self.staff)[0]
        self.assertEqual(action['url'],reverse('participants:staff-add'))
        self.assertEqual(action['label'],'Add Participant')
        self.assertEqual([a['id'] for a in quick_actions_for(self.staff)],['add-participant','settings'])
        self.assertEqual(next(a for a in available_quick_actions(self.admin) if a['id']=='add-participant')['url'],reverse('participants:staff-add'))
        self.grant(self.other,'participants.view_participant')
        self.assertNotIn('add-participant',[a['id'] for a in available_quick_actions(self.other)])
        for values in ({'is_active':False,'is_staff':True},{'is_staff':False,'is_active':True}):
            user=get_user_model().objects.create_user('SYNTHETIC-blocked-'+str(len(values))+str(get_user_model().objects.count()),**values)
            self.assertEqual(available_quick_actions(user),[])

    def test_personal_previews_and_shortcuts_have_one_ordered_set_of_controls(self):
        self.staff.quick_action_ids=['settings','create-task'];self.staff.save(update_fields=['quick_action_ids'])
        for i in range(4):
            task=self.task(title='SYNTHETIC preview '+str(i))
            audit=AuditLog.objects.create(actor=self.other,action='synthetic.home.notice',content_type=ContentType.objects.get_for_model(task),object_id=str(task.pk))
            TaskNotification.objects.create(recipient=self.staff,task=task,source_event=audit,kind='assigned',message='SYNTHETIC task update')
        response=self.page(self.staff);markup=Markup();markup.feed(response.content.decode())
        self.assertEqual([a['id'] for a in response.context['quick_actions']],['settings','create-task'])
        self.assertEqual(len([x for x in markup.with_class('p2-task-list')[0]['children'] if x['tag']=='li']),2)
        self.assertEqual(len([x for x in markup.with_class('dashboard-notifications')[0]['children'] if x['tag']=='li']),2)
        self.assertEqual(response.context['task_updates']['unread'],4)
        self.assertEqual(len(markup.with_class('dashboard-notice-read')),3)
        self.assertContains(response,'More tasks');self.assertContains(response,'More updates')
        ids=[x['attrs']['id'] for x in markup.nodes if 'id' in x['attrs']]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertNotContains(response,'Hiring Activity')
        self.assertNotContains(response,'ACTIVE CANDIDATES')

    def test_existing_daily_hours_are_labeled_instead_of_inferred_from_the_multiday_window(self):
        event=self.event(start_at=datetime(2026,10,28,13,tzinfo=UTC),
            end_at=datetime(2026,10,30,21,tzinfo=UTC), daily_start_time=time(9), daily_end_time=time(17))
        with patch('django.utils.timezone.now',return_value=datetime(2026,10,27,12,tzinfo=UTC)):
            response=self.page()
        self.assertContains(response,'Daily hours: 9:00 AM – 5:00 PM')
        self.assertContains(response,'Oct 28, 2026 – Oct 30, 2026')
        self.assertNotContains(response,'Window:')
