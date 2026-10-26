"""Build 10C contracts: exact time preservation, navigation, projections and a read-only foundation."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.forms import EventForm
from apps.events.models import Event
from apps.events.event_management import save_event
from apps.staffing.models import StaffProfile, StaffAssignment, AssignmentDay
from apps.operations.models import Task, EventOperationsApplication
from apps.operations.health_presentation import health_context, PLANNING_PHASES
from apps.internal_ui.event_presentation import event_tabs, staff_tabs

class Build10CTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.admin = U.objects.create_superuser('synthetic-10c-admin', email='review@example.test', password=None)
        cls.staff = U.objects.create_user('synthetic-10c-staff', is_staff=True)
        cls.profile = StaffProfile.objects.create(user=cls.staff, first_name='SYNTHETIC', last_name='Reviewer')
        start = (timezone.now()+timedelta(days=7)).replace(hour=14, minute=15, second=47, microsecond=123456)
        cls.event = Event.objects.create(name='SYNTHETIC 10C Event', start_at=start, end_at=start+timedelta(hours=4), timezone_name='America/New_York', status='upcoming')
        assignment = StaffAssignment.objects.create(event=cls.event, staff=cls.profile)
        AssignmentDay.objects.create(assignment=assignment, date=cls.event.local_start_at.date(), starts_at=start, ends_at=cls.event.end_at)

    def login(self, user=None):
        self.client.force_login(user or self.admin, backend='django.contrib.auth.backends.ModelBackend')

    def grant(self, *names):
        for name in names:
            app, code = name.split('.')
            self.staff.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
        for cache in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
            self.staff.__dict__.pop(cache, None)

    def payload(self, event=None):
        event = event or self.event
        form = EventForm(instance=event)
        data = {name: form[name].value() if form[name].value() is not None else '' for name in EventForm.Meta.fields}
        data['revision'] = event.updated_at.isoformat()
        return data

    def url(self, section='overview'):
        return reverse('events:event_detail', args=[self.event.pk])+'?section='+section

    def test_widget_uses_browser_supported_milliseconds_and_retains_exact_storage(self):
        form = EventForm(instance=self.event)
        self.assertEqual(form.initial['start_at'], self.event.local_start_at.replace(tzinfo=None).isoformat(timespec='milliseconds'))
        self.assertIn('47.123', form.initial['start_at'])
        self.assertNotIn('123456', form.initial['start_at'])
        self.assertIn('full stored timestamp', form.fields['start_at'].help_text)
        self.assertEqual(form.fields['start_at'].widget.attrs['step'], 'any')

    def test_unrelated_edit_keeps_exact_event_window_with_confirmed_staff(self):
        original = (self.event.start_at, self.event.end_at)
        data = self.payload(); data['registration_capacity'] = '45'
        saved, form = save_event(actor=self.admin, event_id=self.event.pk, data=data)
        self.assertIsNotNone(saved, form.errors)
        self.event.refresh_from_db()
        self.assertEqual((self.event.start_at, self.event.end_at), original)
        self.assertEqual(self.event.registration_capacity, 45)
        day = AssignmentDay.objects.get(assignment__event=self.event)
        self.assertEqual((day.starts_at, day.ends_at), original)

    def test_legitimate_staffing_start_guard_remains(self):
        original = self.event.start_at
        data = self.payload(); data['start_at'] = (self.event.local_start_at+timedelta(minutes=1)).replace(tzinfo=None).isoformat()
        saved, form = save_event(actor=self.admin, event_id=self.event.pk, data=data)
        self.assertIsNone(saved); self.assertIn('confirmed working times', str(form.errors))
        self.event.refresh_from_db(); self.assertEqual(self.event.start_at, original)

    def test_legitimate_staffing_end_guard_remains(self):
        data = self.payload(); data['end_at'] = (self.event.local_end_at-timedelta(minutes=1)).replace(tzinfo=None).isoformat()
        saved, form = save_event(actor=self.admin, event_id=self.event.pk, data=data)
        self.assertIsNone(saved); self.assertIn('confirmed working times', str(form.errors))

    def test_explicit_schedule_change_is_not_silently_reverted(self):
        data = self.payload(); changed = self.event.local_start_at-timedelta(minutes=2)
        data['start_at'] = changed.replace(tzinfo=None).isoformat()
        saved, form = save_event(actor=self.admin, event_id=self.event.pk, data=data)
        self.assertIsNotNone(saved, form.errors); self.assertEqual(saved.start_at, changed)

    def test_explicit_minute_input_is_an_actual_edit_not_an_implicit_expansion(self):
        data = self.payload(); changed = self.event.local_start_at.replace(second=0, microsecond=0)
        data['start_at'] = changed.isoformat()
        saved, form = save_event(actor=self.admin, event_id=self.event.pk, data=data)
        self.assertIsNotNone(saved, form.errors); self.assertEqual(saved.start_at, changed)

    def test_existing_dst_fold_round_trip_preserves_known_instant(self):
        zone = ZoneInfo('America/New_York')
        start = datetime(2026,11,1,1,30,47,123456,tzinfo=zone,fold=1)
        event = Event.objects.create(name='SYNTHETIC fold',start_at=start,end_at=start+timedelta(hours=2),timezone_name='America/New_York')
        saved, form = save_event(actor=self.admin,event_id=event.pk,data=self.payload(event))
        self.assertIsNotNone(saved, form.errors); self.assertEqual(saved.start_at.timestamp(),start.timestamp())

    def test_changed_ambiguous_local_time_is_still_rejected(self):
        data=self.payload();data['start_at']='2026-11-01T01:40:00'
        form=EventForm(data,instance=self.event)
        self.assertFalse(form.is_valid());self.assertIn('unambiguous',str(form.errors))

    def test_nonexistent_local_time_is_still_rejected(self):
        data=self.payload();data['start_at']='2026-03-08T02:30:00'
        form=EventForm(data,instance=self.event)
        self.assertFalse(form.is_valid());self.assertIn('unambiguous',str(form.errors))

    def test_registration_times_also_round_trip_without_truncation(self):
        self.event.registration_opens_at=self.event.start_at-timedelta(days=1);self.event.save()
        original=self.event.registration_opens_at
        data=self.payload();data['description']='SYNTHETIC unrelated change'
        saved,form=save_event(actor=self.admin,event_id=self.event.pk,data=data)
        self.assertIsNotNone(saved,form.errors);self.assertEqual(saved.registration_opens_at,original)

    def test_stale_revision_guard_is_preserved(self):
        data=self.payload();data['revision']='stale'
        saved,form=save_event(actor=self.admin,event_id=self.event.pk,data=data)
        self.assertIsNone(saved);self.assertIn('changed after you opened',str(form.errors))

    def test_create_event_is_left_of_search_and_clear_is_inside_filters(self):
        self.login();response=self.client.get(reverse('events:event_list'))
        html=response.content.decode()
        self.assertLess(html.index('event-primary-create'),html.index('name="q"'))
        details=html.split('<details class="event-filter-popover"',1)[1].split('</details>',1)[0]
        self.assertIn('Clear filters',details);self.assertEqual(html.count('>Clear filters</a>'),1)

    def test_status_filters_and_invalid_input_are_preserved(self):
        self.login();response=self.client.get(reverse('events:event_list'),{'q':'SYNTHETIC 10C','view':'upcoming'})
        self.assertContains(response,self.event.name)
        self.assertEqual(self.client.get(reverse('events:event_list'),{'date_from':'invalid'}).status_code,400)

    def test_event_header_has_no_task_or_report_button(self):
        self.login();html=self.client.get(self.url()).content.decode()
        header=html.split('<header class="event-record-header"',1)[1].split('</header>',1)[0]
        self.assertNotIn('data-task-create',header);self.assertNotIn('Event Report</a>',header)
        self.assertEqual(html.count('data-global-task-create'),1)
        self.assertIn('button-primary global-task-create',html)
        self.assertIn(reverse('operations:quick-create-event',args=[self.event.pk]),html)

    def test_event_tabs_have_distinct_expenses_and_report_and_no_workday_top_tab(self):
        tabs={t['id']:t for t in event_tabs(self.admin,self.event)}
        self.assertNotIn('workdays',tabs)
        self.assertIn('?section=expenses',tabs['expenses']['url']);self.assertIn('?section=report',tabs['report']['url'])
        self.assertEqual(tabs['registrations']['label'],'Registrations & tickets')

    def test_tasks_notes_documents_remain_discoverable_in_more(self):
        self.login();html=self.client.get(self.url()).content.decode()
        more=html.split('aria-label="More event sections"',1)[1].split('</nav>',1)[0]
        for section in ('tasks','notes','documents'):
            self.assertIn(reverse('operations:event',args=[self.event.pk,section]),more)

    def test_more_selected_section_is_identified(self):
        self.login();response=self.client.get(reverse('operations:event',args=[self.event.pk,'notes']))
        self.assertContains(response,'More event tools, Notes selected');self.assertContains(response,'has-active')
        self.assertContains(response,reverse('operations:event',args=[self.event.pk,'notes']))

    def test_workdays_are_part_of_staff_without_changing_url_or_policy(self):
        self.login();response=self.client.get(reverse('workdays:event',args=[self.event.pk]))
        self.assertEqual(response.status_code,200);self.assertContains(response,'aria-label="Staff sections"')
        self.assertContains(response,'Assignments &amp; coverage');self.assertContains(response,'Workdays &amp; mileage')
        self.assertContains(response,reverse('staffing:event',args=[self.event.pk]))

    def test_workday_manager_does_not_gain_staffing_management(self):
        self.grant('events.view_event','workdays.manage_workdays');self.login(self.staff)
        self.assertEqual([t['id'] for t in staff_tabs(self.staff,self.event)],['workdays'])
        self.assertEqual(self.client.get(reverse('staffing:event',args=[self.event.pk])).status_code,403)
        self.assertEqual(self.client.get(reverse('workdays:event',args=[self.event.pk])).status_code,200)

    def test_expense_projection_excludes_report_but_keeps_real_expense_tools(self):
        self.login();response=self.client.get(reverse('event_reports:detail',args=[self.event.pk]),{'section':'expenses'})
        self.assertContains(response,'<h2>Expenses</h2>',html=True);self.assertNotContains(response,'<h2>Participant performance</h2>')
        self.assertContains(response,'name="section" value="expenses"')
        self.assertContains(response,reverse('event_reports:expense-add',args=[self.event.pk]))

    def test_report_projection_excludes_expense_editor_and_preserves_metrics(self):
        self.login();response=self.client.get(reverse('event_reports:detail',args=[self.event.pk]),{'section':'report'})
        self.assertContains(response,'Participant performance');self.assertNotContains(response,'<h2>Manual event expenses</h2>')
        self.assertContains(response,'Export performance CSV')

    def test_legacy_combined_report_and_anchors_still_work(self):
        self.login();response=self.client.get(reverse('event_reports:detail',args=[self.event.pk]))
        self.assertContains(response,'<h2>Expenses</h2>',html=True);self.assertContains(response,'Participant performance')
        self.assertContains(response,'id="expenses"');self.assertContains(response,'id="performance"')

    def test_projection_does_not_change_report_calculations(self):
        self.login();url=reverse('event_reports:detail',args=[self.event.pk])
        expense=self.client.get(url,{'section':'expenses'});report=self.client.get(url,{'section':'report'})
        self.assertEqual(expense.context['report']['cost']['recorded'],report.context['report']['cost']['recorded'])
        self.assertEqual(expense.context['report']['metrics'],report.context['report']['metrics'])

    def test_unauthorized_report_and_workday_direct_urls_remain_denied(self):
        self.grant('events.view_event');self.login(self.staff)
        for route in ('event_reports:detail','workdays:event'):
            self.assertEqual(self.client.get(reverse(route,args=[self.event.pk])).status_code,403)

    def test_registration_scope_is_registration_tickets_and_checkin_not_profiles(self):
        self.login();response=self.client.get(self.url('registrations'))
        self.assertContains(response,'Event registrations &amp; tickets')
        self.assertContains(response,'Register and issue ticket');self.assertContains(response,'name="action" value="register"')
        self.assertNotContains(response,'Edit participant profile')

    def test_results_keep_score_units_and_separate_configuration_from_health(self):
        self.login();response=self.client.get(self.url('results'))
        self.assertContains(response,'raw game points');self.assertContains(response,'TOTAL SCORE (%)')
        self.assertNotContains(response,'Configuration references');self.assertContains(self.client.get(reverse('events:event_detail',args=[self.event.pk]), {'section':'operations'}),'Configuration references');self.assertNotContains(response,'<h2>Event Readiness</h2>')
        self.assertContains(response,'id="event-stations"')

    def test_station_action_is_still_post_only(self):
        self.login();self.assertEqual(self.client.get(reverse('events:station_assign',args=[self.event.pk])).status_code,405)

    def test_simple_health_is_readable_to_event_viewers_without_setup_write_grants(self):
        self.grant('events.view_event');self.assertTrue(health_context(self.staff,self.event)['health_foundation_visible'])
        self.grant('operations.view_event_operations_setup')
        self.assertTrue(health_context(self.staff,self.event)['health_foundation_visible'])
        self.assertEqual(health_context(AnonymousUser(),self.event),{})

    def test_assignment_grants_own_checklist_read_but_not_global_event_management(self):
        self.assertTrue(health_context(self.staff,self.event)['health_foundation_visible'])
        self.assertEqual(self.client.get(reverse('operations:master-checklist')).status_code,302)
        self.login(self.staff);self.assertEqual(self.client.get(self.url('operations')).status_code,403)

    def test_monitor_is_pending_and_never_claims_verified_readiness(self):
        self.login();response=self.client.get(self.url('operations'))
        self.assertContains(response,'Event Health Monitor');self.assertContains(response,'does not have a checklist snapshot yet')
        self.assertContains(response,'Progress records staff checklist completion')
        self.assertNotContains(response,'Ready with Exceptions');self.assertNotContains(response,'Approve exception')
        self.assertEqual(len(PLANNING_PHASES),4)

    def test_repeated_monitor_reads_do_not_create_evidence_audits_or_tasks(self):
        self.login();before=(AuditLog.objects.count(),Task.objects.count(),EventOperationsApplication.objects.count())
        for _ in range(2):self.client.get(self.url('operations'))
        self.assertEqual(before,(AuditLog.objects.count(),Task.objects.count(),EventOperationsApplication.objects.count()))

    def test_shared_badge_escapes_content_and_restricts_style(self):
        html=Template('{% load ui_patterns %}{% ui_badge label tone %}').render(Context({'label':'<script>bad</script>','tone':'arbitrary'}))
        self.assertIn('&lt;script&gt;',html);self.assertIn('btw-badge-neutral',html);self.assertNotIn('<script>',html)

    def test_shared_metrics_distinguish_zero_from_unavailable(self):
        render=lambda value:Template('{% load ui_patterns %}{% ui_metric "Count" value %}').render(Context({'value':value}))
        self.assertIn('<strong>0</strong>',render(0));self.assertIn('Unavailable',render(None))
