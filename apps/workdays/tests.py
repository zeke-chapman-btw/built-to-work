"""Synthetic acceptance and integrity tests for Build 9E."""
import csv
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError,transaction
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.staffing.models import StaffProfile, StaffAssignment, AssignmentDay
from apps.staffing.calendar import day_window
from .models import StaffRole, CompensationRate, EventMileage, MileageTrip, Workday
from . import services as svc,reports

@override_settings(DEBUG=True)
class Workday9ETests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.manager=U.objects.create_user('synthetic-9e-manager',password='test-password',is_staff=True)
        cls.admin=U.objects.create_user('synthetic-9e-admin',password='test-password',is_staff=True)
        cls.employee=U.objects.create_user('synthetic-9e-employee',password='test-password',is_staff=True)
        cls.other=U.objects.create_user('synthetic-9e-other',password='test-password',is_staff=True)
        cls.public=U.objects.create_user('synthetic-9e-public',password='test-password')
        cls.manager.user_permissions.add(Permission.objects.get(content_type__app_label='workdays',codename='manage_workdays'),Permission.objects.get(content_type__app_label='events',codename='view_event'))
        cls.admin.user_permissions.add(*Permission.objects.filter(content_type__app_label='workdays',codename__in=['manage_workdays','manage_compensation']),Permission.objects.get(content_type__app_label='events',codename='view_event'))
        from apps.accounts.roles import role_configuration,ROLE_GROUPS
        from django.contrib.auth.models import Group
        role_configuration(apply=True)
        cls.employee.groups.add(Group.objects.get(name=ROLE_GROUPS['event_staff']))
        cls.staff=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='9E Employee',user=cls.employee)
        cls.second=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='9E Other',user=cls.other)
        cls.event=Event.objects.create(name='SYNTHETIC 9E event',start_at=datetime(2030,7,1,13,tzinfo=UTC),end_at=datetime(2030,7,3,21,tzinfo=UTC),status='upcoming',staff_published_at=timezone.now(),timezone_name='America/New_York',address_line_1='SYNTHETIC 123 Test Street',city='Athens',state='GA')
        cls.role=StaffRole.objects.create(name='SYNTHETIC daily role',per_diem_eligible=True)
        cls.unknown=StaffRole.objects.create(name='SYNTHETIC unconfigured role')
        cls.rate=CompensationRate.objects.create(kind='role',role=cls.role,amount=100,effective_from=date(2030,1,1),reason='SYNTHETIC daily rate')
        CompensationRate.objects.create(kind='per_diem',amount=20,effective_from=date(2030,1,1),reason='SYNTHETIC per diem')
        cls.mile=CompensationRate.objects.create(kind='mileage',amount='.5',effective_from=date(2030,1,1),reason='SYNTHETIC mileage only')
        a=StaffAssignment.objects.create(event=cls.event,staff=cls.staff)
        for i in range(3):AssignmentDay.objects.create(assignment=a,**day_window(cls.event,date(2030,7,1)+timedelta(days=i)))
    def setUp(self):
        self.client.force_login(self.manager,backend='django.contrib.auth.backends.ModelBackend')
    def check(self,day=1,actor=None,role=None,drove=True):
        with patch('apps.workdays.services.timezone.now',return_value=datetime(2030,7,day,14,tzinfo=UTC)):
            obj,form=svc.checkin(actor=actor or self.employee,event_id=self.event.pk,data={'work_date':f'2030-07-{day:02d}','role':str((role or self.role).pk),'drove':'on' if drove else ''})
        self.assertIsNotNone(obj,form.errors);return obj
    def manual(self,old=None,**changes):
        data=dict(staff=str(self.staff.pk),work_date='2030-07-01',role=str(self.role.pk),drove='',effective_checkin_at='2030-07-01T09:00',per_diem='eligible',reason='SYNTHETIC manager correction',revision=old.updated_at.isoformat() if old else 'new')
        data.update(changes)
        obj,form=svc.save_workday(actor=self.manager,event_id=self.event.pk,record_id=old.pk if old else None,data=data)
        self.assertIsNotNone(obj,form.errors);return obj
    def action(self,obj,action='approve'):
        result,form=svc.workday_action(actor=self.manager,record_id=obj.pk,action=action,data={'revision':obj.updated_at.isoformat(),'reason':'SYNTHETIC review'})
        self.assertIsNotNone(result,form.errors);return result
    def distance(self,value='100'):
        old=EventMileage.objects.filter(event=self.event).first()
        obj,form=svc.save_distance(actor=self.manager,event_id=self.event.pk,data=dict(revision=old.updated_at.isoformat() if old else 'new',reason='SYNTHETIC verified driving distance',one_way_miles=value,confirm='on'))
        self.assertIsNotNone(obj,form.errors);return obj
    def trip(self,staff=None,additional=False,**changes):
        data=dict(staff=str((staff or self.staff).pk),travel_start='',travel_end='',additional='on' if additional else '',revision='new',reason='SYNTHETIC manager trip');data.update(changes)
        obj,form=svc.save_trip(actor=self.manager,event_id=self.event.pk,data=data)
        self.assertIsNotNone(obj,form.errors);return obj
    def trip_action(self,obj,action='approve',actor=None):
        if action=='approve' and obj.status=='pending':obj=self.trip_action(obj,'verify',actor)
        result,form=svc.trip_action(actor=actor or self.manager,trip_id=obj.pk,action=action,data={'revision':obj.updated_at.isoformat(),'reason':'SYNTHETIC trip review'})
        self.assertIsNotNone(result,form.errors);return result
    def test_staff_source_timestamp_role_and_driver_independence(self):
        obj=self.check(drove=False);self.assertEqual(obj.source,'staff');self.assertEqual(obj.submitted_at,obj.effective_checkin_at);self.assertEqual(obj.role_name,self.role.name);self.assertIsNone(obj.trip_id)
    def test_duplicate_staff_checkin(self):
        self.check()
        with self.assertRaises(ValidationError):self.check()
    def test_database_duplicate_constraint(self):
        obj=self.check();obj.pk=None
        with self.assertRaises(IntegrityError),transaction.atomic():obj.save()
    def test_staff_can_choose_different_roles_on_different_days(self):
        first=self.check();second=self.check(day=2,role=self.unknown);self.assertNotEqual(first.role_id,second.role_id);self.assertEqual(second.per_diem,'review')
    def test_multiple_driving_days_share_one_pending_trip(self):
        first=self.check();second=self.check(day=2);third=self.check(day=3)
        self.assertEqual(first.trip_id,second.trip_id);self.assertEqual(first.trip_id,third.trip_id);self.assertEqual(MileageTrip.objects.count(),1);self.assertEqual(first.trip.status,'pending');self.assertIsNone(first.trip.amount)
    def test_driver_role_without_driving_creates_no_claim(self):
        driver=StaffRole.objects.get(name='Driver');self.check(role=driver,drove=False);self.assertFalse(MileageTrip.objects.exists())
    def test_unassigned_staff_denied(self):
        with self.assertRaises(PermissionDenied):self.check(actor=self.other)
    def test_inactive_staff_denied(self):
        self.staff.is_active=False;self.staff.save()
        with self.assertRaises(PermissionDenied):self.check()
    def test_inactive_role_not_offered(self):
        self.role.is_active=False;self.role.save()
        with patch('apps.workdays.services.timezone.now',return_value=datetime(2030,7,1,14,tzinfo=UTC)):
            obj,form=svc.checkin(actor=self.employee,event_id=self.event.pk,data={'work_date':'2030-07-01','role':str(self.role.pk)})
        self.assertIsNone(obj);self.assertIn('role',form.errors)
    def test_future_and_historical_staff_days_rejected(self):
        for day in ['2030-07-02','2030-06-30']:
            with self.subTest(day=day),patch('apps.workdays.services.timezone.now',return_value=datetime(2030,7,1,14,tzinfo=UTC)),self.assertRaises(ValidationError):svc.checkin(actor=self.employee,event_id=self.event.pk,data={'work_date':day,'role':str(self.role.pk)})
    def test_timezone_uses_event_date_not_utc(self):
        with patch('apps.workdays.services.timezone.now',return_value=datetime(2030,7,2,1,tzinfo=UTC)):
            obj,form=svc.checkin(actor=self.employee,event_id=self.event.pk,data={'work_date':'2030-07-01','role':str(self.role.pk)})
        self.assertIsNotNone(obj,form.errors);self.assertEqual(obj.work_date,date(2030,7,1));self.assertEqual(obj.submitted_at.date(),date(2030,7,2))
    def test_closed_event_staff_checkin_denied(self):
        for status in ['draft','cancelled','archived']:
            self.event.status=status;self.event.save()
            with self.subTest(status=status),self.assertRaises(ValidationError):self.check()
    def test_manager_entry_without_assignment(self):
        obj=self.manual(staff=str(self.second.pk));self.assertEqual(obj.source,'manager');self.assertIsNone(obj.submitted_at);self.assertFalse(StaffAssignment.objects.filter(staff=self.second).exists())
    def test_manager_corrects_staff_record_preserving_submission(self):
        obj=self.check();original=obj.submitted_at;obj=self.manual(old=obj,work_date='2030-07-02',effective_checkin_at='2030-07-02T10:00',per_diem='ineligible')
        self.assertEqual(obj.source,'staff');self.assertEqual(obj.submitted_at,original);self.assertEqual(obj.work_date,date(2030,7,2));self.assertEqual(obj.per_diem_amount,0)
    def test_stale_workday_update_denied(self):
        obj=self.manual();old=obj.updated_at.isoformat();obj=self.manual(old=obj)
        with self.assertRaises(ValidationError):self.manual(old=obj,revision=old)
    def test_void_retains_history_and_excludes_totals(self):
        obj=self.action(self.manual());self.assertEqual(reports.totals(Workday.objects.all(),MileageTrip.objects.all())['pay'],100)
        obj=self.action(obj,'void');self.assertFalse(obj.is_active);self.assertEqual(reports.totals(Workday.objects.all(),MileageTrip.objects.all())['pay'],0)
        self.assertTrue(AuditLog.objects.filter(action='workdays.workday.void',object_id=str(obj.pk)).exists())
    def test_voided_record_allows_replacement(self):
        self.action(self.manual(),'void');self.manual();self.assertEqual(Workday.objects.filter(is_active=True).count(),1);self.assertEqual(Workday.objects.count(),2)
    def test_approved_workday_requires_reopen_for_correction(self):
        obj=self.action(self.manual())
        with self.assertRaises(ValidationError):self.manual(old=obj)
        obj=self.action(obj,'reopen');obj=self.manual(old=obj,per_diem='ineligible');self.assertEqual(obj.per_diem_amount,0)
    def test_workday_snapshots_survive_global_rate_change(self):
        obj=self.action(self.manual());CompensationRate.objects.create(kind='role',role=self.role,amount=999,effective_from=date(2030,1,1),reason='SYNTHETIC later rate');obj.refresh_from_db();self.assertEqual(obj.pay_amount,100)
    def test_unknown_configuration_blocks_approval(self):
        obj=self.manual(role=str(self.unknown.pk),per_diem='review');self.assertIsNone(obj.pay_amount);self.assertIsNone(obj.per_diem_amount)
        with self.assertRaises(ValidationError):self.action(obj)
    def test_per_diem_edit_does_not_duplicate_daily_amount(self):
        obj=self.manual();obj=self.manual(old=obj);self.assertEqual(Workday.objects.count(),1);self.assertEqual(obj.per_diem_amount,20)
    def test_effective_checkin_must_match_local_work_date(self):
        with self.assertRaises(ValidationError):self.manual(effective_checkin_at='2030-07-03T09:00')
    def test_missing_distance_and_rate_block_claim_approval(self):
        obj=self.trip()
        with self.assertRaises(ValidationError):self.trip_action(obj)
        self.distance();self.mile.delete()
        with self.assertRaises(ValidationError):self.trip_action(obj)
    def test_round_trip_true_per_mile_calculation(self):
        self.distance('123.45');obj=self.trip_action(self.trip());self.assertEqual(obj.round_trip_miles,Decimal('246.90'));self.assertEqual(obj.amount,Decimal('123.45'))
    def test_multiple_independent_drivers(self):
        self.distance();self.trip_action(self.trip());self.trip_action(self.trip(staff=self.second));self.assertEqual(reports.totals(Workday.objects.all(),MileageTrip.objects.all())['mileage'],200)
    def test_explicit_additional_complete_round_trip(self):
        self.trip()
        with self.assertRaises(ValidationError):self.trip()
        obj=self.trip(additional=True);self.assertEqual(obj.number,2)
    def test_trip_dates_and_claim_date(self):
        obj=self.trip(travel_start='2030-06-30',travel_end='2030-07-04');self.assertEqual(obj.claim_date,date(2030,6,30))
    def test_reversed_trip_dates_invalid(self):
        obj,form=svc.save_trip(actor=self.manager,event_id=self.event.pk,data=dict(staff=str(self.staff.pk),travel_start='2030-07-04',travel_end='2030-06-30',revision='new',reason='SYNTHETIC'))
        self.assertIsNone(obj);self.assertIn('travel_end',form.errors)
    def test_approved_trip_preserves_distance_and_rate(self):
        self.distance();obj=self.trip_action(self.trip());CompensationRate.objects.create(kind='mileage',amount=9,effective_from=date(2030,1,1),reason='SYNTHETIC new mileage');self.distance('999');obj.refresh_from_db();self.assertEqual(obj.amount,100);self.assertEqual(obj.one_way_miles,100)
    def test_manager_cannot_approve_own_mileage(self):
        self.staff.user=self.manager;self.staff.save();self.distance();obj=self.trip()
        with self.assertRaises(PermissionDenied):self.trip_action(obj)
    def test_approved_trip_requires_reopen(self):
        self.distance();obj=self.trip_action(self.trip())
        with self.assertRaises(ValidationError):svc.save_trip(actor=self.manager,event_id=self.event.pk,trip_id=obj.pk,data={})
        obj=self.trip_action(obj,'reopen');self.assertEqual(obj.status,'pending')
    def test_void_trip_excluded_from_totals(self):
        self.distance();obj=self.trip_action(self.trip());self.trip_action(obj,'void');self.assertEqual(reports.totals(Workday.objects.all(),MileageTrip.objects.all())['mileage'],0)
    def test_new_event_location_requires_distance_reverification(self):
        self.distance();self.event.address_line_1='SYNTHETIC changed venue';self.event.save();obj=self.trip()
        with self.assertRaises(ValidationError):self.trip_action(obj)
    def test_negative_distance_and_missing_confirmation_invalid(self):
        for value,confirm in [('-1','on'),('100','')]:
            obj,form=svc.save_distance(actor=self.manager,event_id=self.event.pk,data=dict(revision='new',reason='SYNTHETIC',one_way_miles=value,confirm=confirm));self.assertIsNone(obj)

    def new_rate(self,**changes):
        data=dict(kind='mileage',role='',amount='.75',effective_from='2030-07-01',reason='SYNTHETIC approved rate',revision=self.mile.updated_at.isoformat());data.update(changes)
        return svc.save_rate(actor=self.admin,data=data)
    def test_admin_rate_settings_versions_and_effective_dates(self):
        old=Decimal(self.mile.amount);obj,form=self.new_rate();self.assertIsNotNone(obj,form.errors);self.assertEqual(svc.rate_for('mileage',date(2030,6,30)).amount,old);self.assertEqual(svc.rate_for('mileage',date(2030,7,1)).amount,Decimal('.75'));self.mile.refresh_from_db();self.assertEqual(self.mile.amount,old)
    def test_rate_change_stale_revision_rejected(self):
        self.new_rate()
        with self.assertRaises(ValidationError):self.new_rate()
    def test_negative_nan_and_invalid_rate_scope(self):
        for value in ['-1','NaN','Infinity']:
            obj,form=self.new_rate(amount=value);self.assertIsNone(obj)
        obj,form=self.new_rate(kind='role');self.assertIsNone(obj);self.assertIn('role',form.errors)
    def test_manager_cannot_change_global_compensation(self):
        with self.assertRaises(PermissionDenied):svc.save_rate(actor=self.manager,data={})
        with self.assertRaises(PermissionDenied):svc.save_role(actor=self.manager,data={})
    def test_role_rename_deactivation_preserves_prior_workday(self):
        obj=self.check();oldname=obj.role_name
        role,form=svc.save_role(actor=self.admin,role_id=self.role.pk,data=dict(name='SYNTHETIC renamed inactive',is_active='',per_diem_eligible='no',revision=self.role.updated_at.isoformat(),reason='SYNTHETIC role correction'))
        self.assertIsNotNone(role,form.errors);obj.refresh_from_db();self.assertEqual(obj.role_name,oldname);self.assertFalse(role.is_active)
    def test_new_role_has_no_invented_rate_or_eligibility(self):
        obj,form=svc.save_role(actor=self.admin,data=dict(name='SYNTHETIC new role',is_active='on',per_diem_eligible='unknown',revision='new',reason='SYNTHETIC new role'))
        self.assertIsNotNone(obj,form.errors);self.assertIsNone(obj.per_diem_eligible);self.assertIsNone(svc.rate_for('role',date(2030,7,1),obj))
    def test_rate_and_role_changes_are_audited(self):
        self.new_rate();self.assertTrue(AuditLog.objects.filter(action='workdays.rate.version',old_data__isnull=False).exists())
    def test_database_negative_rate_constraint(self):
        with self.assertRaises(IntegrityError),transaction.atomic():CompensationRate.objects.create(kind='mileage',amount=-1,effective_from=date(2030,7,1),reason='SYNTHETIC invalid')
    def test_reports_three_days_one_trip_and_no_double_counting(self):
        for day in [1,2,3]:self.action(self.check(day=day))
        self.distance();self.trip_action(MileageTrip.objects.get())
        days,trips=reports.querysets({});self.assertEqual(days.count(),3);self.assertEqual(trips.count(),1)
        totals=reports.totals(days,trips);self.assertEqual(totals,dict(pay=300,per_diem=60,mileage=100))
        rows=list(csv.DictReader(StringIO(reports.csv_export(days,trips).content.decode())))
        self.assertEqual(sum(r['Record type']=='mileage_trip' for r in rows),1);self.assertEqual(sum(bool(r['Trip reimbursement USD']) for r in rows),1)
    def test_report_filters_employee_event_dates_role(self):
        self.check(day=1);self.check(day=2,role=self.unknown);self.trip(staff=self.second)
        days,trips=reports.querysets(dict(event=self.event,employee=self.staff,start=date(2030,7,1),end=date(2030,7,1),role=self.role))
        self.assertEqual(days.count(),1);self.assertEqual(trips.count(),1)
    def test_csv_formula_injection_and_unknown_amounts(self):
        self.event.name='=SYNTHETIC()';self.event.save();self.check(role=self.unknown)
        response=reports.csv_export(Workday.objects.select_related('event','staff'),MileageTrip.objects.all());rows=list(csv.DictReader(StringIO(response.content.decode())))
        self.assertEqual(rows[0]['Event'],"'=SYNTHETIC()");self.assertEqual(rows[0]['Daily pay USD'],'')
    def test_public_and_staff_manager_routes_denied(self):
        for user in [self.employee,self.other,self.public]:
            self.client.force_login(user,backend='django.contrib.auth.backends.ModelBackend')
            for name,args in [('workdays:index',[]),('workdays:reports',[]),('workdays:settings',[]),('workdays:event',[self.event.pk]),('workdays:add',[self.event.pk]),('workdays:distance',[self.event.pk]),('workdays:trip-add',[self.event.pk])]:
                with self.subTest(user=user.username,route=name):self.assertEqual(self.client.get(reverse(name,args=args)).status_code,403)
    def test_staff_cannot_edit_other_record_or_approve(self):
        obj=self.manual();self.client.force_login(self.other,backend='django.contrib.auth.backends.ModelBackend');self.assertEqual(self.client.get(reverse('workdays:record',args=[obj.pk])).status_code,403)
        self.client.force_login(self.employee,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('workdays:record',args=[obj.pk])).status_code,200)
        self.assertEqual(self.client.post(reverse('workdays:action',args=[obj.pk,'approve']),{'revision':obj.updated_at.isoformat(),'reason':'SYNTHETIC'}).status_code,403)
    def test_staff_history_does_not_expose_other_compensation(self):
        self.manual(staff=str(self.second.pk));own=self.manual();self.client.force_login(self.employee,backend='django.contrib.auth.backends.ModelBackend');r=self.client.get(reverse('workdays:mine'));self.assertContains(r,'SYNTHETIC 9E event');self.assertNotContains(r,'SYNTHETIC 9E Other')
    def test_manager_and_admin_pages_render_with_portal_navigation(self):
        obj=self.manual();self.trip();self.distance()
        urls=[reverse('workdays:index'),reverse('workdays:reports'),reverse('workdays:event',args=[self.event.pk]),reverse('workdays:record',args=[obj.pk]),reverse('workdays:edit',args=[obj.pk]),reverse('workdays:distance',args=[self.event.pk]),reverse('workdays:trip-add',args=[self.event.pk])]
        for url in urls:
            with self.subTest(url=url):self.assertEqual(self.client.get(url).status_code,200)
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        for url in [reverse('workdays:settings'),reverse('workdays:role-add'),reverse('workdays:role-edit',args=[self.role.pk]),reverse('workdays:rate-edit',args=['mileage']),reverse('workdays:rate-edit',args=['role'])+'?role='+str(self.role.pk)]:
            with self.subTest(url=url):self.assertEqual(self.client.get(url).status_code,200)
    def test_manager_settings_denied_export_allowed(self):
        self.assertEqual(self.client.get(reverse('workdays:settings')).status_code,403)
        r=self.client.get(reverse('workdays:reports'),{'export':'csv'});self.assertEqual(r.status_code,200);self.assertIn('text/csv',r['Content-Type']);self.assertTrue(AuditLog.objects.filter(action='workdays.report.exported').exists())
    def test_invalid_report_filters_return_errors_not_all_records(self):
        self.manual();r=self.client.get(reverse('workdays:reports'),{'start':'not-a-date'});self.assertEqual(r.status_code,200);self.assertEqual(r.context['days'].paginator.count,0)
    def test_get_requests_do_not_approve_or_void(self):
        obj=self.manual();self.client.get(reverse('workdays:action',args=[obj.pk,'approve']));obj.refresh_from_db();self.assertEqual(obj.approval,'pending');self.assertTrue(obj.is_active)
    def test_csrf_protects_workday_actions(self):
        obj=self.manual();client=Client(enforce_csrf_checks=True);client.force_login(self.manager,backend='django.contrib.auth.backends.ModelBackend');r=client.post(reverse('workdays:action',args=[obj.pk,'approve']),{'revision':obj.updated_at.isoformat(),'reason':'SYNTHETIC'});self.assertEqual(r.status_code,403)
    def test_checked_in_page_and_schedule_integration(self):
        self.check();self.client.force_login(self.employee,backend='django.contrib.auth.backends.ModelBackend')
        with patch('apps.workdays.views.timezone.localdate',return_value=date(2030,7,1)):
            r=self.client.get(reverse('workdays:checkin',args=[self.event.pk]))
        self.assertContains(r,'Already checked in for today');self.assertContains(self.client.get(reverse('staffing:schedule')+'?view=assignments'),'Daily Check-In')
    def test_audit_contains_original_and_corrected_values(self):
        obj=self.check();self.manual(old=obj,per_diem='ineligible');log=AuditLog.objects.get(action='workdays.workday.corrected');self.assertEqual(log.old_data['per_diem'],'eligible');self.assertEqual(log.new_data['per_diem'],'ineligible');self.assertEqual(log.actor,self.manager)
    def test_verified_reference_seed_and_no_invented_mileage_default(self):
        self.assertEqual(StaffRole.objects.filter(name__in=['Staff','Staff Lead','Driver','Combined']).count(),4)
        self.assertEqual(svc.rate_for('role',date(2026,10,19),StaffRole.objects.get(name='Staff')).amount,200)
        self.assertIsNone(svc.rate_for('mileage',date(2026,10,19)))

    def test_workday_approval_uses_amounts_already_reviewed(self):
        obj=self.manual()
        CompensationRate.objects.create(kind='role',role=self.role,amount=999,effective_from=date(2030,1,1),reason='SYNTHETIC later configuration')
        CompensationRate.objects.create(kind='per_diem',amount=888,effective_from=date(2030,1,1),reason='SYNTHETIC later configuration')
        obj=self.action(obj);self.assertEqual(obj.pay_amount,100);self.assertEqual(obj.per_diem_amount,20)
    def test_pending_mileage_cannot_skip_verification(self):
        self.distance();obj=self.trip()
        with self.assertRaises(ValidationError):svc.trip_action(actor=self.manager,trip_id=obj.pk,action='approve',data={'revision':obj.updated_at.isoformat(),'reason':'SYNTHETIC attempt'})
        obj.refresh_from_db();self.assertEqual(obj.status,'pending')
    def test_verified_trip_approval_preserves_reviewed_amount(self):
        self.distance();obj=self.trip_action(self.trip(),'verify')
        self.distance('999');CompensationRate.objects.create(kind='mileage',amount=9,effective_from=date(2030,1,1),reason='SYNTHETIC later rate')
        obj=self.trip_action(obj);self.assertEqual(obj.one_way_miles,100);self.assertEqual(obj.rate_amount,Decimal('.5'));self.assertEqual(obj.amount,100)
    def test_venue_changed_after_verification_blocks_approval(self):
        self.distance();obj=self.trip_action(self.trip(),'verify');self.event.city='SYNTHETIC changed city';self.event.save()
        with self.assertRaises(ValidationError):self.trip_action(obj)

    def test_report_pagination_preserves_event_filter(self):
        for i in range(41):
            staff=StaffProfile.objects.create(first_name='SYNTHETIC',last_name=f'Pagination {i}')
            Workday.objects.create(event=self.event,staff=staff,work_date=date(2030,7,1),role=self.role,role_name=self.role.name,source='manager',effective_checkin_at=datetime(2030,7,1,13,tzinfo=UTC))
        r=self.client.get(reverse('workdays:reports'),{'event':str(self.event.pk),'role':str(self.role.pk)})
        self.assertContains(r,'event='+str(self.event.pk)+'&amp;role='+str(self.role.pk)+'&amp;page=2')

    def test_manager_driving_declaration_retains_manager_trip_source(self):
        obj=self.manual(staff=str(self.second.pk),drove='on')
        self.assertEqual(obj.trip.source,'manager');self.assertEqual(obj.trip.created_by,self.manager)

    def test_database_rejects_approved_workday_without_snapshot(self):
        obj=self.manual(role=str(self.unknown.pk),per_diem='review')
        with self.assertRaises(IntegrityError),transaction.atomic():Workday.objects.filter(pk=obj.pk).update(approval='approved',approved_at=timezone.now())
    def test_database_rejects_verified_trip_without_snapshot(self):
        obj=self.trip()
        with self.assertRaises(IntegrityError),transaction.atomic():MileageTrip.objects.filter(pk=obj.pk).update(status='verified')

    def test_manager_can_record_historical_inactive_employee_without_reactivation(self):
        self.second.is_active=False;self.second.save()
        day=self.manual(staff=str(self.second.pk));trip=self.trip(staff=self.second)
        self.assertEqual(day.staff_id,self.second.pk);self.assertEqual(trip.staff_id,self.second.pk)
        self.second.refresh_from_db();self.assertFalse(self.second.is_active)
