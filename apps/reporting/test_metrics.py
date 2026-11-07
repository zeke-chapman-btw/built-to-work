import uuid
from datetime import datetime,timedelta,date,UTC
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from apps.events.models import Event,EventRegistration,ParticipantMatchReview,ParticipantIdentityLink
from apps.participants.models import Participant,ParticipantCareerProfile
from apps.stations.models import Station,ExperienceSession
from apps.simulators.models import SimulatorCapture,SimulatorCaptureProfile
from apps.staffing.models import StaffProfile,StaffAssignment,AssignmentDay
from apps.workdays.models import Workday,MileageTrip,StaffRole,CompensationRate
from apps.event_reports.models import ManualExpense,ExpenseCategory,IncentiveLine
from apps.event_reports.services import save_review,review_token
from . import metrics,services,definitions
from .models import EventReport,EventReportVersion,CompensationAdjustment
MAPPING={"seeking":"yes","open":"maybe","not_seeking":"no"}

class ReportingFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model();cls.admin=U.objects.create_superuser("synthetic-report-admin","report-admin@example.test","synthetic-test-password",can_finalize_event_reports=True,can_approve_weekly_payroll=True)
        cls.when=datetime(2026,11,2,15,tzinfo=UTC);cls.monday=date(2026,11,2)
        cls.event=Event.objects.create(name="SYNTHETIC Reporting Week",start_at=cls.when,end_at=cls.when+timedelta(days=6),status="completed",timezone_name="America/New_York",state="GA")
        cls.station=Station.objects.create(code="synthetic-r17-sim",name="SYNTHETIC simulator",station_type="simulator")
        cls.capture_profile=SimulatorCaptureProfile.objects.create(name="SYNTHETIC R17 profile",simulator_type="Synthetic")
        cls.worker=U.objects.create_user("synthetic-contractor-one",password="synthetic-test-password",is_staff=True)
        cls.other=U.objects.create_user("synthetic-contractor-two",password="synthetic-test-password",is_staff=True)
        cls.staff=StaffProfile.objects.create(first_name="SYNTHETIC",last_name="Contractor One",user=cls.worker)
        cls.other_staff=StaffProfile.objects.create(first_name="SYNTHETIC",last_name="Contractor Two",user=cls.other)
        cls.role=StaffRole.objects.get(name="Staff")
        cls.rate=CompensationRate.objects.create(kind="mileage",amount=".5",effective_from=date(2020,1,1),reason="SYNTHETIC")
    def person(self,interest="",event=None):
        p=Participant.objects.create(first_name="SYNTHETIC",last_name=uuid.uuid4().hex[:8])
        ParticipantCareerProfile.objects.create(participant=p,employment_interest=interest)
        e=event or self.event;r=EventRegistration.objects.create(event=e,participant=p)
        s=ExperienceSession.objects.create(event=e,registration=r,participant=p,started_at=self.when)
        return p,r,s
    def capture(self,session,score="50",**overrides):
        if SimulatorCapture.objects.filter(experience_session=session).exists():
            root=session.participant
            alias=Participant.objects.create(first_name="SYNTHETIC",last_name="Reviewed duplicate "+uuid.uuid4().hex[:8])
            review=ParticipantMatchReview.objects.create(established_person_review=True,source_participant=alias,candidate_participant=root,evidence={},status="resolved")
            ParticipantIdentityLink.objects.create(review=review,source_participant=alias,candidate_participant=root)
            registration=EventRegistration.objects.create(event=session.event,participant=alias)
            session=ExperienceSession.objects.create(event=session.event,participant=alias,registration=registration,started_at=self.when)
        data=dict(submission_id=uuid.uuid4(),request_fingerprint="synthetic",event=session.event,station=self.station,profile=self.capture_profile,profile_version="1",raw_identifier="SYNTHETIC",raw_total_score=str(score),total_score=score,captured_at=self.when,status="matched",mode="official",is_official=True,registration=session.registration,participant=session.participant,experience_session=session)
        data.update(overrides);return SimulatorCapture.objects.create(**data)
    def day(self,**overrides):
        data=dict(event=self.event,staff=self.staff,work_date=self.monday,role=self.role,role_name="Staff",source="manager",effective_checkin_at=self.when,approval="approved",pay_amount=Decimal("100"),per_diem_amount=Decimal("20"),per_diem="eligible",approved_at=self.when)
        data.update(overrides);return Workday.objects.create(**data)
    def reconcile(self):
        row,form=save_review(actor=self.admin,event_id=self.event.pk,data={"revision":review_token(self.event),"reason":"SYNTHETIC reconciliation","manual_reviewed":"on","incentives_reviewed":"on","staff_reviewed":"on"})
        self.assertIsNotNone(row,form.errors)

