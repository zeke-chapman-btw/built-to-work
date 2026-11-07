import io,uuid
from datetime import timedelta,date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission,Group
from django.core.exceptions import PermissionDenied,ValidationError
from django.urls import reverse
from .test_metrics import ReportingFixtures
from . import payroll,services,exports,definitions
from .models import PayrollPeriod,PayrollVersion,PayrollApproval,PayrollSubmission,ContractorStatement,CompensationAdjustment
from .forms import PayrollStartForm,ReportFilter
from .access import can_finalize,can_approve_payroll

class PayrollWorkflowTests(ReportingFixtures):
    def compile(self):return payroll.compile_payroll(actor=self.admin,starts_on=self.monday,why="SYNTHETIC compilation")
    def approve(self):return payroll.approve_payroll(actor=self.admin,starts_on=self.monday,expected_revision=1,why="SYNTHETIC approval")
    def test_monday_sunday_source_boundaries_and_no_assignment_pay(self):
        self.day();self.day(work_date=self.monday+timedelta(days=6),effective_checkin_at=self.when+timedelta(days=6),pay_amount=50,per_diem_amount=0)
        self.day(work_date=self.monday+timedelta(days=7),effective_checkin_at=self.when+timedelta(days=7),pay_amount=999)
        data,fp=payroll.compile_data(self.monday);self.assertEqual(data['totals']['total'],'170.00');self.assertEqual(len(data['rows']),2)
        self.assertFalse(PayrollStartForm({'starts_on':'2026-11-03','reason':'SYNTHETIC'}).is_valid())
        with self.assertRaises(ValidationError):PayrollPeriod.objects.create(starts_on=self.monday+timedelta(days=1))
    def test_pending_daily_approval_blocks_weekly_approval(self):
        self.day(approval='pending');self.compile()
        with self.assertRaises(ValidationError):self.approve()
    def test_missing_rate_is_unavailable_not_zero_in_totals_and_exports(self):
        self.day(approval="pending",pay_amount=None);version=self.compile();data=version.snapshot
        self.assertIsNone(data["rows"][0]["total"]);self.assertIsNone(data["totals"]["total"]);self.assertFalse(data["ready"])
        self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")
        for kind in ("pdf","xlsx","csv"):
            self.assertEqual(self.client.get(reverse("reporting:payroll",args=[self.monday.isoformat()]),{"format":kind}).status_code,200)
        with self.assertRaises(ValidationError):self.approve()
    def test_explicit_admin_designation_required(self):
        self.day();self.compile();self.admin.can_approve_weekly_payroll=False;self.admin.save()
        with self.assertRaises(PermissionDenied):self.approve()
        self.assertEqual(PayrollApproval.objects.count(),0)
    def test_source_change_blocks_stale_approval(self):
        d=self.day();self.compile();d.pay_amount=125;d.save()
        with self.assertRaises(ValidationError):self.approve()
    def test_approval_creates_private_immutable_projection(self):
        self.day();self.day(staff=self.other_staff,pay_amount=777);v=self.compile();approval=self.approve()
        own=payroll.own_statement(self.worker,v.pk);self.assertEqual(own['totals']['total'],'120.00');self.assertEqual(len(own['rows']),1)
        self.assertNotIn('Contractor Two',str(own));self.assertEqual(ContractorStatement.objects.count(),2)
        self.assertEqual(payroll.earnings_year(self.worker,2026)['total'],120)
        with self.assertRaises(PermissionDenied):payroll.own_statement(self.worker,uuid.uuid4())
        with self.assertRaises(ValidationError):v.save()
    def test_reopen_recompile_reapprove_preserves_history_and_latest_year_totals(self):
        d=self.day();v1=self.compile();self.approve()
        payroll.submit_payroll(actor=self.admin,starts_on=self.monday,expected_revision=2,why='SYNTHETIC manual send',reference='SYNTHETIC AP receipt',operation_id=uuid.uuid4())
        with self.assertRaises(ValidationError):payroll.reopen_payroll(actor=self.admin,starts_on=self.monday,expected_revision=2,why='')
        payroll.reopen_payroll(actor=self.admin,starts_on=self.monday,expected_revision=2,why='SYNTHETIC corrected rate')
        d.pay_amount=150;d.save();v2=payroll.compile_payroll(actor=self.admin,starts_on=self.monday,expected_revision=3,why='SYNTHETIC recompile')
        payroll.approve_payroll(actor=self.admin,starts_on=self.monday,expected_revision=4,why='SYNTHETIC reapprove')
        self.assertEqual(PayrollVersion.objects.count(),2);self.assertEqual(PayrollApproval.objects.count(),2)
        self.assertEqual(payroll.own_statement(self.worker,v1.pk)['totals']['total'],'120.00')
        self.assertEqual(payroll.earnings_year(self.worker,2026)['total'],170)
        self.assertIn('resubmission',payroll.submission_status(PayrollPeriod.objects.get(pk=self.monday)))
    def test_submission_idempotency_and_no_automatic_mail(self):
        from django.core import mail
        self.day();self.compile();self.approve();op=uuid.uuid4();args=dict(actor=self.admin,starts_on=self.monday,expected_revision=2,why='SYNTHETIC manual submission',reference='SYNTHETIC',operation_id=op)
        a=payroll.submit_payroll(**args);b=payroll.submit_payroll(**args);self.assertEqual(a.pk,b.pk);self.assertEqual(PayrollSubmission.objects.count(),1);self.assertEqual(len(mail.outbox),0)
    def test_ytd_cutoff_and_year_boundaries(self):
        self.day();self.day(work_date=self.monday+timedelta(days=6),effective_checkin_at=self.when+timedelta(days=6),pay_amount=50,per_diem_amount=0);self.compile();self.approve()
        self.assertEqual(payroll.earnings_year(self.worker,2026,through=self.monday)['total'],120);self.assertEqual(payroll.earnings_year(self.worker,2025)['total'],0)
    def test_adjustment_match_void_and_one_source(self):
        d=self.day();data=dict(event=self.event,staff=self.staff,workday=d,occurred_on=self.monday,kind='incentive',amount=Decimal('15'),reason='SYNTHETIC',reference='SYNTHETIC unique source',operation_id=uuid.uuid4())
        a=services.add_adjustment(actor=self.admin,data=data)
        with self.assertRaises(ValidationError):services.add_adjustment(actor=self.admin,data=data)
        self.assertEqual(payroll.compile_data(self.monday)[0]['totals']['total'],'135.00')
        services.void_adjustment(actor=self.admin,adjustment_id=a.pk,expected_revision=0,why='SYNTHETIC correction')
        self.assertEqual(payroll.compile_data(self.monday)[0]['totals']['total'],'120.00');self.assertEqual(CompensationAdjustment.objects.count(),1)
    def test_payroll_directory_does_not_load_full_weekly_row_payloads(self):
        from django.test import RequestFactory
        from .presentation import workspace_data
        self.day();self.compile();self.approve()
        data=workspace_data(self.admin,"payroll",{},RequestFactory().get("/internal/reports/"))
        self.assertEqual(Decimal(str(data["report_rows"][0]["total"])),120)
        self.assertIn("snapshot",list(data["page"].object_list)[0].current_version.get_deferred_fields())
    def test_source_change_after_approval_blocks_ap_submission(self):
        d=self.day();self.compile();self.approve();d.pay_amount=999;d.save()
        with self.assertRaises(ValidationError):payroll.submit_payroll(actor=self.admin,starts_on=self.monday,expected_revision=2,why='SYNTHETIC',reference='SYNTHETIC',operation_id=uuid.uuid4())

