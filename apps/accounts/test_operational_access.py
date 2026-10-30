"""Synthetic role matrix and direct-URL/forged-write security regression tests."""
import uuid
from datetime import timedelta,date
from zoneinfo import ZoneInfo
from unittest.mock import patch
from django.test import TestCase,Client
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group,Permission
from django.core.exceptions import PermissionDenied,ValidationError
from django.urls import reverse
from django.utils import timezone
from apps.events.models import Event
from apps.staffing.models import StaffProfile,StaffingPlan,StaffAssignment,AssignmentDay
from apps.staffing import services as staffing
from apps.staffing.calendar import day_window
from apps.workdays import services as workdays
from apps.workdays.models import StaffRole,Workday
from apps.event_reports import services as finance
from apps.participants.models import Participant
from apps.participants.workspace_models import ParticipantNote
from apps.participants.workspace_services import save_note,archive_record
from apps.participants.workspace_access import can_edit_note
from apps.participants.intake_admin_guards import full_dob_allowed
from apps.core.models import AuditLog
from apps.internal_ui.navigation import staff_destinations
from . import test_staff_access as foundation
from .roles import ROLE_GROUPS,has_role
U=get_user_model()
class OperationalRoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        foundation.StaffFoundationTests.setUpTestData.__func__(cls)
        now=timezone.now()
        cls.checkin_at=now  # Keep assignment and check-in on the same event-local day.
        cls.event=Event.objects.create(name='SYNTHETIC general event',start_at=now-timedelta(hours=1),end_at=now+timedelta(hours=5),status='upcoming',staff_published_at=now,contact_name='PRIVATE_CONTACT_MARKER',housing_information='PRIVATE_HOUSING_MARKER',logistics_instructions='PRIVATE_OPERATIONS_MARKER')
        cls.plan=StaffingPlan.objects.create(event=cls.event,is_published=True,required_staff=2)
        cls.profile=StaffProfile.objects.get(user=cls.worker)
        cls.person=Participant.objects.create(first_name='SYNTHETIC',last_name='Recruiting',date_of_birth=date(2000,1,2),contact_email='participant@example.test',city='Athens')
        cls.pay_role=StaffRole.objects.create(name='SYNTHETIC staff role')
        cls.other_recruiter=U.objects.create_user('synthetic-second-recruiter',is_staff=True)
        cls.other_recruiter.groups.add(Group.objects.get(name=ROLE_GROUPS['recruiter']))
        cls.note=ParticipantNote.objects.create(participant=cls.person,author=cls.recruiter,content='SYNTHETIC shared recruiting note')
    def login_as(self,user):
        self.client.force_login(user,backend='django.contrib.auth.backends.ModelBackend')
    def assignment(self,user=None,coverage_role='staff'):
        profile=StaffProfile.objects.get(user=user or self.worker)
        row=StaffAssignment.objects.create(event=self.event,staff=profile,coverage_role=coverage_role)
        AssignmentDay.objects.create(assignment=row,**day_window(self.event,timezone.localdate(self.checkin_at,ZoneInfo(self.event.timezone_name))))
        return row
    def check(self,user):
        with patch('apps.workdays.services.timezone.now',return_value=self.checkin_at):
            return workdays.checkin(actor=user,event_id=self.event.pk,data={'work_date':timezone.localdate(self.checkin_at,ZoneInfo(self.event.timezone_name)).isoformat(),'role':str(self.pay_role.pk),'drove':''})
    def test_published_history_includes_cancelled_and_archived(self):
        self.login_as(self.worker)
        for status in ('upcoming','completed','cancelled','archived'):
            self.event.status=status;self.event.save()
            response=self.client.get(reverse('staffing:general-event',args=[self.event.pk]))
            self.assertEqual(response.status_code,200)
            self.assertContains(response,self.event.name)
    def test_drafts_and_never_published_history_are_private(self):
        self.login_as(self.worker)
        self.event.status='draft';self.event.save()
        self.assertEqual(self.client.get(reverse('staffing:general-event',args=[self.event.pk])).status_code,404)
        self.event.status='completed';self.event.staff_published_at=None;self.event.save()
        self.assertEqual(self.client.get(reverse('staffing:general-event',args=[self.event.pk])).status_code,404)
    def test_general_projection_omits_private_event_fields_even_when_assigned(self):
        self.assignment();self.login_as(self.worker)
        r=self.client.get(reverse('staffing:general-event',args=[self.event.pk]))
        self.assertEqual(r.status_code,200)
        for marker in ('PRIVATE_CONTACT_MARKER','PRIVATE_HOUSING_MARKER','PRIVATE_OPERATIONS_MARKER'):self.assertNotContains(r,marker)
        self.assertNotIn('event',r.context)
        self.assertEqual(self.client.get(reverse('staffing:staff-event',args=[self.event.pk])).status_code,302)
    def test_event_staff_cannot_read_or_edit_event_workspace(self):
        self.login_as(self.worker)
        for route in ('events:event_detail','staffing:event','staffing:plan-edit','staffing:assignment-add'):
            url=reverse(route,args=[self.event.pk])
            for method in ('get','post'):
                self.assertIn(getattr(self.client,method)(url,{}).status_code,(403,404))
    def test_assigned_worker_cannot_access_private_operational_notes_documents_or_execution(self):
        from apps.operations.services import event_access
        from apps.operations.execution_services import can_view
        self.assignment()
        for kind in ('event_notes','event_documents'):
            with self.assertRaises(PermissionDenied):event_access(self.worker,self.event,kind)
        self.assertFalse(can_view(self.worker,self.event))
    def test_request_does_not_assign_and_manager_approval_does(self):
        row,form=staffing.request_availability(actor=self.worker,event_id=self.event.pk,data={'dates':[self.event.local_start_at.date().isoformat()],'note':'SYNTHETIC willing to work','staff':str(StaffProfile.objects.get(user=self.recruiter).pk)})
        self.assertIsNotNone(row,form.errors);self.assertEqual(row.staff_id,self.profile.pk)
        self.assertEqual(row.status,'pending');self.assertFalse(StaffAssignment.objects.filter(staff=self.profile).exists())
        result,form=staffing.decide_request(actor=self.manager,request_id=row.pk,decision='approve',data={'revision':row.updated_at.isoformat(),'confirm':True,'reason':'SYNTHETIC approval','coverage_role':'staff'})
        self.assertIsNotNone(result,form.errors);self.assertTrue(StaffAssignment.objects.filter(staff=self.profile,status='confirmed').exists())
    def test_worker_cannot_approve_own_request(self):
        row,_=staffing.request_availability(actor=self.worker,event_id=self.event.pk,data={'dates':[self.event.local_start_at.date().isoformat()]})
        with self.assertRaises(PermissionDenied):staffing.decide_request(actor=self.worker,request_id=row.pk,decision='approve',data={})
    def test_worker_only_self_checkin_and_own_assignment(self):
        self.assignment();row,form=self.check(self.worker)
        self.assertIsNotNone(row,form.errors);self.assertEqual(row.staff_id,self.profile.pk)
        self.assertEqual(row.created_by_id,self.worker.pk)
    def test_manager_and_administrator_without_event_staff_cannot_self_checkin(self):
        for user in (self.manager,self.admin):
            with self.subTest(user=user.username),self.assertRaises(PermissionDenied):self.check(user)
    def test_dual_manager_event_staff_requires_own_assignment(self):
        self.manager.groups.add(Group.objects.get(name=ROLE_GROUPS['event_staff']))
        with self.assertRaises(PermissionDenied):self.check(self.manager)
        self.assignment(self.manager);row,form=self.check(self.manager);self.assertIsNotNone(row,form.errors)
    def test_event_positions_never_grant_platform_role(self):
        for role in ('staff_lead','driver','combined'):
            row=self.assignment(self.recruiter,role)
            self.assertFalse(has_role(self.recruiter,'event_staff'))
            with self.assertRaises(PermissionDenied):self.check(self.recruiter)
            row.status='removed';row.save()
    def test_event_staff_has_no_participant_access_even_through_task_or_event(self):
        self.assignment();self.login_as(self.worker)
        self.assertEqual(self.client.get(reverse('participants:staff-profile',args=[self.person.pk])).status_code,403)
        self.assertEqual(self.client.get(reverse('participants:staff-directory')).status_code,403)
    def test_recruiter_general_profile_without_sensitive_sections(self):
        self.login_as(self.recruiter)
        r=self.client.get(reverse('participants:staff-profile',args=[self.person.pk]));self.assertEqual(r.status_code,200)
        self.assertContains(r,'participant@example.test');self.assertContains(r,'Athens');self.assertNotContains(r,'Jan. 2, 2000')
        self.assertFalse(full_dob_allowed(self.recruiter))
        for section in ('documents','assessments','events','acquisition','activity'):
            self.assertEqual(self.client.get(reverse('participants:staff-profile',args=[self.person.pk]),{'section':section}).status_code,403)
    def test_recruiter_can_read_shared_notes_but_edit_only_own(self):
        self.assertTrue(can_edit_note(self.recruiter,self.note));self.assertFalse(can_edit_note(self.other_recruiter,self.note))
        self.login_as(self.other_recruiter)
        self.assertContains(self.client.get(reverse('participants:staff-profile',args=[self.person.pk]),{'section':'notes'}),'SYNTHETIC shared recruiting note')
        with self.assertRaises(PermissionDenied):save_note(actor=self.other_recruiter,participant_id=self.person.pk,object_id=self.note.pk,data={'content':'forged','revision':self.note.updated_at.isoformat(),'audit_reason':'forged'})
    def test_manager_cross_author_note_edit_preserves_authorship_and_audit(self):
        saved=save_note(actor=self.manager,participant_id=self.person.pk,object_id=self.note.pk,data={'content':'SYNTHETIC manager correction','revision':self.note.updated_at.isoformat(),'audit_reason':'SYNTHETIC reviewed correction'})
        self.assertEqual(saved.author_id,self.recruiter.pk);self.assertEqual(saved.edited_by_id,self.manager.pk)
        self.assertTrue(AuditLog.objects.filter(action='participant.workspace.note.edited',actor=self.manager).exists())
    def test_note_archive_restore_and_stale_revision_are_guarded(self):
        for restore in (False,True):
            with self.assertRaises(PermissionDenied):archive_record(actor=self.other_recruiter,participant_id=self.person.pk,kind='note',object_id=self.note.pk,revision=self.note.updated_at.isoformat(),reason='forged',restore=restore)
        with self.assertRaises(ValidationError):save_note(actor=self.recruiter,participant_id=self.person.pk,object_id=self.note.pk,data={'content':'stale','revision':'stale','audit_reason':'SYNTHETIC'})
    def test_dual_recruiter_worker_retains_general_people_access_after_event_ends(self):
        self.worker.groups.add(Group.objects.get(name=ROLE_GROUPS['recruiter']))
        self.event.status='archived';self.event.save();self.login_as(self.worker)
        self.assertEqual(self.client.get(reverse('participants:staff-profile',args=[self.person.pk])).status_code,200)
        self.assertFalse(full_dob_allowed(self.worker))
    def test_manager_operational_finance_but_not_protected_configuration(self):
        self.assertTrue(workdays.manager(self.manager));self.assertTrue(finance.can_manage(self.manager))
        self.assertFalse(workdays.administrator(self.manager));self.assertFalse(finance.can_configure(self.manager))
        self.manager.user_permissions.add(Permission.objects.get(codename='manage_compensation'),Permission.objects.get(codename='manage_expense_categories'))
        fresh=U.objects.get(pk=self.manager.pk)
        self.assertFalse(workdays.administrator(fresh));self.assertFalse(finance.can_configure(fresh))
        self.assertTrue(workdays.administrator(self.admin));self.assertTrue(finance.can_configure(self.admin))
    def test_recruiter_worker_have_no_operational_finance(self):
        for user in (self.recruiter,self.worker):
            self.assertFalse(workdays.manager(user));self.assertFalse(finance.can_report(user));self.assertFalse(finance.can_manage(user))
    def test_ordinary_roles_cannot_use_native_admin_to_bypass_privacy(self):
        for user in (self.recruiter,self.worker):
            self.login_as(user)
            for method in ('get','post'):
                self.assertEqual(getattr(self.client,method)(reverse('admin:participants_participant_change',args=[self.person.pk]),{}).status_code,403)
    def test_protected_financial_admin_is_denied_to_manager(self):
        self.login_as(self.manager)
        for route in ('workdays:settings','event_reports:categories'):
            self.assertEqual(self.client.get(reverse(route)).status_code,403)
    def test_navigation_is_additive_and_permission_aware(self):
        ids=lambda u:{r['id']:r['url'] for r in staff_destinations(u)}
        w=ids(self.worker);r=ids(self.recruiter)
        self.assertEqual(w['events'],reverse('staffing:general-schedule'));self.assertNotIn('participants',w);self.assertNotIn('operations',w);self.assertNotIn('reports',w)
        self.assertIn('participants',r);self.assertNotIn('operations',r)
    def test_personal_workday_isolation(self):
        self.assignment();row,_=self.check(self.worker)
        self.login_as(self.recruiter)
        self.assertNotContains(self.client.get(reverse('workdays:mine')),self.event.name)
        self.assertEqual(self.client.get(reverse('workdays:record',args=[row.pk])).status_code,403)
    def test_legacy_direct_permissions_preserved_without_role_enrollment(self):
        user=U.objects.create_user('synthetic-legacy-rates',is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='manage_compensation'))
        self.assertTrue(workdays.administrator(user));self.assertFalse(has_role(user,'manager'))