class EventMetricTests(ReportingFixtures):
    def test_unique_association_counts_do_not_claim_attendance(self):
        p,r,s=self.person();EventRegistration.objects.create(event=self.event,participant=p,status="void")
        row=metrics.event_metrics(self.admin,Event.objects.filter(pk=self.event.pk))[0]
        self.assertEqual((row["leads"],row["registrations"],row["plays"]),(1,1,0))
    def test_archiving_a_person_does_not_erase_historical_event_leads(self):
        person,registration,session=self.person();person.archived_at=self.when;person.save(update_fields=["archived_at"])
        row=metrics.event_metrics(self.admin,Event.objects.filter(pk=self.event.pk))[0];self.assertEqual(row["leads"],1)
    def test_reviewed_aliases_count_once_without_rewriting_records(self):
        p,r,s=self.person();alias,ar,ss=self.person()
        review=ParticipantMatchReview.objects.create(established_person_review=True,source_participant=alias,candidate_participant=p,evidence={},status="resolved")
        ParticipantIdentityLink.objects.create(review=review,source_participant=alias,candidate_participant=p)
        row=metrics.event_metrics(self.admin,Event.objects.filter(pk=self.event.pk))[0]
        self.assertEqual((row["leads"],row["registrations"]),(1,2));self.assertEqual(EventRegistration.objects.count(),2)
    def test_same_person_at_distinct_events_counts_separately(self):
        p,r,s=self.person();e=Event.objects.create(name="SYNTHETIC second",start_at=self.when,end_at=self.when,status="completed")
        EventRegistration.objects.create(event=e,participant=p)
        self.assertEqual(sum(r["leads"] for r in metrics.event_metrics(self.admin,Event.objects.all())),2)
    def test_highest_per_person_average_and_threshold_equality(self):
        with patch.object(definitions,"INTEREST_MAPPING",MAPPING):
            p,r,s=self.person("seeking");self.capture(s,"10");self.capture(s,"50")
            q,rr,ss=self.person("open");self.capture(ss,"100")
            row=metrics.event_metrics(self.admin,Event.objects.filter(pk=self.event.pk))[0]
            self.assertEqual(row["plays"],3);self.assertEqual(row["average_score"],Decimal("75"));self.assertEqual(row["meeting_threshold"],2);self.assertEqual(row["qualified"],2)
    def test_interest_intersection_excludes_no_missing_and_low_scores(self):
        with patch.object(definitions,"INTEREST_MAPPING",MAPPING):
            for interest,score in [("seeking","50"),("open","49"),("not_seeking","99"),("","100")]:
                p,r,s=self.person(interest);self.capture(s,score)
            row=metrics.event_metrics(self.admin,Event.objects.filter(pk=self.event.pk))[0]
            self.assertEqual(row["interested"],2);self.assertEqual(row["qualified"],1);self.assertEqual(row["interest_missing"],1)
    def test_invalid_incomplete_demo_void_replaced_sources_excluded(self):
        p,r,s=self.person();valid=self.capture(s,"75")
        for changes in [{"status":"needs_review"},{"mode":"demo"},{"is_official":False},{"voided_at":self.when},{"total_score":None},{"total_score":"101"},{"replaced_by":valid}]:self.capture(s,**changes)
        row=metrics.event_metrics(self.admin,Event.objects.filter(pk=self.event.pk))[0];self.assertEqual(row["plays"],1);self.assertEqual(row["average_score"],75)
    def test_archived_event_completion_requires_retained_execution_evidence(self):
        from apps.operations.execution_models import EventExecution
        from django.test import RequestFactory
        from .presentation import workspace_data
        self.event.status="archived";self.event.save()
        request=RequestFactory().get("/internal/reports/")
        def conducted():return next(c["value"] for c in workspace_data(self.admin,"events",{},request)["cards"] if c["label"]=="Events conducted")
        self.assertEqual(conducted(),0)
        EventExecution.objects.create(event=self.event,actual_ended_at=self.when,ended_by=self.admin)
        self.assertEqual(conducted(),1)
    def test_event_specific_thresholds(self):
        p,r,s=self.person();self.capture(s,"50");self.event.simulator_qualification_threshold=75;self.event.save()
        self.assertEqual(metrics.event_metrics(self.admin,Event.objects.filter(pk=self.event.pk))[0]["meeting_threshold"],0)
    def test_zero_denominators_and_missing_policy_are_unavailable(self):
        with patch.object(definitions,"INTEREST_MAPPING",None):
            row=metrics.event_detail(self.admin,self.event);self.assertIsNone(row["interested"]);self.assertIsNone(row["qualified"]);self.assertIsNone(row["average_score"]);self.assertIsNone(row["cost_per_lead"])
    def test_source_queries_are_aggregated_not_one_query_per_person(self):
        for n in range(10):p,r,s=self.person();self.capture(s,"70")
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as queries:
            metrics.event_metrics(self.admin,Event.objects.filter(pk=self.event.pk))
        self.assertLessEqual(len(queries),35)

