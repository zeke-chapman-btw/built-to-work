"""Synthetic Build 9C workflow, privacy, scheduling and history acceptance tests."""
from datetime import UTC, datetime, date, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.events.forms import EventForm
from apps.events.event_management import save_event
from .models import StaffProfile, StaffingPlan, AvailabilityRequest, StaffAssignment, AssignmentDay
from .forms import ProfileForm, PlanForm, AssignmentForm
from .calendar import event_days, day_window
from . import services as svc


class Staffing9CTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User=get_user_model()
        cls.admin=User.objects.create_superuser('synthetic-9c-admin','admin9c@example.test','test-password')
        cls.manager=User.objects.create_user('synthetic-9c-manager',password='test-password',is_staff=True)
        cls.employee=User.objects.create_user('synthetic-9c-employee',password='test-password',is_staff=True)
        cls.other=User.objects.create_user('synthetic-9c-other',password='test-password',is_staff=True)
        cls.public=User.objects.create_user('synthetic-9c-public',password='test-password')
        cls.manager.user_permissions.add(*Permission.objects.filter(content_type__app_label='staffing',codename__in=['manage_event_staffing','view_staffprofile','add_staffprofile','change_staffprofile','link_staff_account']))
        cls.manager.user_permissions.add(Permission.objects.get(content_type__app_label='events',codename='view_event'))
        cls.person=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Employee',email='employee@example.test',user=cls.employee,internal_notes='PRIVATE MANAGER NOTE 9C',street_address='SYNTHETIC PRIVATE ADDRESS')
        cls.other_person=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Other',user=cls.other,internal_notes='OTHER PRIVATE STAFF NOTE')
        cls.event=Event.objects.create(name='SYNTHETIC 9C three-day event',start_at=datetime(2030,7,1,13,tzinfo=UTC),end_at=datetime(2030,7,2,21,tzinfo=UTC),timezone_name='America/New_York',setup_date=date(2030,6,30),daily_start_time=datetime.strptime('09:00','%H:%M').time(),daily_end_time=datetime.strptime('17:00','%H:%M').time(),status='upcoming',staff_published_at=timezone.now(),description='MANAGER DESCRIPTION',housing_information='ASSIGNED HOUSING 9C',logistics_instructions='ASSIGNED LOGISTICS 9C')
        cls.plan=StaffingPlan.objects.create(event=cls.event,required_staff=2,is_published=True)
    def setUp(self):
        self.client.force_login(self.manager,backend='django.contrib.auth.backends.ModelBackend')
    def request(self,person=None,days=None):
        record,form=svc.request_availability(actor=(person or self.person).user,event_id=self.event.pk,data={'dates':days or ['2030-07-01','2030-07-02'],'note':'SYNTHETIC offered dates'})
        self.assertIsNotNone(record,form.errors); return record
    def decision(self,record,kind='approve',**changes):
        record.refresh_from_db(); data=dict(revision=record.updated_at.isoformat(),confirm='on',reason='SYNTHETIC manager decision'); data.update(changes)
        return svc.decide_request(actor=self.manager,request_id=record.pk,decision=kind,data=data)
    def assignment(self,person=None,event=None,days=None,old=None,**changes):
        event=event or self.event; data={'revision':old.updated_at.isoformat() if old else 'new','staff':str((person or self.person).pk),'role':'Staff lead','reason':'SYNTHETIC staffing verification'}
        for day in days or ['2030-07-01']: data['day_'+day]='on'
        data.update(changes)
        obj,form=svc.save_assignment(actor=self.manager,event_id=event.pk,assignment_id=old.pk if old else None,data=data)
        self.assertIsNotNone(obj,form.errors); return obj
    def plan_data(self,**changes):
        self.plan.refresh_from_db(); data=dict(revision=self.plan.updated_at.isoformat(),required_staff=2,is_published='on',signup_deadline='')
        for d in event_days(self.event): data['need_'+d.isoformat()]=2
        data.update(changes); return data
    def profile_data(self,person=None,**changes):
        person=person or self.person; person.refresh_from_db(); data={k:('' if v is None else v) for k,v in ProfileForm(instance=person).initial.items()}; data.update(revision=person.updated_at.isoformat()); data.update(changes); return data
    def staff_login(self): self.client.force_login(self.employee,backend='django.contrib.auth.backends.ModelBackend')
    def test_profile_creation_does_not_create_or_auto_link_account(self):
        count=get_user_model().objects.count()
        person,form=svc.save_profile(actor=self.manager,data=dict(first_name='SYNTHETIC',last_name='Roster only',email=self.employee.email,is_active='on'))
        self.assertIsNotNone(person,form.errors); self.assertIsNone(person.user_id); self.assertEqual(get_user_model().objects.count(),count)
    def test_profile_edit_and_inactivate_preserve_assignments(self):
        a=self.assignment(); person,form=svc.save_profile(actor=self.manager,profile_id=self.person.pk,data=self.profile_data(phone='2025550199',is_active=''))
        self.assertIsNotNone(person,form.errors); self.assertFalse(person.is_active); self.assertEqual(person.phone,'2025550199'); a.refresh_from_db(); self.assertEqual(a.status,'confirmed')
        self.assertTrue(AuditLog.objects.filter(action='staffing.profile.updated',object_id=str(person.pk)).exists())
    def test_inactive_profile_can_be_reactivated(self):
        self.person.is_active=False; self.person.save()
        person,_=svc.save_profile(actor=self.manager,profile_id=self.person.pk,data=self.profile_data(is_active='on')); self.assertTrue(person.is_active)
    def test_explicit_account_link_requires_confirmation_and_preserves_permissions(self):
        p=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Unlinked')
        user=get_user_model().objects.create_user('synthetic-unlinked',is_staff=True)
        before=list(user.user_permissions.values_list('pk',flat=True))
        saved,form=svc.link_account(actor=self.manager,profile_id=p.pk,data=dict(revision=p.updated_at.isoformat(),user=str(user.pk)))
        self.assertIsNone(saved); self.assertIn('confirm',form.errors)
        saved,_=svc.link_account(actor=self.manager,profile_id=p.pk,data=dict(revision=p.updated_at.isoformat(),user=str(user.pk),confirm='on'))
        self.assertEqual(saved.user_id,user.pk); self.assertEqual(list(user.user_permissions.values_list('pk',flat=True)),before)
    def test_account_link_rejects_already_linked_user(self):
        saved,form=svc.link_account(actor=self.manager,profile_id=self.other_person.pk,data=dict(revision=self.other_person.updated_at.isoformat(),user=str(self.employee.pk),confirm='on'))
        self.assertIsNone(saved); self.assertIn('user',form.errors)
    def test_account_link_can_be_removed_without_deleting_user(self):
        p,_=svc.link_account(actor=self.manager,profile_id=self.person.pk,data=dict(revision=self.person.updated_at.isoformat(),user='',confirm='on'))
        self.assertIsNone(p.user_id); self.assertTrue(get_user_model().objects.filter(pk=self.employee.pk).exists())
    def test_profile_view_is_private_and_own_profile_hides_internal_notes(self):
        self.staff_login()
        self.assertEqual(self.client.get(reverse('staffing:profile',args=[self.other_person.pk])).status_code,403)
        r=self.client.get('/staff/',{'view':'profile'}); self.assertContains(r,'SYNTHETIC PRIVATE ADDRESS'); self.assertNotContains(r,'PRIVATE MANAGER NOTE'); self.assertNotContains(r,'OTHER PRIVATE STAFF NOTE')
    def test_general_staff_cannot_create_edit_link_or_list_private_profiles(self):
        self.staff_login()
        for url in ['/staff/directory/','/staff/directory/add/',reverse('staffing:profile-edit',args=[self.person.pk]),reverse('staffing:account-link',args=[self.person.pk])]:
            self.assertEqual(self.client.post(url,{}).status_code,403,url)
    def test_profile_stale_edit_is_rejected(self):
        data=self.profile_data(); self.person.phone='changed'; self.person.save()
        with self.assertRaises(ValidationError): svc.save_profile(actor=self.manager,profile_id=self.person.pk,data=data)
        self.person.refresh_from_db(); self.assertEqual(self.person.phone,'changed')
    def test_staff_directory_search_inactive_and_pagination(self):
        self.other_person.is_active=False; self.other_person.save()
        r=self.client.get('/staff/directory/',{'q':'Other','status':'inactive'}); self.assertContains(r,'SYNTHETIC Other'); self.assertNotContains(r,'SYNTHETIC Employee')
        self.assertContains(self.client.get('/staff/directory/'),'Add staff')
    def test_availability_starts_pending_with_original_dates(self):
        r=self.request(); self.assertEqual(r.status,'pending'); self.assertEqual(r.requested_dates,['2030-07-01','2030-07-02']); self.assertFalse(StaffAssignment.objects.exists())
    def test_duplicate_pending_and_approved_requests_rejected(self):
        r=self.request()
        with self.assertRaises(ValidationError): self.request()
        self.decision(r)
        with self.assertRaises(ValidationError): self.request()
    def test_database_constraint_prevents_duplicate_active_requests(self):
        self.request()
        with self.assertRaises(IntegrityError),transaction.atomic(): AvailabilityRequest.objects.create(event=self.event,staff=self.person)
    def test_approval_automatically_confirms_all_requested_days(self):
        r=self.request(days=['2030-06-30','2030-07-02']); self.decision(r); r.refresh_from_db()
        a=StaffAssignment.objects.get(source_request=r,status='confirmed'); self.assertEqual(r.status,'approved'); self.assertEqual([d.isoformat() for d in a.days.values_list('date',flat=True)],r.requested_dates)
    def test_decline_creates_no_assignment_and_keeps_reason(self):
        r=self.request(); self.decision(r,'decline'); r.refresh_from_db(); self.assertEqual(r.status,'declined'); self.assertTrue(r.decision_reason); self.assertFalse(StaffAssignment.objects.exists())
    def test_decline_requires_reason(self):
        r=self.request()
        with self.assertRaises(ValidationError): self.decision(r,'decline',reason='')
        r.refresh_from_db(); self.assertEqual(r.status,'pending')
    def test_decision_requires_confirmation_and_cannot_repeat(self):
        r=self.request(); saved,form=self.decision(r,confirm=''); self.assertIsNone(saved); self.assertIn('confirm',form.errors)
        self.decision(r)
        with self.assertRaises(ValidationError): self.decision(r)
        self.assertEqual(StaffAssignment.objects.count(),1)
    def test_manager_cannot_decide_own_request(self):
        p=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='Manager',user=self.manager); r=self.request(p)
        with self.assertRaises(PermissionDenied): self.decision(r)
    def test_request_only_uses_signed_in_staff_profile(self):
        self.staff_login(); response=self.client.post(reverse('staffing:staff-event',args=[self.event.pk]),{'dates':['2030-07-01'],'staff':str(self.other_person.pk)})
        self.assertEqual(response.status_code,302); self.assertEqual(AvailabilityRequest.objects.get().staff_id,self.person.pk)
    def test_invalid_and_empty_requested_dates_are_rejected(self):
        for days in [['2030-08-01'],[]]:
            record,form=svc.request_availability(actor=self.employee,event_id=self.event.pk,data={'dates':days}); self.assertIsNone(record); self.assertIn('dates',form.errors)
    def test_draft_and_unpublished_events_are_not_staff_opportunities(self):
        self.staff_login(); self.event.status='draft'; self.event.save()
        self.assertNotContains(self.client.get('/staff/'),self.event.name); self.assertEqual(self.client.get(reverse('staffing:staff-event',args=[self.event.pk])).status_code,404)
        with self.assertRaises(ValidationError): self.request()
        self.event.status='upcoming'; self.event.save(); self.plan.is_published=False; self.plan.save()
        self.assertNotContains(self.client.get('/staff/'),self.event.name)
    def test_general_staff_has_no_participant_or_management_event_access(self):
        self.staff_login(); self.assertEqual(self.client.get(reverse('events:event_detail',args=[self.event.pk])).status_code,403)
        self.assertEqual(self.client.get('/participants/staff/directory/').status_code,403)
    def test_deadline_closes_new_requests_at_exact_deadline(self):
        self.plan.signup_deadline=timezone.now()-timedelta(seconds=1); self.plan.save()
        with self.assertRaises(ValidationError): self.request()
        self.staff_login(); self.assertContains(self.client.get(reverse('staffing:staff-event',args=[self.event.pk])),'Sign-ups closed')
    def test_request_before_deadline_is_allowed(self):
        self.plan.signup_deadline=timezone.now()+timedelta(hours=1); self.plan.save(); self.assertEqual(self.request().status,'pending')
    def test_deadline_form_converts_event_timezone_and_rejects_dst(self):
        form=PlanForm(self.plan_data(signup_deadline='2030-07-01T08:00'),event=self.event,plan=self.plan); self.assertTrue(form.is_valid(),form.errors); self.assertEqual(form.cleaned_data['signup_deadline'].astimezone(UTC).hour,12)
        for value in ['2030-03-10T02:30','2030-11-03T01:30','bad']:
            form=PlanForm(self.plan_data(signup_deadline=value),event=self.event,plan=self.plan); self.assertFalse(form.is_valid())
    def test_deadline_edit_and_removal_preserve_requests_and_assignments(self):
        r=self.request(); self.decision(r); a=StaffAssignment.objects.get(); original=a.pk
        p,f=svc.save_plan(actor=self.manager,event_id=self.event.pk,data=self.plan_data(signup_deadline='2026-01-01T10:00')); self.assertIsNotNone(p,f.errors); self.assertIsNotNone(p.signup_deadline)
        p,f=svc.save_plan(actor=self.manager,event_id=self.event.pk,data=self.plan_data(signup_deadline='')); self.assertIsNone(p.signup_deadline); self.assertEqual(StaffAssignment.objects.get().pk,original); r.refresh_from_db(); self.assertEqual(r.status,'approved')
    def test_manager_can_approve_existing_request_after_deadline(self):
        r=self.request(); self.plan.signup_deadline=timezone.now()-timedelta(days=1); self.plan.save(); self.decision(r); self.assertEqual(StaffAssignment.objects.get().status,'confirmed')
    def test_manager_can_assign_and_edit_after_deadline_without_request(self):
        self.plan.signup_deadline=timezone.now()-timedelta(days=1); self.plan.save(); a=self.assignment(); self.assertFalse(AvailabilityRequest.objects.exists())
        changed=self.assignment(old=a,days=['2030-07-02']); self.assertEqual(changed.days.get().date,date(2030,7,2)); self.assertFalse(AvailabilityRequest.objects.exists())
    def test_direct_assignment_is_immediately_confirmed(self):
        a=self.assignment(); self.assertEqual(a.status,'confirmed'); self.assertEqual(svc.coverage(self.event)['confirmed'],1)
    def test_duplicate_assignment_is_rejected(self):
        self.assignment()
        with self.assertRaises(ValidationError): self.assignment()
    def test_assignment_day_role_and_times_can_be_edited(self):
        a=self.assignment(); changed=self.assignment(old=a,days=['2030-07-02'],role='Driver',**{'start_2030-07-02':'10:00','end_2030-07-02':'16:00','arrival_2030-07-02':'09:30'})
        day=changed.days.get(); self.assertEqual(changed.role,'Driver'); self.assertEqual(day.starts_at.astimezone(self.event.local_start_at.tzinfo).hour,10); self.assertIsNotNone(day.arrival_at)
        a.refresh_from_db(); self.assertEqual(a.status,'replaced'); self.assertEqual(a.days.get().date,date(2030,7,1))
    def test_reassignment_preserves_original_availability_and_revision(self):
        r=self.request(); self.decision(r); a=StaffAssignment.objects.get(); changed=self.assignment(old=a,person=self.other_person,days=['2030-07-02'])
        r.refresh_from_db(); a.refresh_from_db(); self.assertEqual(r.staff_id,self.person.pk); self.assertEqual(r.requested_dates,['2030-07-01','2030-07-02']); self.assertEqual(r.status,'approved'); self.assertEqual(a.status,'replaced'); self.assertEqual(changed.staff_id,self.other_person.pk); self.assertEqual(changed.source_request_id,r.pk)
    def test_removal_preserves_approved_request_and_daily_history(self):
        r=self.request(); self.decision(r); a=StaffAssignment.objects.get()
        obj,_=svc.remove_assignment(actor=self.manager,assignment_id=a.pk,data=dict(revision=a.updated_at.isoformat(),confirm='on',reason='SYNTHETIC removal'))
        self.assertEqual(obj.status,'removed'); self.assertEqual(obj.days.count(),2); r.refresh_from_db(); self.assertEqual(r.status,'approved'); self.assertEqual(svc.coverage(self.event)['confirmed'],0)
    def test_stale_assignment_edit_cannot_overwrite_current_revision(self):
        a=self.assignment(); self.assignment(old=a,days=['2030-07-02'])
        with self.assertRaises(ValidationError): self.assignment(old=a)
        self.assertEqual(StaffAssignment.objects.filter(status='confirmed').count(),1)
    def test_assignment_cannot_use_out_of_calendar_dates_or_no_days(self):
        data=dict(revision='new',staff=str(self.person.pk),reason='SYNTHETIC',**{'day_2030-08-01':'on'})
        form=AssignmentForm(data,event=self.event); self.assertFalse(form.is_valid()); self.assertIn('Select at least one',str(form.errors))
    def test_working_time_conflicts_and_arrival_order_rejected(self):
        for extra in [{'start_2030-07-01':'11:00'},{'start_2030-07-01':'16:00','end_2030-07-01':'10:00'},{'start_2030-07-01':'08:00','end_2030-07-01':'16:00'},{'arrival_2030-07-01':'10:00'}]:
            form=AssignmentForm(dict(revision='new',staff=str(self.person.pk),reason='SYNTHETIC',**{'day_2030-07-01':'on'},**extra),event=self.event); self.assertFalse(form.is_valid(),extra)
    def test_cross_event_overlapping_assignment_rejected_atomically(self):
        self.assignment(); other=Event.objects.create(name='SYNTHETIC conflicting event',start_at=self.event.start_at,end_at=self.event.end_at,status='upcoming',timezone_name=self.event.timezone_name,staff_published_at=timezone.now())
        with self.assertRaises(ValidationError): self.assignment(event=other)
        self.assertEqual(StaffAssignment.objects.count(),1)
    def test_non_overlapping_assignments_are_allowed(self):
        self.assignment(**{'start_2030-07-01':'09:00','end_2030-07-01':'12:00'})
        other=Event.objects.create(name='SYNTHETIC later event',start_at=datetime(2030,7,1,16,tzinfo=UTC),end_at=datetime(2030,7,1,21,tzinfo=UTC),status='upcoming',timezone_name='America/New_York',staff_published_at=timezone.now())
        self.assignment(event=other,**{'start_2030-07-01':'12:00','end_2030-07-01':'17:00'}); self.assertEqual(StaffAssignment.objects.filter(status='confirmed').count(),2)
    def test_approval_conflict_rolls_back_request_decision(self):
        r=self.request(); self.assignment()
        with self.assertRaises(ValidationError): self.decision(r)
        r.refresh_from_db(); self.assertEqual(r.status,'pending'); self.assertIsNone(r.decided_at)
    def test_pending_requests_do_not_count_as_confirmed_coverage(self):
        self.request(); summary=svc.coverage(self.event); self.assertEqual(summary['confirmed'],0); self.assertEqual(summary['pending'],1); self.assertEqual(summary['unfilled'],6)
        self.assignment(person=self.other_person); summary=svc.coverage(self.event); self.assertEqual(summary['confirmed'],1); self.assertEqual(summary['unfilled'],5)
    def test_daily_requirements_update_counts(self):
        data=self.plan_data(); data['need_2030-07-01']=4; data['need_2030-06-30']=0
        svc.save_plan(actor=self.manager,event_id=self.event.pk,data=data); summary=svc.coverage(self.event); self.assertEqual([r['required'] for r in summary['days']],[0,4,2])
    def test_inactive_staff_cannot_request_or_receive_new_assignments(self):
        self.person.is_active=False; self.person.save()
        with self.assertRaises(PermissionDenied): self.request()
        form=AssignmentForm(dict(revision='new',staff=str(self.person.pk),reason='SYNTHETIC',**{'day_2030-07-01':'on'}),event=self.event); self.assertFalse(form.is_valid())
    def test_general_staff_cannot_manage_direct_urls(self):
        r=self.request(); a=self.assignment(person=self.other_person); self.staff_login()
        urls=[reverse('staffing:event',args=[self.event.pk]),reverse('staffing:plan-edit',args=[self.event.pk]),reverse('staffing:assignment-add',args=[self.event.pk]),reverse('staffing:assignment-edit',args=[self.event.pk,a.pk]),reverse('staffing:assignment-remove',args=[a.pk]),reverse('staffing:request-decision',args=[r.pk,'approve']),'/staff/requests/']
        for url in urls: self.assertEqual(self.client.post(url,{}).status_code,403,url)
    def test_staff_view_shows_own_confirmed_assignment_and_no_private_notes(self):
        a=self.assignment(); self.staff_login(); r=self.client.get(reverse('staffing:staff-event',args=[self.event.pk])); self.assertContains(r,'ASSIGNED HOUSING 9C'); self.assertContains(r,'My confirmed assignment'); self.assertNotContains(r,'PRIVATE MANAGER NOTE'); self.assertNotContains(r,'OTHER PRIVATE STAFF NOTE')
        self.assertContains(self.client.get('/staff/',{'view':'assignments'}),self.event.name)
    def test_unassigned_staff_only_gets_published_summary(self):
        self.staff_login(); r=self.client.get(reverse('staffing:staff-event',args=[self.event.pk])); self.assertContains(r,'I Can Work This Event'); self.assertNotContains(r,'ASSIGNED HOUSING 9C'); self.assertNotContains(r,'ASSIGNED LOGISTICS 9C'); self.assertNotContains(r,'MANAGER DESCRIPTION')
    def test_staff_cannot_see_other_requests_or_assignments(self):
        self.request(self.other_person); self.assignment(person=self.other_person); self.staff_login()
        r=self.client.get('/staff/',{'view':'requests'}); self.assertNotContains(r,'SYNTHETIC offered dates')
        r=self.client.get('/staff/',{'view':'assignments'}); self.assertNotContains(r,'Staff lead')
    def test_removed_assignment_is_clearly_not_a_confirmed_schedule(self):
        r=self.request(); self.decision(r); a=StaffAssignment.objects.get(); svc.remove_assignment(actor=self.manager,assignment_id=a.pk,data=dict(revision=a.updated_at.isoformat(),confirm='on',reason='SYNTHETIC removal')); self.staff_login()
        r=self.client.get(reverse('staffing:staff-event',args=[self.event.pk])); self.assertContains(r,'no longer have an active assignment'); self.assertNotContains(r,'ASSIGNED HOUSING 9C')
    def test_event_date_edit_cannot_strand_confirmed_staffing(self):
        self.assignment(days=['2030-07-02']); event=self.event; data={k:('' if v is None else v) for k,v in EventForm(instance=event).initial.items()}; data.update(action='save',revision=event.updated_at.isoformat(),end_at='2030-07-01T17:00')
        saved,form=save_event(actor=self.admin,event_id=event.pk,data=data); self.assertIsNone(saved); self.assertIn('confirmed',str(form.errors)); event.refresh_from_db(); self.assertEqual(event.local_end_at.date(),date(2030,7,2))
    def test_unchanged_event_edit_preserves_assignment(self):
        a=self.assignment(); event=self.event; data={k:('' if v is None else v) for k,v in EventForm(instance=event).initial.items()}; data.update(action='save',revision=event.updated_at.isoformat()); saved,form=save_event(actor=self.admin,event_id=event.pk,data=data); self.assertIsNotNone(saved,form.errors); a.refresh_from_db(); self.assertEqual(a.status,'confirmed')
    def test_lifecycle_closes_new_staffing_without_deleting_history(self):
        a=self.assignment(); self.event.status='cancelled'; self.event.save()
        with self.assertRaises(ValidationError): self.assignment(person=self.other_person)
        a.refresh_from_db(); self.assertEqual(a.status,'confirmed'); self.assertEqual(a.days.count(),1)
    def test_audit_history_covers_requests_decisions_revisions_and_removal(self):
        r=self.request(); self.decision(r); a=StaffAssignment.objects.get(); b=self.assignment(old=a,days=['2030-07-02']); svc.remove_assignment(actor=self.manager,assignment_id=b.pk,data=dict(revision=b.updated_at.isoformat(),confirm='on',reason='SYNTHETIC removed'))
        for action in ['request.created','request.approved','assignment.confirmed','assignment.revised','assignment.removed']: self.assertTrue(AuditLog.objects.filter(action='staffing.'+action).exists(),action)
    def test_anonymous_public_and_inactive_accounts_denied(self):
        self.client.logout(); self.assertEqual(self.client.get('/staff/').status_code,302)
        self.client.force_login(self.public,backend='django.contrib.auth.backends.ModelBackend'); self.assertEqual(self.client.get('/staff/').status_code,403)
    def test_staffing_post_enforces_csrf(self):
        c=Client(enforce_csrf_checks=True); c.force_login(self.manager,backend='django.contrib.auth.backends.ModelBackend'); self.assertEqual(c.post(reverse('staffing:plan-edit',args=[self.event.pk]),self.plan_data()).status_code,403)
    def test_manager_pages_and_all_forms_render(self):
        r=self.request(); a=self.assignment(person=self.other_person)
        urls=['/staff/directory/',reverse('staffing:profile',args=[self.person.pk]),reverse('staffing:profile-edit',args=[self.person.pk]),reverse('staffing:account-link',args=[self.person.pk]),reverse('staffing:event',args=[self.event.pk]),reverse('staffing:plan-edit',args=[self.event.pk]),reverse('staffing:assignment-add',args=[self.event.pk]),reverse('staffing:assignment-edit',args=[self.event.pk,a.pk]),reverse('staffing:request-decision',args=[r.pk,'approve']),reverse('staffing:assignment-remove',args=[a.pk]),'/staff/requests/']
        for url in urls: self.assertEqual(self.client.get(url).status_code,200,url)
    def test_staff_mobile_schedule_and_event_forms_render(self):
        self.staff_login()
        for view in ['available','requests','assignments','profile']: self.assertEqual(self.client.get('/staff/',{'view':view}).status_code,200)
        self.assertContains(self.client.get(reverse('staffing:staff-event',args=[self.event.pk])),'csrfmiddlewaretoken')
    def test_navigation_discovery_preserves_six_item_shell_and_gear(self):
        self.staff_login(); r=self.client.get('/internal/'); self.assertContains(r,'EVENTS'); self.assertContains(r,'Personal settings'); self.assertContains(self.client.get('/internal/workspaces/events/'),'My staff schedule')
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend'); r=self.client.get('/internal/workspaces/operations/'); self.assertContains(r,'Staff directory'); self.assertContains(r,'Staff availability requests')
    def test_staff_data_not_added_to_offline_registration_contract(self):
        from apps.events.downsync import EVENT_FIELDS
        for field in ['signup_deadline','staff_assignments','staff_availability','staff_profiles']: self.assertNotIn(field,EVENT_FIELDS)

    def test_exact_deadline_boundary_is_closed(self):
        instant=datetime(2028,1,1,tzinfo=UTC); self.plan.signup_deadline=instant; self.plan.save()
        with patch('apps.staffing.services.timezone.now',return_value=instant):
            self.assertFalse(svc.opportunity_open(self.event,self.plan))
            with self.assertRaises(ValidationError): self.request()
    def test_failed_reassignment_conflict_preserves_original_assignment(self):
        old=self.assignment(); other_event=Event.objects.create(name='SYNTHETIC conflicting reassignment',start_at=self.event.start_at,end_at=self.event.end_at,status='upcoming',timezone_name=self.event.timezone_name,staff_published_at=timezone.now())
        self.assignment(person=self.other_person,event=other_event)
        with self.assertRaises(ValidationError): self.assignment(old=old,person=self.other_person)
        old.refresh_from_db(); self.assertEqual(old.status,'confirmed'); self.assertFalse(StaffAssignment.objects.filter(supersedes=old).exists())
    def test_arrival_conflict_is_checked_even_when_working_times_do_not_overlap(self):
        self.assignment(**{'start_2030-07-01':'09:00','end_2030-07-01':'12:00'})
        later=Event.objects.create(name='SYNTHETIC arrival conflict',start_at=datetime(2030,7,1,16,tzinfo=UTC),end_at=datetime(2030,7,1,21,tzinfo=UTC),status='upcoming',timezone_name='America/New_York',staff_published_at=timezone.now())
        with self.assertRaises(ValidationError): self.assignment(event=later,**{'start_2030-07-01':'12:00','end_2030-07-01':'17:00','arrival_2030-07-01':'11:00'})
    def test_old_direct_assignment_history_still_has_a_safe_event_summary(self):
        a=self.assignment(); svc.remove_assignment(actor=self.manager,assignment_id=a.pk,data=dict(revision=a.updated_at.isoformat(),confirm='on',reason='SYNTHETIC removal')); self.plan.is_published=False; self.plan.save(); self.staff_login()
        r=self.client.get(reverse('staffing:staff-event',args=[self.event.pk])); self.assertEqual(r.status_code,200); self.assertNotContains(r,'ASSIGNED HOUSING 9C')
    def test_terminal_event_direct_management_url_returns_conflict_not_error(self):
        self.event.status='completed'; self.event.save(); response=self.client.get(reverse('staffing:plan-edit',args=[self.event.pk])); self.assertEqual(response.status_code,409)
    def test_admin_calendar_is_protected_when_confirmed_staff_exist(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from apps.events.admin import EventAdmin
        self.assignment(); request=RequestFactory().get('/admin/'); request.user=self.admin; admin=EventAdmin(Event,AdminSite())
        for name in ['start_at','end_at','timezone_name','setup_date']: self.assertIn(name,admin.get_readonly_fields(request,self.event))
        self.event.end_at=datetime(2030,6,30,21,tzinfo=UTC)
        with self.assertRaises(ValidationError): admin.save_model(request,self.event,None,True)
    def test_event_assignment_pagination_exposes_older_confirmed_records(self):
        for i in range(22):
            p=StaffProfile.objects.create(first_name='SYNTHETIC',last_name=f'Page {i:02d}'); self.assignment(person=p)
        response=self.client.get(reverse('staffing:event',args=[self.event.pk]),{'page':2}); self.assertEqual(len(response.context['assignments']),2); self.assertContains(response,'Previous')
    def test_staff_and_approved_request_are_not_deleted_by_repeated_removal(self):
        r=self.request(); self.decision(r); a=StaffAssignment.objects.get(); data=dict(revision=a.updated_at.isoformat(),confirm='on',reason='SYNTHETIC removal')
        svc.remove_assignment(actor=self.manager,assignment_id=a.pk,data=data)
        with self.assertRaises(ValidationError): svc.remove_assignment(actor=self.manager,assignment_id=a.pk,data=data)
        self.assertTrue(StaffProfile.objects.filter(pk=self.person.pk).exists()); r.refresh_from_db(); self.assertEqual(r.status,'approved')

    def test_available_event_schedule_displays_event_timezone(self):
        self.staff_login(); response=self.client.get('/staff/')
        self.assertContains(response,'9:00 AM'); self.assertContains(response,'5:00 PM'); self.assertNotContains(response,'1:00 PM')

    def test_legacy_upcoming_event_can_publish_opportunity_without_new_metadata(self):
        self.event.staff_published_at=None; self.event.save()
        record=self.request(); self.assertEqual(record.status,'pending')
        self.staff_login(); self.assertContains(self.client.get('/staff/'),self.event.name)
    def test_legacy_upcoming_event_supports_direct_assignment(self):
        self.event.staff_published_at=None; self.event.save(); assignment=self.assignment()
        self.assertEqual(assignment.status,'confirmed'); self.event.refresh_from_db(); self.assertIsNone(self.event.staff_published_at)
