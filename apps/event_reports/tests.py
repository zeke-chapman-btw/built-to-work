"""Synthetic Build 9F financial integrity, metrics, permissions and portal acceptance."""
import csv,uuid
from datetime import timedelta,date
from decimal import Decimal
from io import StringIO
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.test import TestCase,Client,override_settings
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event,EventRegistration,Attendance
from apps.participants.models import Participant
from apps.stations.models import ExperienceSession,ExperienceActivity,Station
from apps.assessments.models import QuizAttempt,AssessmentCategory,QuestionSet,AssessmentSection,AssessmentResponse
from apps.simulators.models import SimulatorCapture,SimulatorCaptureProfile
from apps.staffing.models import StaffProfile,StaffAssignment,AssignmentDay
from apps.workdays.models import Workday,MileageTrip,StaffRole,CompensationRate
from apps.event_reports.models import ExpenseCategory,ManualExpense,IncentiveLine,CostReview
from apps.event_reports import services as svc,reporting

@override_settings(DEBUG=True)
class Reporting9FTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.admin=U.objects.create_superuser('synthetic-9f-admin','9f@example.test','test-password')
        cls.manager=U.objects.create_user('synthetic-9f-manager',password='test-password',is_staff=True)
        cls.staff=U.objects.create_user('synthetic-9f-staff',password='test-password',is_staff=True)
        cls.reader=U.objects.create_user('synthetic-9f-reader',password='test-password',is_staff=True)
        for permission in svc.REPORT_PERMS+('event_reports.manage_event_expenses',):
            app,code=permission.split('.');cls.manager.user_permissions.add(Permission.objects.get(content_type__app_label=app,codename=code))
        for permission in svc.REPORT_PERMS:
            app,code=permission.split('.');cls.reader.user_permissions.add(Permission.objects.get(content_type__app_label=app,codename=code))
        cls.now=timezone.now()
        cls.event=Event.objects.create(name='SYNTHETIC 9F event',start_at=cls.now-timedelta(days=2),end_at=cls.now+timedelta(days=1),status='upcoming',timezone_name='America/New_York',location_name='SYNTHETIC venue')
        cls.other_event=Event.objects.create(name='SYNTHETIC other 9F',start_at=cls.now-timedelta(days=4),end_at=cls.now-timedelta(days=3),status='completed')
        cls.category=ExpenseCategory.objects.get(name='Fuel')
        cls.purchase_category=ExpenseCategory.objects.get(name='Gift cards and incentives')
        cls.profile=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='9F staff')
        cls.other_profile=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='9F other')
        cls.role=StaffRole.objects.get(name='Staff')
        cls.rate=CompensationRate.objects.create(kind='mileage',amount='.5',effective_from=date(2020,1,1),reason='SYNTHETIC')
        cls.station=Station.objects.create(code='synthetic-9f-sim',name='SYNTHETIC simulator',station_type='simulator')
        cls.capture_profile=SimulatorCaptureProfile.objects.create(name='SYNTHETIC 9F capture',simulator_type='Synthetic')
        cls.assessment_category=AssessmentCategory.objects.create(name='SYNTHETIC 9F category',slug='synthetic-9f')
        cls.questions=QuestionSet.objects.create(category=cls.assessment_category,name='SYNTHETIC pool',version=1)
    def setUp(self):self.client.force_login(self.manager,backend="django.contrib.auth.backends.ModelBackend")
    def expense_data(self,**changes):
        d={'category':str(self.category.pk),'description':'SYNTHETIC fuel','amount':'12.34','expense_date':self.event.local_start_at.date().isoformat(),'treatment':'cost','reference':'SYNTHETIC reference','notes':'Synthetic only','reason':'SYNTHETIC acceptance','revision':'new','request_key':str(uuid.uuid4())};d.update(changes);return d
    def incentive_data(self,**changes):
        d={'description':'SYNTHETIC gift','unit_value':'27.35','quantity':'3','expense_date':self.event.local_start_at.date().isoformat(),'notes':'Distributed synthetic fixture','reason':'SYNTHETIC acceptance','revision':'new','request_key':str(uuid.uuid4())};d.update(changes);return d
    def expense(self,**changes):
        obj,form=svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='expense',data=self.expense_data(**changes));self.assertIsNotNone(obj,form.errors);return obj
    def incentive(self,**changes):
        obj,form=svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='incentive',data=self.incentive_data(**changes));self.assertIsNotNone(obj,form.errors);return obj
    def reconcile(self,event=None):
        event=event or self.event
        obj,form=svc.save_review(actor=self.manager,event_id=event.pk,data={'revision':svc.review_token(event),'reason':'SYNTHETIC reconciliation','manual_reviewed':'on','incentives_reviewed':'on','staff_reviewed':'on'});self.assertIsNotNone(obj,form.errors);return obj
    def day(self,**changes):
        d=dict(event=self.event,staff=self.profile,work_date=self.event.local_start_at.date(),role=self.role,role_name='Staff',source='manager',effective_checkin_at=self.now,approval='approved',pay_amount=100,per_diem_amount=20,per_diem='eligible',approved_at=self.now);d.update(changes);return Workday.objects.create(**d)
    def trip(self,**changes):
        d=dict(event=self.event,staff=self.profile,number=1,claim_date=self.event.local_start_at.date(),one_way_miles=100,round_trip_miles=200,rate=self.rate,rate_amount='.5',amount=100,status='approved',approved_at=self.now,source='manager');d.update(changes);return MileageTrip.objects.create(**d)
    def person_session(self,event=None,name=None):
        person=Participant.objects.create(first_name='SYNTHETIC',last_name=name or uuid.uuid4().hex[:10])
        reg=EventRegistration.objects.create(event=event or self.event,participant=person)
        session=ExperienceSession.objects.create(event=event or self.event,participant=person,registration=reg,started_at=self.now-timedelta(minutes=5))
        for activity in ['kiosk','simulator']:ExperienceActivity.objects.create(session=session,activity=activity)
        return person,reg,session
    def finish_kiosk(self,session,correct=2):
        attempt=QuizAttempt.objects.create(experience_session=session,participant=session.participant,is_official=True,status='complete',completed_at=self.now)
        section=AssessmentSection.objects.create(attempt=attempt,category=self.assessment_category,question_set=self.questions,position=1,category_name_snapshot='SYNTHETIC',questions_snapshot=[{}, {}, {}],status='complete')
        for n in range(3):AssessmentResponse.objects.create(section=section,question_position=n,selected_choice_key='synthetic',is_correct=n<correct)
        ExperienceActivity.objects.filter(session=session,activity='kiosk').update(status='completed',started_at=self.now-timedelta(minutes=2),completed_at=self.now)
        return attempt
    def finish_simulator(self,session,score='82.5'):
        capture=SimulatorCapture.objects.create(submission_id=uuid.uuid4(),request_fingerprint='synthetic',event=session.event,station=self.station,profile=self.capture_profile,profile_version='1',raw_identifier='synthetic',raw_total_score=score,total_score=score,captured_at=self.now,status='matched',mode='official',is_official=True,registration=session.registration,participant=session.participant,experience_session=session)
        ExperienceActivity.objects.filter(session=session,activity='simulator').update(status='completed',started_at=self.now-timedelta(minutes=1),completed_at=self.now)
        return capture
    def csv_rows(self,url):return list(csv.reader(StringIO(self.client.get(url).content.decode())))

    def test_expense_creation_decimal_audit(self):
        obj=self.expense();self.assertEqual(obj.amount,Decimal('12.34'));self.assertEqual(obj.source,'Manual');self.assertEqual(obj.category_name,'Fuel');self.assertTrue(AuditLog.objects.filter(action='event_reports.expense.created',object_id=str(obj.pk)).exists())
    def test_expense_edit_preserves_before_values(self):
        obj=self.expense();before=obj.updated_at.isoformat();data=self.expense_data(amount='25.75',request_key=str(obj.request_key),revision=before)
        saved,form=svc.save_entry(actor=self.manager,event_id=self.event.pk,record_id=obj.pk,kind='expense',data=data)
        self.assertIsNotNone(saved,form.errors);log=AuditLog.objects.get(action='event_reports.expense.corrected');self.assertEqual(log.old_data['amount'],'12.34');self.assertEqual(log.new_data['amount'],'25.75')
    def test_void_expense_retains_history_excludes_cost(self):
        obj=self.expense();svc.void_entry(actor=self.manager,record_id=obj.pk,kind='expense',data={'revision':obj.updated_at.isoformat(),'reason':'SYNTHETIC void'});obj.refresh_from_db();self.assertFalse(obj.is_active);self.assertEqual(reporting.costs(self.event)['manual'],0);self.assertTrue(AuditLog.objects.filter(action='event_reports.expense.voided').exists())
    def test_negative_expense_rejected(self):
        obj,form=svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='expense',data=self.expense_data(amount='-1'));self.assertIsNone(obj);self.assertIn('amount',form.errors)
    def test_nonfinite_amount_rejected(self):
        for amount in ['NaN','Infinity','1.001']:
            obj,form=svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='expense',data=self.expense_data(amount=amount));self.assertIsNone(obj)
    def test_correction_reason_required(self):
        obj=self.expense()
        saved,form=svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='expense',record_id=obj.pk,data=self.expense_data(reason='   ',revision=obj.updated_at.isoformat(),request_key=str(obj.request_key)))
        self.assertIsNone(saved);self.assertIn('reason',form.errors)
    def test_duplicate_submission_rejected(self):
        data=self.expense_data();svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='expense',data=data)
        with self.assertRaises(ValidationError):svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='expense',data=data)
        self.assertEqual(ManualExpense.objects.count(),1)
    def test_stale_expense_edit_rejected(self):
        obj=self.expense()
        with self.assertRaises(ValidationError):svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='expense',record_id=obj.pk,data=self.expense_data(revision='stale',request_key=str(obj.request_key)))
    def test_wrong_event_edit_rejected(self):
        obj=self.expense()
        with self.assertRaises(ManualExpense.DoesNotExist):svc.save_entry(actor=self.manager,event_id=self.other_event.pk,kind='expense',record_id=obj.pk,data=self.expense_data())
    def test_void_record_readonly(self):
        obj=self.expense();obj.is_active=False;obj.save()
        with self.assertRaises(ValidationError):svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='expense',record_id=obj.pk,data=self.expense_data())
    def test_database_negative_constraint(self):
        obj=self.expense()
        with self.assertRaises(IntegrityError),transaction.atomic():ManualExpense.objects.filter(pk=obj.pk).update(amount=-1)
    def test_arbitrary_incentive_decimal_and_quantity(self):
        obj=self.incentive();self.assertEqual(obj.line_total,Decimal('82.05'));self.assertEqual(reporting.costs(self.event)['incentives'],Decimal('82.05'))
    def test_multiple_incentive_lines_sum(self):
        for value,qty in [('25',20),('50',8),('100',5),('15',10)]:self.incentive(unit_value=value,quantity=str(qty))
        self.assertEqual(reporting.costs(self.event)['incentives'],Decimal('1550'))
    def test_incentive_quantity_whole_nonnegative(self):
        for qty in ['-1','1.5']:
            obj,form=svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='incentive',data=self.incentive_data(quantity=qty));self.assertIsNone(obj)
    def test_zero_distribution_allowed_and_reviewable(self):
        self.incentive(quantity='0');self.reconcile();self.assertEqual(reporting.costs(self.event)['total'],0)
    def test_incentive_edit_audits_previous_quantity(self):
        obj=self.incentive();data=self.incentive_data(quantity='5',revision=obj.updated_at.isoformat(),request_key=str(obj.request_key));svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='incentive',record_id=obj.pk,data=data)
        log=AuditLog.objects.get(action='event_reports.incentive.corrected');self.assertEqual(log.old_data['quantity'],3);self.assertEqual(log.new_data['quantity'],5)
    def test_incentive_void_excluded(self):
        obj=self.incentive();svc.void_entry(actor=self.manager,kind='incentive',record_id=obj.pk,data={'revision':obj.updated_at.isoformat(),'reason':'SYNTHETIC void'});self.assertEqual(reporting.costs(self.event)['incentives'],0)
    def test_purchase_and_distribution_count_once(self):
        self.expense(category=str(self.purchase_category.pk),treatment='incentive_purchase',amount='100');self.incentive(unit_value='25',quantity='4');self.assertEqual(reporting.costs(self.event)['recorded'],100);self.assertEqual(reporting.costs(self.event)['excluded'],100)
    def test_purchase_category_cannot_count_as_operating(self):
        obj,form=svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='expense',data=self.expense_data(category=str(self.purchase_category.pk)));self.assertIsNone(obj);self.assertIn('treatment',form.errors)
    def test_staff_reference_never_duplicates_source(self):
        self.day();self.expense(treatment='staff_reference',amount='120');self.assertEqual(reporting.costs(self.event)['recorded'],120)
    def test_category_admin_create_rename_deactivate(self):
        obj,form=svc.save_category(actor=self.admin,data={'name':'SYNTHETIC custom','is_active':'on','revision':'new','reason':'SYNTHETIC'});self.assertIsNotNone(obj,form.errors)
        obj,form=svc.save_category(actor=self.admin,category_id=obj.pk,data={'name':'SYNTHETIC renamed','revision':obj.updated_at.isoformat(),'reason':'SYNTHETIC rename'});self.assertFalse(obj.is_active)
        self.assertEqual(AuditLog.objects.filter(action='event_reports.category.changed').count(),2)
    def test_category_history_label_preserved(self):
        obj=self.expense();self.category.name='SYNTHETIC renamed fuel';self.category.is_active=False;self.category.save();obj.refresh_from_db();self.assertEqual(obj.category_name,'Fuel');self.assertEqual(obj.category_id,self.category.pk)
    def test_inactive_category_not_available_new(self):
        self.category.is_active=False;self.category.save();obj,form=svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='expense',data=self.expense_data());self.assertIsNone(obj)
    def test_inactive_existing_category_correction_allowed(self):
        obj=self.expense();self.category.is_active=False;self.category.save();saved,form=svc.save_entry(actor=self.manager,event_id=self.event.pk,kind='expense',record_id=obj.pk,data=self.expense_data(revision=obj.updated_at.isoformat(),request_key=str(obj.request_key),amount='20'));self.assertIsNotNone(saved,form.errors)
    def test_case_insensitive_category_duplicate(self):
        obj,form=svc.save_category(actor=self.admin,data={'name':'fuel','is_active':'on','revision':'new','reason':'SYNTHETIC'});self.assertIsNone(obj)
    def test_manager_not_category_admin(self):
        with self.assertRaises(PermissionDenied):svc.save_category(actor=self.manager,data={})
    def test_staff_cannot_manage_financial_entries(self):
        with self.assertRaises(PermissionDenied):svc.save_entry(actor=self.staff,event_id=self.event.pk,kind='expense',data=self.expense_data())
    def test_approved_staff_costs_distinct_multi_day_trips(self):
        trip=self.trip();self.day(trip=trip);self.day(work_date=self.event.local_start_at.date()+timedelta(days=1),trip=trip);self.trip(staff=self.other_profile);self.trip(number=2)
        cost=reporting.costs(self.event);self.assertEqual(cost['staff_cost'],540);self.assertEqual(cost['approved_days'],2)
    def test_pending_void_reopened_excluded(self):
        self.day(approval='pending');self.trip(status='pending',amount=None);self.trip(number=2,status='void');self.day(staff=self.other_profile,is_active=False)
        cost=reporting.costs(self.event);self.assertEqual(cost['recorded'],0);self.assertFalse(cost['complete']);self.assertEqual(cost['pending_days'],1);self.assertEqual(cost['pending_trips'],1)
    def test_source_approval_state_refreshes_without_copy(self):
        obj=self.day();self.assertEqual(reporting.costs(self.event)['recorded'],120);obj.approval='pending';obj.save();self.assertEqual(reporting.costs(self.event)['recorded'],0);self.assertEqual(ManualExpense.objects.count(),0)
    def test_global_rate_changes_leave_approved_snapshot(self):
        self.day();self.trip();self.rate.amount=9;self.rate.save();self.assertEqual(reporting.costs(self.event)['recorded'],220)
    def test_no_entries_is_unknown_not_confirmed_zero(self):
        cost=reporting.costs(self.event);self.assertIsNone(cost['total']);self.assertEqual(cost['recorded'],0)
    def test_explicit_zero_review_enables_zero_total(self):
        self.reconcile();cost=reporting.costs(self.event);self.assertTrue(cost['complete']);self.assertEqual(cost['total'],0);self.assertTrue(all(c['state']=='Confirmed zero' for c in cost['components']))
    def test_pending_staff_not_overridden_by_review(self):
        self.day(approval='pending');self.reconcile();self.assertIsNone(reporting.costs(self.event)['total'])
    def test_financial_change_invalidates_review(self):
        self.expense();self.reconcile();self.assertTrue(reporting.costs(self.event)['complete']);self.incentive();self.assertFalse(reporting.costs(self.event)['complete'])
    def test_staff_source_change_invalidates_review(self):
        obj=self.day();self.reconcile();obj.pay_amount=150;obj.save();self.assertFalse(reporting.costs(self.event)['complete'])
    def test_missing_assignment_day_prevents_complete_cost(self):
        assignment=StaffAssignment.objects.create(event=self.event,staff=self.profile,status='confirmed');AssignmentDay.objects.create(assignment=assignment,date=self.event.local_start_at.date(),starts_at=self.event.start_at,ends_at=self.event.start_at+timedelta(hours=8));self.reconcile();self.assertEqual(reporting.costs(self.event)['missing_days'],1);self.assertIsNone(reporting.costs(self.event)['total'])
    def test_stale_review_rejected(self):
        revision=svc.review_token(self.event);self.expense()
        with self.assertRaises(ValidationError):svc.save_review(actor=self.manager,event_id=self.event.pk,data={'revision':revision,'reason':'SYNTHETIC'})
    def test_total_formula(self):
        self.expense(amount='10');self.incentive(unit_value='15',quantity='2');self.day();self.trip();self.reconcile();self.assertEqual(reporting.costs(self.event)['total'],260)
    def test_registration_does_not_infer_completion(self):
        self.person_session();m=reporting.performance(self.event);self.assertEqual(m['registered'],1);self.assertEqual(m['kiosk_completions'],0);self.assertEqual(m['simulator_completions'],0);self.assertIsNone(m['kiosk_average'])
    def test_unique_participant_repeated_attendance_count_once(self):
        person,reg,session=self.person_session();Attendance.objects.create(event=self.event,registration=reg,participant=person);m=reporting.performance(self.event);self.assertEqual(m['unique_participants'],1)
    def test_starts_and_completions_distinct(self):
        _,_,session=self.person_session();QuizAttempt.objects.create(experience_session=session,participant=session.participant,is_official=True);m=reporting.performance(self.event);self.assertEqual(m['kiosk_starts'],1);self.assertEqual(m['kiosk_completions'],0)
    def test_completed_scores_and_strict_experience(self):
        _,_,session=self.person_session();self.finish_kiosk(session);self.finish_simulator(session);session.completed_at=self.now;session.save();m=reporting.performance(self.event);self.assertEqual(m['completed_experiences'],1);self.assertEqual(m['kiosk_average'],2);self.assertEqual(m['simulator_average'],Decimal('82.5'));self.assertEqual(m['overall_completion_rate'],100)
    def test_skipped_stage_is_not_fully_completed(self):
        _,_,session=self.person_session();self.finish_kiosk(session);self.finish_simulator(session);ExperienceActivity.objects.create(session=session,activity='game',status='skipped');session.completed_at=self.now;session.save();self.assertEqual(reporting.performance(self.event)['completed_experiences'],0)
    def test_voided_quiz_excluded(self):
        _,_,session=self.person_session();quiz=self.finish_kiosk(session);quiz.status='void';quiz.voided_at=self.now;quiz.save();m=reporting.performance(self.event);self.assertEqual(m['kiosk_completions'],0);self.assertIsNone(m['kiosk_high'])
    def test_unresolved_and_voided_capture_excluded(self):
        _,_,session=self.person_session();capture=self.finish_simulator(session);capture.voided_at=self.now;capture.save();self.assertEqual(reporting.performance(self.event)['simulator_completions'],0)
    def test_test_demo_excluded(self):
        session=ExperienceSession.objects.create(event=self.event,mode='demo');QuizAttempt.objects.create(experience_session=session,is_official=False,status='complete',completed_at=self.now);self.assertEqual(reporting.performance(self.event)['kiosk_completions'],0)
    def test_cancelled_registration_excluded(self):
        _,reg,session=self.person_session();self.finish_kiosk(session);reg.status='cancelled';reg.save();m=reporting.performance(self.event);self.assertEqual(m['registered'],0);self.assertEqual(m['kiosk_completions'],0)
    def test_conversion_and_dropoff_set_intersection(self):
        _,_,one=self.person_session();self.finish_kiosk(one);self.finish_simulator(one);_,_,two=self.person_session();self.finish_kiosk(two);self.person_session();m=reporting.performance(self.event);self.assertEqual(m['registration_to_kiosk'],Decimal('66.67'));self.assertEqual(m['kiosk_to_simulator'],50);self.assertEqual(m['registration_dropoff'],1);self.assertEqual(m['kiosk_simulator_dropoff'],1)
    def test_zero_denominator_unavailable(self):
        self.reconcile();r=reporting.event_report(self.event);self.assertIsNone(r['cost_per_registration']);self.assertIsNone(r['cost_per_completion']);self.assertIsNone(r['metrics']['overall_completion_rate'])
    def test_weighted_aggregate_not_average_ratios(self):
        rows=[{'cost':{'recorded':Decimal('100'),'complete':True,'approved_days':1},'metrics':{'registered':1,'completed_experiences':1}},{'cost':{'recorded':Decimal('100'),'complete':True,'approved_days':1},'metrics':{'registered':9,'completed_experiences':3}}]
        result=reporting.aggregate(rows);self.assertEqual(result['cost_per_registration'],20);self.assertEqual(result['cost_per_completion'],50)
    def test_aggregate_incomplete_does_not_hide_unknown_cost(self):
        self.reconcile();result=reporting.aggregate([reporting.event_report(self.event),reporting.event_report(self.other_event)]);self.assertIsNone(result['total']);self.assertEqual(result['incomplete'],1)
    def test_cross_event_filters(self):
        response=self.client.get(reverse('event_reports:index'),{'q':'other 9F','status':'completed'});self.assertEqual(response.status_code,200);self.assertEqual(response.context['summary']['count'],1)
    def test_invalid_filter_dates_rejected(self):
        response=self.client.get(reverse('event_reports:index'),{'start':'2030-02-02','end':'2030-01-01'});self.assertEqual(response.status_code,400)
    def test_local_date_filter(self):
        local=self.event.local_start_at.date().isoformat();response=self.client.get(reverse('event_reports:index'),{'start':local,'end':local});self.assertEqual(response.context['summary']['count'],1)
    def test_report_pages_render_no_private_notes(self):
        from apps.operations.models import EventNote
        EventNote.objects.create(event=self.event,content='SYNTHETIC SECRET MANAGER NOTE',author=self.admin,visibility='managers')
        response=self.client.get(reverse('event_reports:detail',args=[self.event.pk]));self.assertEqual(response.status_code,200);self.assertNotContains(response,'SYNTHETIC SECRET MANAGER NOTE');self.assertContains(response,'Gift Cards &amp; Incentives')
    def test_note_managers_see_authorized_existing_notes(self):
        from apps.operations.models import EventNote
        EventNote.objects.create(event=self.event,content='SYNTHETIC authorized note',author=self.admin,visibility='managers');self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend");self.assertContains(self.client.get(reverse('event_reports:detail',args=[self.event.pk])),'SYNTHETIC authorized note')
    def test_staff_reports_and_exports_denied(self):
        self.client.force_login(self.staff,backend="django.contrib.auth.backends.ModelBackend")
        for url in [reverse('event_reports:index'),reverse('event_reports:detail',args=[self.event.pk]),reverse('event_reports:categories')]:self.assertEqual(self.client.get(url,{'export':'csv'}).status_code,403)
    def test_source_permission_required(self):
        permission=Permission.objects.get(content_type__app_label='simulators',codename='view_simulatorcapture');self.manager.user_permissions.remove(permission);self.client.force_login(self.manager,backend="django.contrib.auth.backends.ModelBackend");self.assertEqual(self.client.get(reverse('event_reports:index')).status_code,403)
    def test_readonly_report_user_cannot_write(self):
        self.client.force_login(self.reader,backend="django.contrib.auth.backends.ModelBackend");self.assertEqual(self.client.post(reverse('event_reports:expense-add',args=[self.event.pk]),self.expense_data()).status_code,403)
    def test_csv_expense_source_trip_once(self):
        self.day();self.trip();self.day(work_date=self.event.local_start_at.date()+timedelta(days=1));self.incentive();rows=self.csv_rows(reverse('event_reports:detail',args=[self.event.pk])+'?export=expenses');self.assertEqual(sum(r[1]=='Staff mileage' for r in rows),1);self.assertEqual(sum(r[1]=='Staff daily pay' for r in rows),2)
    def test_csv_incentive_arbitrary_values(self):
        self.incentive();rows=self.csv_rows(reverse('event_reports:detail',args=[self.event.pk])+'?export=incentives');self.assertEqual(rows[1][4],'82.05')
    def test_csv_formula_safe_all_exports(self):
        self.event.name='=SYNTHETIC formula';self.event.save();self.expense(description='@SYNTHETIC');self.incentive(description='+SYNTHETIC')
        for export in ['expenses','incentives','performance']:
            rows=self.csv_rows(reverse('event_reports:detail',args=[self.event.pk])+'?export='+export);self.assertTrue(rows[1][0].startswith("'="))
        rows=self.csv_rows(reverse('event_reports:index')+'?export=csv');self.assertTrue(any(r[0].startswith("'=") for r in rows[1:]))
    def test_csv_private_cache_and_audit(self):
        response=self.client.get(reverse('event_reports:index'),{'export':'csv'});self.assertEqual(response['Cache-Control'],'private, no-store');self.assertTrue(AuditLog.objects.filter(action='event_reports.exported').exists())
    def test_csrf_required(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.manager,backend="django.contrib.auth.backends.ModelBackend");self.assertEqual(client.post(reverse('event_reports:expense-add',args=[self.event.pk]),self.expense_data()).status_code,403)
    def test_archived_finances_readonly(self):
        self.event.status='archived';self.event.save()
        with self.assertRaises(ValidationError):self.expense()
        self.assertEqual(self.client.get(reverse('event_reports:detail',args=[self.event.pk])).status_code,200)
    def test_category_history_and_usage_portal(self):
        self.expense();self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend");response=self.client.get(reverse('event_reports:categories'));self.assertEqual(response.status_code,200);self.assertContains(response,'1 historical expense references')
    def test_financial_history_portal(self):
        obj=self.expense();response=self.client.get(reverse('event_reports:action',args=['expense',obj.pk,'history']));self.assertEqual(response.status_code,200);self.assertContains(response,'event_reports.expense.created')
    def test_compensation_settings_not_duplicated(self):
        self.assertEqual(Workday.objects.count(),0);self.assertEqual(CompensationRate.objects.filter(kind='mileage').count(),1)

    def test_report_dates_remain_in_event_timezone(self):
        from datetime import datetime,UTC
        self.event.start_at=datetime(2030,1,2,2,tzinfo=UTC);self.event.end_at=datetime(2030,1,3,2,tzinfo=UTC);self.event.save()
        detail=self.client.get(reverse('event_reports:detail',args=[self.event.pk]))
        self.assertContains(detail,'Jan 1, 2030')
        comparison=self.client.get(reverse('event_reports:index'),{'q':self.event.name})
        self.assertContains(comparison,'Jan 1, 2030')

    def test_edit_forms_keep_original_record_date(self):
        actual=(self.event.local_start_at.date()+timedelta(days=1)).isoformat()
        for kind,obj in [('expense',self.expense(expense_date=actual)),('incentive',self.incentive(expense_date=actual))]:
            response=self.client.get(reverse('event_reports:'+kind+'-edit',args=[obj.pk]))
            self.assertEqual(response.context['form']['expense_date'].value().isoformat(),actual)
    def test_second_review_form_cannot_overwrite_newer_reconciliation(self):
        token=svc.review_token(self.event)
        self.reconcile()
        with self.assertRaises(ValidationError):
            svc.save_review(actor=self.manager,event_id=self.event.pk,data={'revision':token,'reason':'SYNTHETIC stale review'})