class FinancialAndFinalizationTests(ReportingFixtures):
    def test_approved_workdays_and_trips_count_once(self):
        self.day();self.day(work_date=self.monday+timedelta(days=1),effective_checkin_at=self.when+timedelta(days=1),pay_amount=50,per_diem_amount=0)
        MileageTrip.objects.create(event=self.event,staff=self.staff,number=1,claim_date=self.monday,one_way_miles=100,round_trip_miles=200,rate=self.rate,rate_amount=".5",amount=100,status="approved",approved_at=self.when,source="manager")
        cost=metrics.event_finances(self.admin,self.event);self.assertEqual(cost["staff_cost"],270)
    def test_expense_treatments_and_adjustments_do_not_duplicate(self):
        self.day();category=ExpenseCategory.objects.get(name="Fuel")
        for treatment,amount in [("cost","20"),("staff_reference","500"),("incentive_purchase","100")]:
            ManualExpense.objects.create(event=self.event,description="SYNTHETIC",expense_date=self.monday,category=category,category_name=category.name,amount=amount,treatment=treatment,reason="SYNTHETIC",request_key=uuid.uuid4())
        IncentiveLine.objects.create(event=self.event,description="SYNTHETIC distributed",expense_date=self.monday,unit_value=10,quantity=6,reason="SYNTHETIC",request_key=uuid.uuid4())
        CompensationAdjustment.objects.create(event=self.event,staff=self.staff,occurred_on=self.monday,kind="incentive",amount=15,reason="SYNTHETIC",reference="SYNTHETIC unique adjustment",operation_id=uuid.uuid4(),recorded_by=self.admin)
        cost=metrics.event_finances(self.admin,self.event);self.assertEqual(cost["recorded"],215);self.assertEqual(cost["excluded"],600)
    def test_cost_ratios_require_reconciliation(self):
        self.person();self.day();self.assertIsNone(metrics.event_detail(self.admin,self.event)["cost_per_lead"])
        self.reconcile();self.assertEqual(metrics.event_detail(self.admin,self.event)["cost_per_lead"],120)
    def test_no_automatic_finalization_authority_for_administrator(self):
        self.admin.can_finalize_event_reports=False;self.admin.save()
        with self.assertRaises(PermissionDenied):services.finalize_event(actor=self.admin,event_id=self.event.pk,expected_revision=0,why="SYNTHETIC")
    def test_finalized_versions_retain_threshold_and_old_cost(self):
        self.day();self.reconcile();v=services.finalize_event(actor=self.admin,event_id=self.event.pk,expected_revision=0,why="SYNTHETIC finalization")
        self.event.simulator_qualification_threshold=75;self.event.save()
        v.refresh_from_db();self.assertEqual(Decimal(v.snapshot["threshold"]),Decimal("50"))
        with self.assertRaises(ValidationError):services.reopen_event(actor=self.admin,event_id=self.event.pk,expected_revision=1,why="")
        services.reopen_event(actor=self.admin,event_id=self.event.pk,expected_revision=1,why="SYNTHETIC correction")
        second=services.finalize_event(actor=self.admin,event_id=self.event.pk,expected_revision=2,why="SYNTHETIC refinalization")
        self.assertEqual(second.number,2);self.assertEqual(EventReportVersion.objects.count(),2);self.assertEqual(Decimal(v.snapshot["threshold"]),50);self.assertEqual(Decimal(second.snapshot["threshold"]),75)
    def test_snapshot_mutation_and_deletion_rejected(self):
        v=services.finalize_event(actor=self.admin,event_id=self.event.pk,expected_revision=0,why="SYNTHETIC")
        with self.assertRaises(ValidationError):v.save()
        with self.assertRaises(ValidationError):v.delete()
        with self.assertRaises(ValidationError):EventReportVersion.objects.filter(pk=v.pk).update(snapshot={})
    def test_manual_figure_edit_audit_retains_original_provenance(self):
        from apps.core.models import AuditLog
        data={"label":"Partner booths","value":3,"unit":"booths","source_note":"SYNTHETIC venue signoff A","reason":"SYNTHETIC creation","revision":0}
        figure=services.save_manual_figure(actor=self.admin,event_id=self.event.pk,data=data)
        data.update(value=4,source_note="SYNTHETIC venue signoff B",reason="SYNTHETIC correction",revision=1)
        services.save_manual_figure(actor=self.admin,event_id=self.event.pk,figure_id=figure.pk,data=data)
        audit=AuditLog.objects.filter(action="reporting.manual_business_figure.saved",object_id=str(figure.pk)).latest("timestamp")
        self.assertEqual(audit.old_data["source_note"],"SYNTHETIC venue signoff A");self.assertEqual(audit.new_data["source_note"],"SYNTHETIC venue signoff B")
        self.assertEqual(audit.old_data["unit"],"booths")
    def test_manual_business_figure_cannot_replace_calculated_counts(self):
        for label in ["Total Leads","Simulator Plays","Qualified Leads","Event Cost"]:
            with self.assertRaises(ValidationError):services.save_manual_figure(actor=self.admin,event_id=self.event.pk,data={"label":label,"value":99,"unit":"count","source_note":"SYNTHETIC","reason":"SYNTHETIC","revision":0})