class ReportingHTTPTests(ReportingFixtures):
    def setUp(self):self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
    def test_all_seven_tabs_and_filter_errors(self):
        self.person();self.day()
        for tab in ['highlights','events','payroll','participants','customers','opportunities','hires']:
            with self.subTest(tab=tab):
                response=self.client.get(reverse('reporting:workspace'),{'tab':tab});self.assertEqual(response.status_code,200);self.assertContains(response,'Reports &amp; Analytics')
        r=self.client.get(reverse('reporting:workspace'),{'tab':'events','start':'2026-11-05','end':'2026-11-01'});self.assertContains(r,'Through must be on or after From')
        self.assertEqual(self.client.get(reverse('reporting:workspace'),{'tab':'events','start':'bad','format':'csv'}).status_code,400)
    def test_approved_interest_mapping_and_recorded_preference_drilldown(self):
        self.assertEqual(definitions.INTEREST_MAPPING,{"seeking":"yes","open":"maybe","not_seeking":"no"})
        for interest,score in [("seeking","50"),("open","70"),("not_seeking","100"),("","99")]:
            person,registration,session=self.person(interest);self.capture(session,score)
        from .metrics import event_metrics
        from apps.events.models import Event
        result=event_metrics(self.admin,Event.objects.filter(pk=self.event.pk))[0]
        self.assertEqual((result["interested"],result["qualified"]),(2,2))
        response=self.client.get(reverse("reporting:event-people",args=[self.event.pk]));self.assertContains(response,"Seeking work");self.assertContains(response,"Approved Yes/Maybe/No mapping")
    def test_kpi_drilldown_preserves_the_selected_date_cohort(self):
        from datetime import timedelta
        from urllib.parse import urlsplit,parse_qs
        from apps.events.models import Event,EventRegistration
        from apps.participants.models import Participant
        self.person();other=Event.objects.create(name="SYNTHETIC later event",start_at=self.when+timedelta(days=40),end_at=self.when+timedelta(days=41))
        person=Participant.objects.create(first_name="SYNTHETIC",last_name="Later acquisition");EventRegistration.objects.create(event=other,participant=person)
        response=self.client.get(reverse("reporting:workspace"),{"start":"2026-11-01","end":"2026-11-30"})
        card=next(c for c in response.context["cards"] if c["label"]=="Unique event leads")
        self.assertEqual(card["value"],1);query=parse_qs(urlsplit(card["url"]).query);self.assertEqual(query["start"],["2026-11-01"])
        detail=self.client.get(card["url"]);self.assertEqual(next(c["value"] for c in detail.context["cards"] if c["label"]=="Unique event leads"),1)
    def test_event_financial_category_and_workday_detail(self):
        from apps.event_reports.models import ManualExpense,ExpenseCategory
        self.day();category=ExpenseCategory.objects.get(name="Fuel")
        ManualExpense.objects.create(event=self.event,description="SYNTHETIC category proof",expense_date=self.monday,category=category,category_name="Fuel",amount=20,treatment="cost",reason="SYNTHETIC",request_key=uuid.uuid4())
        self.reconcile();response=self.client.get(reverse("reporting:event",args=[self.event.pk]))
        self.assertContains(response,"Fuel");self.assertContains(response,"Base $100.00");self.assertContains(response,"Per diem $20.00")
        self.assertEqual(Decimal(str(response.context["cost"]["expense_categories"][0]["amount"])),20)
        self.assertEqual(Decimal(str(response.context["cost"]["total"])),140)
    def test_event_detail_people_and_manual_figure_forms(self):
        self.person();self.day()
        for name in ['reporting:event','reporting:event-people','reporting:figure-new']:
            self.assertEqual(self.client.get(reverse(name,args=[self.event.pk])).status_code,200)
    def test_event_excel_cost_cells_remain_numeric_currency(self):
        from openpyxl import load_workbook
        self.day();self.reconcile()
        response=self.client.get(reverse("reporting:event",args=[self.event.pk]),{"format":"xlsx"})
        self.assertEqual(response.status_code,200);sheet=load_workbook(io.BytesIO(response.content)).active
        row=next(row for row in sheet.iter_rows() if row[0].value=="Recorded event cost USD")
        self.assertEqual(row[1].value,120);self.assertIn("0.00",row[1].number_format)
    def test_payroll_and_private_statement_routes(self):
        self.day();v=payroll.compile_payroll(actor=self.admin,starts_on=self.monday,why='SYNTHETIC');payroll.approve_payroll(actor=self.admin,starts_on=self.monday,expected_revision=1,why='SYNTHETIC')
        for name,args in [('reporting:payroll-start',[]),('reporting:adjustment-new',[]),('reporting:payroll',[self.monday.isoformat()])]:
            self.assertEqual(self.client.get(reverse(name,args=args)).status_code,200)
        self.client.force_login(self.worker,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('reporting:my-pay')).status_code,200);self.assertEqual(self.client.get(reverse('reporting:my-statement',args=[v.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('reporting:workspace'),{'tab':'payroll'}).status_code,403)
        self.client.force_login(self.other,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('reporting:my-statement',args=[v.pk])).status_code,403)
    def test_historical_versions_reject_post(self):
        v=services.finalize_event(actor=self.admin,event_id=self.event.pk,expected_revision=0,why='SYNTHETIC')
        url=reverse('reporting:event',args=[self.event.pk])+'?version='+str(v.pk)
        self.assertEqual(self.client.post(url,{'action':'reopen','revision':1,'reason':'SYNTHETIC'}).status_code,400)
        self.assertNotContains(self.client.get(url),'name="action"')
    def test_exports_use_same_frozen_payroll_and_private_scope(self):
        from openpyxl import load_workbook
        from pypdf import PdfReader
        self.day();self.day(staff=self.other_staff,pay_amount=777);v=payroll.compile_payroll(actor=self.admin,starts_on=self.monday,why='SYNTHETIC');payroll.approve_payroll(actor=self.admin,starts_on=self.monday,expected_revision=1,why='SYNTHETIC')
        self.client.force_login(self.worker,backend='django.contrib.auth.backends.ModelBackend');url=reverse('reporting:my-statement',args=[v.pk])
        for fmt in ['csv','xlsx','pdf']:
            r=self.client.get(url,{'format':fmt});self.assertEqual(r.status_code,200)
            content=b''.join(r.streaming_content) if r.streaming else r.content
            if fmt=='csv':self.assertIn(b'120.00',content);self.assertNotIn(b'Contractor Two',content)
            elif fmt=='xlsx':
                book=load_workbook(io.BytesIO(content));values=list(book.active.values);self.assertIn(120,[v for row in values for v in row]);self.assertNotIn('SYNTHETIC Contractor Two',str(values))
            else:self.assertEqual(len(PdfReader(io.BytesIO(content)).pages),1)
    def test_non_staff_and_no_source_permissions_are_denied(self):
        U=get_user_model();outsider=U.objects.create_user('synthetic-outside',password='synthetic-test-password');self.client.force_login(outsider,backend='django.contrib.auth.backends.ModelBackend')
        for path in [reverse('reporting:workspace'),reverse('reporting:event',args=[self.event.pk]),reverse('reporting:my-pay')]:self.assertEqual(self.client.get(path).status_code,403)
    def test_pdf_long_report_paginates_and_retains_all_amounts(self):
        from pypdf import PdfReader
        rows=[{"label":"SYNTHETIC record "+str(i),"amount":Decimal("12.34")} for i in range(40)]
        response=exports.export_response(actor=self.admin,format="pdf",title="SYNTHETIC paginated report",columns=[("label","Source","text"),("amount","Amount USD","money")],rows=rows,meta=exports.metadata("SYNTHETIC",{}))
        reader=PdfReader(io.BytesIO(response.content));self.assertGreater(len(reader.pages),1)
        text="".join(p.extract_text() for p in reader.pages);self.assertEqual(text.count("$12.34"),40)
    def test_export_permission_is_separate_from_staff_access(self):
        self.client.force_login(self.worker,backend='django.contrib.auth.backends.ModelBackend');r=self.client.get(reverse('reporting:workspace'),{'format':'csv'});self.assertEqual(r.status_code,403)
    def test_csv_formula_injection_and_xlsx_numeric_currency(self):
        columns=[('label','Label','text'),('amount','Amount USD','money')];rows=[{'label':'=HYPERLINK("bad")','amount':Decimal('-12.30')}];meta=exports.metadata('SYNTHETIC',{})
        r=exports.export_response(actor=self.admin,format='csv',title='SYNTHETIC',columns=columns,rows=rows,meta=meta);body=b''.join(r.streaming_content).decode();self.assertNotIn('\n=HYPERLINK',body);self.assertIn('-12.30',body)
    def test_unknown_format_and_malformed_history_parameters(self):
        self.assertEqual(self.client.get(reverse("reporting:workspace"),{"format":"unknown"}).status_code,400)
        self.assertEqual(self.client.get(reverse("reporting:event",args=[self.event.pk]),{"version":"invalid"}).status_code,404)
        payroll.compile_payroll(actor=self.admin,starts_on=self.monday,why="SYNTHETIC")
        self.assertEqual(self.client.get(reverse("reporting:payroll",args=[self.monday.isoformat()]),{"version":"invalid"}).status_code,404)
    def test_nondesignated_flags_default_false(self):
        user=get_user_model().objects.create_superuser('synthetic-new-admin','new@example.test','synthetic-test-password')
        self.assertFalse(can_finalize(user));self.assertFalse(can_approve_payroll(user))


class PortablePayrollConstraintTests(TestCase):
    def test_database_keeps_monday_rule_when_model_save_is_bypassed(self):
        from django.db import IntegrityError, transaction
        monday = date(2030, 7, 1)
        self.assertEqual(monday.weekday(), 0)
        PayrollPeriod.objects.bulk_create([PayrollPeriod(starts_on=monday)])
        with self.assertRaises(IntegrityError), transaction.atomic():
            PayrollPeriod.objects.bulk_create([PayrollPeriod(starts_on=monday + timedelta(days=1))])

    def test_sqlite_integrity_check_needs_no_django_date_functions(self):
        import sqlite3
        from django.db import connection
        if connection.vendor != "sqlite":
            self.skipTest("Raw SQLite integrity check; PostgreSQL enforcement is tested separately")
        with connection.cursor() as cursor:
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=%s", [PayrollPeriod._meta.db_table])
            schema = cursor.fetchone()[0]
        self.assertNotIn("django_date_extract", schema)
        with sqlite3.connect(":memory:") as restored:
            restored.execute(schema)
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
