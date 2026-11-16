"""Source-backed reminders use fictional fixtures and the existing durable clock."""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import AuditLog
from apps.events.models import Event
from apps.staffing.models import StaffingPlan, StaffAssignment, AssignmentDay, AvailabilityRequest
from apps.participants.models import Participant, ParticipantQualification, ParticipantDocument
from apps.participants.participant_document_models import ParticipantDocumentCategory
from apps.participants.verification_models import ParticipantVerificationRecord
from apps.participants.verification_services import qualification_snapshot
from apps.customers.models import CustomerOrganization, Campaign, WorkforceNeed, WorkforceNeedRevision
from .execution_models import EventExecution, EventChecklistCategory, EventChecklistItem
from .models import TaskNotification, TaskReminderItem, TaskReminderJob, TaskReminderPolicy
from .event_reminder_sources import event_sources, verification_source, job_source, campaign_source
from .event_reminder_planning import plan_sources
from .reminder_rendering import decorate, visible_items
from .reminders import run_tick, claim_job, deliver_job
from .test_reminders_18a import Fixtures, clock


@override_settings(BTW_TASK_REMINDERS_ENABLED=True, BTW_EVENT_APPROVAL_REMINDERS_ENABLED=True)
class EventReminders18BTests(Fixtures, TestCase):
    def setUp(self):
        self.make_fixture()
        self.event.start_at, self.event.end_at = clock(20, 9), clock(20, 17)
        self.event.save()
        self.execution = EventExecution.objects.create(event=self.event, primary_manager=self.profiles[self.admin.pk])
        self.plan = StaffingPlan.objects.create(event=self.event, required_staff=1)

    def item(self, *, phase='prepare', assigned=None, required=True):
        category = EventChecklistCategory.objects.create(execution=self.execution, title='SAMPLE work', phase=phase)
        return EventChecklistItem.objects.create(category=category, title='SAMPLE ready', is_required=required,
                                                assigned_staff=assigned)

    def notices(self, user=None, now=None):
        with patch('django.utils.timezone.now', return_value=now or clock()):
            return decorate(user or self.admin, TaskNotification.objects.filter(recipient=user or self.admin, kind='summary'))

    def test_staffing_both_windows_repeat_ticks_and_shared_summary(self):
        for day in (13, 13, 18, 18):
            run_tick(now=clock(day))
        self.assertEqual(TaskReminderItem.objects.filter(kind='source').count(), 2)
        self.assertEqual(TaskNotification.objects.filter(kind='summary', recipient=self.admin).count(), 2)
        notice = self.notices(now=clock(18))[0]
        self.assertEqual(notice.summary_sections[0]['title'], 'Staffing')
        self.assertIn('Staff: 1 needed', notice.summary_sections[0]['rows'][0]['label'])
        self.assertFalse(TaskNotification.objects.filter(kind='source').exists())

    def test_pending_request_never_counts_as_confirmed_coverage(self):
        AvailabilityRequest.objects.create(event=self.event, staff=self.profiles[self.staff.pk], requested_dates=['2026-11-20'])
        self.assertEqual(len(event_sources(self.event, clock())), 1)
        assignment = StaffAssignment.objects.create(event=self.event, staff=self.profiles[self.staff.pk], coverage_role='staff')
        AssignmentDay.objects.create(assignment=assignment, date=date(2026, 11, 20), starts_at=clock(20, 9), ends_at=clock(20, 17))
        self.assertEqual(event_sources(self.event, clock()), [])

    def test_resolved_cancelled_rescheduled_and_timezone_changed_hide_old_details(self):
        run_tick(now=clock())
        self.assertTrue(self.notices()[0].summary_sections)
        self.event.start_at, self.event.end_at = clock(27, 9), clock(27, 17)
        self.event.save()
        self.assertEqual(self.notices()[0].item_count, 0)
        self.event.start_at, self.event.end_at = clock(20, 9), clock(20, 17)
        self.event.timezone_name = 'America/Chicago'
        self.event.save()
        self.assertEqual(self.notices()[0].item_count, 0)
        self.event.status = 'cancelled'
        self.event.save()
        self.assertEqual(event_sources(self.event, clock()), [])

    def test_changed_owner_does_not_leave_an_old_recipients_private_rows(self):
        run_tick(now=clock())
        self.execution.primary_manager = self.profiles[self.manager.pk]
        self.execution.save()
        self.assertEqual(self.notices()[0].item_count, 0)

    def test_missing_inactive_owner_is_not_replaced_by_an_admin(self):
        self.profiles[self.admin.pk].is_active = False
        self.profiles[self.admin.pk].save()
        issues = []
        self.assertEqual(event_sources(self.event, clock(), issues=issues), [])
        self.assertTrue(issues)
        run_tick(now=clock())
        self.assertFalse(TaskNotification.objects.filter(kind='summary').exists())

    def test_assigned_preparation_unassigned_owner_and_optional_exclusion(self):
        assigned = self.item(assigned=self.profiles[self.other.pk])
        self.item()
        self.item(required=False)
        sources = event_sources(self.event, clock(17))
        prep = [source for source in sources if source.ref['type'] == 'preparation']
        # A bare staff flag does not authorize the assigned person to this event.
        self.assertEqual(len(prep), 1)
        self.assertEqual(prep[0].user, self.admin)
        StaffAssignment.objects.create(event=self.event, staff=self.profiles[self.other.pk])
        prep = [source for source in event_sources(self.event, clock(17)) if source.ref['type'] == 'preparation']
        self.assertEqual({source.user.pk for source in prep}, {self.admin.pk, self.other.pk})
        assigned.state = 'na'; assigned.na_reason = 'SAMPLE not needed'; assigned.save()
        self.assertEqual(len([s for s in event_sources(self.event, clock(17)) if s.ref['type'] == 'preparation']), 1)

    def test_setup_date_is_authoritative_and_weekend_event_link_alone_is_insufficient(self):
        self.event.setup_date = date(2026, 11, 18); self.event.save()
        self.item()
        # Sunday advance falls back to Friday; Sunday itself has no actual work.
        self.assertTrue(any(s.ref['type'] == 'preparation' for s in event_sources(self.event, clock(13))))
        self.assertEqual(event_sources(self.event, clock(15)), [])

    def test_closeout_gathering_period_and_cancelled_real_obligations(self):
        self.event.start_at, self.event.end_at = clock(13, 9), clock(13, 17)
        self.event.status = 'cancelled'; self.event.save()
        self.item(phase='closeout')
        for day in (14, 16, 17):
            self.assertEqual(event_sources(self.event, clock(day)), [])
        sources = event_sources(self.event, clock(18))
        self.assertEqual([source.ref['type'] for source in sources], ['closeout'])
        run_tick(now=clock(18)); run_tick(now=clock(19))
        self.assertEqual(TaskReminderItem.objects.filter(kind='source').count(), 2)
        self.execution.actual_ended_at, self.execution.closed_at = clock(13, 17), clock(19, 8)
        self.execution.save()
        self.assertEqual(event_sources(self.event, clock(20)), [])

    def test_no_financial_obligation_invented_from_a_bare_event(self):
        self.event.start_at, self.event.end_at = clock(6, 9), clock(6, 17)
        self.event.status = 'completed'; self.event.save()
        self.assertEqual(event_sources(self.event, clock()), [])

    def test_explicit_closeout_task_is_not_sent_by_the_task_reminder_path(self):
        task = self.task(assignee=self.admin)
        self.execution.closeout_task = task; self.execution.save()
        run_tick(now=clock()); run_tick(now=clock(16))
        self.assertFalse(TaskReminderItem.objects.filter(task=task).exists())
        # Human assignment history remains intact.
        self.assertTrue(TaskNotification.objects.filter(task=task, kind='assigned').exists())

    def test_claim_rechecks_resolution_and_restart_is_idempotent(self):
        plan_sources(clock())
        job = claim_job(now=clock())
        self.plan.required_staff = 0; self.plan.save()
        self.assertEqual(deliver_job(job, now=clock()), 'empty')
        self.plan.required_staff = 1; self.plan.save()
        run_tick(now=clock(18)); run_tick(now=clock(18))
        self.assertEqual(TaskNotification.objects.filter(kind='summary').count(), 1)

    def test_non_eastern_summary_has_its_own_local_seven_am(self):
        self.event.timezone_name = 'America/Los_Angeles'; self.event.save()
        run_tick(now=clock(13, 9))
        self.assertFalse(TaskNotification.objects.filter(kind='summary').exists())
        run_tick(now=clock(13, 10))
        self.assertEqual(TaskNotification.objects.filter(kind='summary').count(), 1)

    def test_staffing_approvals_explicit_recipient_daily_and_no_self_review(self):
        request = AvailabilityRequest.objects.create(event=self.event, staff=self.profiles[self.staff.pk], requested_dates=['2026-11-20'])
        TaskReminderPolicy.objects.create(key='company', staffing_review_recipient=self.admin)
        self.plan.required_staff = 0; self.plan.save()
        run_tick(now=clock()); run_tick(now=clock(16))
        self.assertEqual(TaskReminderItem.objects.filter(kind='source').count(), 2)
        request.status = 'withdrawn'; request.save()
        self.assertEqual(self.notices(now=clock(16))[0].item_count, 0)

    def test_qualification_only_explicit_latest_pending_snapshot_qualifies(self):
        person = Participant.objects.create(first_name='SAMPLE', last_name='Review')
        qualification = ParticipantQualification.objects.create(participant=person, custom_name='SAMPLE license')
        TaskReminderPolicy.objects.create(key='company', verification_review_recipient=self.admin)
        record = ParticipantVerificationRecord.objects.create(participant=person, qualification=qualification,
            qualification_snapshot=qualification_snapshot(qualification))
        self.assertIsNotNone(verification_source(record, clock()))
        qualification.notes = 'SAMPLE changed'; qualification.save()
        self.assertIsNone(verification_source(record, clock()))
        qualification.notes = ''; qualification.save()
        ParticipantVerificationRecord.objects.create(participant=person, qualification=qualification,
            status='verified', supersedes=record, reviewer=self.admin, reviewed_at=clock(), method='staff_confirmed',
            evidence_notes='SAMPLE supporting review')
        self.assertIsNone(verification_source(record, clock()))

    def test_document_review_requires_private_document_access(self):
        person = Participant.objects.create(first_name='SAMPLE', last_name='Private')
        document = ParticipantDocument.objects.create(participant=person, original_filename='SAMPLE.txt',
            category=ParticipantDocumentCategory.objects.create(name='SAMPLE document', slug='sample-doc'),
            file='sample/not-a-real-file', content_type='text/plain', file_size=1)
        record = ParticipantVerificationRecord.objects.create(participant=person, document=document)
        self.other.user_permissions.add(*Permission.objects.filter(codename__in=(
            'view_participant', 'review_qualificationapprovals')))
        TaskReminderPolicy.objects.create(key='company', verification_review_recipient=self.other)
        self.assertIsNone(verification_source(record, clock()))
        self.other.user_permissions.add(Permission.objects.get(codename='view_participantdocument'))
        self.assertIsNotNone(verification_source(record, clock()))
        document.state = 'archived'; document.save()
        self.assertIsNone(verification_source(record, clock()))

    def test_customer_campaign_review_has_real_submission_version_and_no_default_recipient(self):
        org = CustomerOrganization.objects.create(name='SAMPLE customer')
        campaign = Campaign.objects.create(organization=org, name='SAMPLE campaign', review_state='submitted',
            planning_version=1, submitted_planning_version=1, submitted_at=clock(), submitted_by=self.admin)
        self.assertIsNone(campaign_source(campaign, clock()))
        TaskReminderPolicy.objects.create(key='company', customer_review_recipient=self.admin)
        self.assertIsNotNone(campaign_source(campaign, clock()))
        campaign.planning_version = 2
        self.assertIsNone(campaign_source(campaign, clock()))
        campaign.review_state = 'draft'; campaign.save()
        self.assertIsNone(campaign_source(campaign, clock()))

    def test_job_review_uses_current_explicit_submission_and_stops_on_withdrawal(self):
        from apps.customers.test_domain import CustomerFixtures
        fixture = CustomerFixtures()
        fixture.admin = self.admin
        org = fixture.org(name='SAMPLE job customer')
        need = fixture.draft(org)
        revision = need.working_revision
        TaskReminderPolicy.objects.create(key='company', job_review_recipient=self.admin)
        self.assertIsNone(job_source(revision, clock()))
        fixture.transition(org, need, 'submit')
        revision.refresh_from_db()
        self.assertIsNotNone(job_source(revision, clock()))
        fixture.transition(org, need, 'review')
        revision.refresh_from_db()
        self.assertIsNotNone(job_source(revision, clock()))
        fixture.transition(org, need, 'withdraw')
        revision.refresh_from_db()
        self.assertIsNone(job_source(revision, clock()))

    def test_catchup_uses_only_latest_staffing_window_and_never_creates_domain_work(self):
        from .models import Task
        before = (Task.objects.count(), AuditLog.objects.count(), StaffAssignment.objects.count())
        run_tick(now=clock(19)); run_tick(now=clock(19))
        self.assertEqual(TaskReminderItem.objects.filter(kind='source').count(), 1)
        self.assertEqual((Task.objects.count(), AuditLog.objects.count(), StaffAssignment.objects.count()), before)
        self.assertEqual(self.notices(now=clock(20))[0].item_count, 0)

    def test_disabled_source_policy_hides_old_summary_without_deleting_history(self):
        run_tick(now=clock())
        TaskReminderPolicy.objects.create(key='company', staffing_enabled=False)
        self.assertEqual(self.notices()[0].item_count, 0)
        self.assertEqual(TaskReminderItem.objects.filter(kind='source').count(), 1)
        self.assertEqual(TaskNotification.objects.filter(kind='summary').count(), 1)

    def test_source_snooze_preserves_work_and_audits_it(self):
        run_tick(now=clock())
        notice = self.summaries(self.admin).get()
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        with patch('django.utils.timezone.now', return_value=clock(13, 8)):
            response = self.client.post(reverse('operations:reminder-snooze', args=[notice.pk]), {'choice': 'morning'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.notices(now=clock(13, 9))[0].item_count, 0)
        self.plan.refresh_from_db(); self.assertEqual(self.plan.required_staff, 1)
        self.assertTrue(AuditLog.objects.filter(action='operations.reminders.sources_snoozed').exists())

    def test_home_removes_exact_summary_work_but_preserves_other_same_title_tasks(self):
        from apps.internal_ui.home_data import home_data
        first = self.task(due=date(2026, 11, 12), assignee=self.admin)
        other = self.task(due=date(2026, 11, 12), assignee=self.staff)
        run_tick(now=clock())
        with patch('django.utils.timezone.now', return_value=clock()):
            result = home_data(self.admin)
        self.assertEqual(result['home_overdue_attention']['count'], 1)
        self.assertEqual(result['home_metrics'][1]['value'], 2)
        self.assertEqual(first.title, other.title)
        self.assertEqual(next((item['count'] for item in result['home_attention'] if item['id'] == 'short-staffed'), 0), 0)

    def test_home_read_or_snoozed_summary_does_not_reintroduce_same_attention(self):
        from apps.operations.reminder_home import coverage
        run_tick(now=clock())
        notice = self.summaries(self.admin).get()
        notice.read_at = clock(); notice.snoozed_until = clock(13, 12); notice.save()
        covered = coverage(self.admin, now=clock(13, 8))
        self.assertTrue(covered['staffing'])
        self.assertFalse(coverage(self.other, now=clock(13, 8))['staffing'])
        # Advance windows do not repeat daily; the still-current Friday summary
        # must continue to cover its exact gap on Monday.
        self.assertTrue(coverage(self.admin, now=clock(16))['staffing'])
        self.plan.required_staff = 0; self.plan.save()
        self.assertFalse(coverage(self.admin, now=clock(16))['staffing'])

    def test_popup_uses_existing_editor_contract_and_no_reason_field(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('operations:reminder-company'), HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-BTW-Editor'], '1')
        self.assertContains(response, 'data-editor-form')
        self.assertContains(response, 'cp3-card')
        self.assertNotContains(response, 'name="reason"')
        self.assertContains(response, 'Customer-approval recipient')
        self.client.force_login(self.staff, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('operations:reminder-company'), HTTP_X_BTW_EDITOR='1').status_code, 403)

    def test_settings_popup_stale_revision_keeps_entries_and_does_not_audit_a_save(self):
        TaskReminderPolicy.objects.create(key='company', advance_enabled=True, activity_enabled=True, completion_enabled=True)
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        form = self.client.get(reverse('operations:reminder-company'), HTTP_X_BTW_EDITOR='1').context['form']
        data = {name: form[name].value() or '' for name in form.fields}
        data['revision'] = 'new'
        before = AuditLog.objects.filter(action='operations.reminders.policy_updated').count()
        response = self.client.post(reverse('operations:reminder-company'), data, HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'data-save-blocked', status_code=409)
        self.assertEqual(AuditLog.objects.filter(action='operations.reminders.policy_updated').count(), before)

    def test_personal_popup_stale_save_preserves_preference_and_audit(self):
        from .reminder_preferences import save_personal
        from .models import TaskReminderPreference
        save_personal(actor=self.staff, activity_updates=False, expected_revision='new')
        self.client.force_login(self.staff, backend='django.contrib.auth.backends.ModelBackend')
        count = AuditLog.objects.count()
        response = self.client.post(reverse('operations:reminder-preferences'),
            {'revision': 'new', 'activity_updates': 'on'}, HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'data-save-blocked', status_code=409)
        self.assertFalse(TaskReminderPreference.objects.get(user=self.staff).activity_updates)
        self.assertEqual(AuditLog.objects.count(), count)
