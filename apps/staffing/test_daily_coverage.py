"""Two-category staffing coverage, audited edits and populated upgrade checks."""
from datetime import UTC, datetime, date
from importlib import import_module
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from apps.core.models import AuditLog
from .models import StaffProfile, StaffAssignment, AssignmentDay, DailyStaffingNeed
from .forms import PlanForm, AssignmentForm, DecisionForm
from .calendar import event_days, day_window
from . import tests as legacy_tests
from . import services as svc


class DailyCoverageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        legacy_tests.Staffing9CTests.setUpTestData.__func__(cls)

    assignment=legacy_tests.Staffing9CTests.assignment
    plan_data=legacy_tests.Staffing9CTests.plan_data
    request=legacy_tests.Staffing9CTests.request
    decision=legacy_tests.Staffing9CTests.decision

    def setUp(self):
        self.client.force_login(self.manager,backend="django.contrib.auth.backends.ModelBackend")

    def needs(self,staff=0,drivers=0):
        self.plan.required_staff=staff
        self.plan.required_drivers=drivers
        self.plan.save()

    def day(self,value="2030-07-01"):
        return next(d for d in svc.coverage(self.event)["days"] if d["date"].isoformat()==value)

    def test_lead_counts_as_staff_and_combined_as_driver_once(self):
        self.needs(staff=1,drivers=1)
        self.assignment(coverage_role="staff_lead")
        self.assignment(person=self.other_person,coverage_role="combined")
        day=self.day()
        self.assertEqual([(r["key"],r["confirmed"]) for r in day["roles"]],[("staff",1),("driver",1)])
        self.assertEqual(day["confirmed"],2)
        self.assertTrue(day["fulfilled"])

    def test_combined_alone_does_not_fill_staff(self):
        self.needs(staff=1,drivers=1)
        self.assignment(coverage_role="combined")
        day=self.day()
        self.assertEqual([r["remaining"] for r in day["roles"]],[1,0])
        self.assertFalse(day["fulfilled"])

    def test_excess_staff_cannot_fill_driver_requirement(self):
        self.needs(staff=1,drivers=1)
        self.assignment(coverage_role="staff")
        self.assignment(person=self.other_person,coverage_role="staff_lead")
        self.assertEqual(self.day()["unfilled"],1)
        self.assertFalse(self.day()["fulfilled"])

    def test_only_requested_buckets_are_displayed(self):
        self.needs(drivers=1)
        self.assignment(coverage_role="combined")
        self.assertEqual([r["key"] for r in self.day()["roles"]],["driver"])
        self.assertTrue(self.day()["fulfilled"])

    def test_no_needs_is_neutral_even_when_someone_is_assigned(self):
        self.needs()
        self.assignment(coverage_role="staff")
        self.assertEqual(self.day()["roles"],[])
        self.assertFalse(self.day()["fulfilled"])

    def test_daily_zero_overrides_default_for_both_roles(self):
        self.needs(staff=3,drivers=2)
        DailyStaffingNeed.objects.create(plan=self.plan,date=date(2030,7,1),required_staff=0,required_drivers=0)
        self.assertEqual(self.day()["roles"],[])
        self.assertEqual(self.day("2030-07-02")["required"],5)

    def test_unknown_legacy_role_never_claims_coverage(self):
        self.needs(staff=1)
        self.assignment(role="Specialist")
        day=self.day()
        self.assertEqual(day["unclassified"],1)
        self.assertEqual(day["confirmed"],1)
        self.assertEqual(day["roles"][0]["confirmed"],0)
        self.assertFalse(day["fulfilled"])

    def test_missing_legacy_role_field_maps_only_known_labels(self):
        a=self.assignment(role="Staff lead")
        self.assertEqual(a.coverage_role,"staff_lead")
        b=self.assignment(person=self.other_person,role="Driver + Staff")
        self.assertEqual(b.coverage_role,"combined")

    def test_pending_requests_never_fill_a_role(self):
        self.needs(staff=1,drivers=1)
        self.request()
        self.assertEqual(self.day()["unfilled"],2)
        self.assertEqual(self.day()["pending"],1)

    def test_revised_role_preserves_prior_role_and_reason(self):
        self.needs(staff=1,drivers=1)
        old=self.assignment(coverage_role="staff",role="SYNTHETIC responsibility")
        revised=self.assignment(old=old,coverage_role="combined",role=old.role)
        old.refresh_from_db()
        self.assertEqual(old.status,"replaced")
        self.assertEqual(old.coverage_role,"staff")
        self.assertEqual(revised.coverage_role,"combined")
        self.assertEqual(revised.role,old.role)
        self.assertEqual(revised.supersedes_id,old.pk)
        self.assertEqual(self.day()["confirmed"],1)
        log=AuditLog.objects.get(action="staffing.assignment.revised",object_id=str(revised.pk))
        self.assertEqual(log.old_data["coverage_role"],"staff")
        self.assertEqual(log.new_data["coverage_role"],"combined")

    def test_edit_from_older_client_preserves_existing_role(self):
        old=self.assignment(coverage_role="driver",role="SYNTHETIC logistics")
        revised=self.assignment(old=old,role="Updated logistics")
        self.assertEqual(revised.coverage_role,"driver")

    def test_removed_or_replaced_records_do_not_fill_needs(self):
        self.needs(staff=1)
        a=self.assignment(coverage_role="staff")
        svc.remove_assignment(actor=self.manager,assignment_id=a.pk,data={
            "revision":a.updated_at.isoformat(),"confirm":"on","reason":"SYNTHETIC removal"})
        self.assertEqual(self.day()["roles"][0]["confirmed"],0)
        self.assertEqual(a.days.count(),1)

    def test_request_approval_records_selected_role(self):
        self.needs(drivers=1)
        record=self.request()
        self.decision(record,coverage_role="combined")
        assignment=StaffAssignment.objects.get(source_request=record)
        self.assertEqual(assignment.coverage_role,"combined")
        self.assertTrue(self.day()["fulfilled"])

    def test_invalid_role_is_rejected_without_assignment(self):
        data={"revision":"new","staff":str(self.person.pk),"role":"","coverage_role":"pilot",
              "reason":"SYNTHETIC","day_2030-07-01":"on"}
        saved,form=svc.save_assignment(actor=self.manager,event_id=self.event.pk,data=data)
        self.assertIsNone(saved)
        self.assertIn("coverage_role",form.errors)
        self.assertFalse(StaffAssignment.objects.exists())

    def test_approval_only_exposes_coverage_role(self):
        self.assertIn("coverage_role",DecisionForm(approve=True).fields)
        self.assertNotIn("coverage_role",DecisionForm().fields)

    def test_older_plan_submission_preserves_driver_defaults_and_daily_values(self):
        self.needs(staff=2,drivers=3)
        DailyStaffingNeed.objects.create(plan=self.plan,date=date(2030,7,1),required_staff=2,required_drivers=1)
        saved,form=svc.save_plan(actor=self.manager,event_id=self.event.pk,data=self.plan_data())
        self.assertIsNotNone(saved,form.errors)
        self.assertEqual(saved.required_drivers,3)
        self.assertEqual(saved.daily_needs.get(date=date(2030,7,1)).required_drivers,1)
        self.assertEqual(saved.daily_needs.get(date=date(2030,7,2)).required_drivers,3)

    def test_daily_driver_settings_are_audited_and_zero_is_explicit(self):
        data=self.plan_data(required_drivers=2,drivers_2030_07_01=0)
        data["drivers_2030-07-01"]=0
        data["drivers_2030-07-02"]=1
        saved,form=svc.save_plan(actor=self.manager,event_id=self.event.pk,data=data)
        self.assertIsNotNone(saved,form.errors)
        self.assertEqual(saved.daily_needs.get(date=date(2030,7,1)).required_drivers,0)
        log=AuditLog.objects.filter(action="staffing.daily_needs.updated").latest("timestamp")
        row=next(d for d in log.new_data["days"] if d["date"]=="2030-07-02")
        self.assertEqual(row["required_drivers"],1)

    def test_negative_driver_needs_are_rejected(self):
        data=self.plan_data(required_drivers=-1)
        saved,form=svc.save_plan(actor=self.manager,event_id=self.event.pk,data=data)
        self.assertIsNone(saved)
        self.assertIn("required_drivers",form.errors)

    def test_green_and_shortage_states_have_text_and_no_aggregate_totals(self):
        self.needs(staff=1,drivers=1)
        self.assignment(coverage_role="staff_lead")
        self.assignment(person=self.other_person,coverage_role="combined")
        response=self.client.get(reverse("staffing:event",args=[self.event.pk]))
        self.assertContains(response,"coverage-complete")
        self.assertContains(response,"Fully staffed")
        self.assertContains(response,"1 needed")
        self.assertNotContains(response,"required staff-days")
        self.assertNotContains(response,"event-coverage-totals")
        self.assertContains(response,"Times and assignment details")

    def test_no_needs_and_unclassified_review_are_visible(self):
        self.needs()
        self.assignment(role="SYNTHETIC specialist")
        response=self.client.get(reverse("staffing:event",args=[self.event.pk]))
        self.assertContains(response,"No needs set")
        self.assertContains(response,"Needs role review")
        self.assertNotContains(response,"✓ Fully staffed")

    def test_needs_editor_and_approval_keep_event_context(self):
        response=self.client.get(reverse("staffing:plan-edit",args=[self.event.pk]))
        self.assertContains(response,"event-needs-grid")
        self.assertContains(response,'name="drivers_2030-07-01"')
        record=self.request()
        response=self.client.get(reverse("staffing:request-decision",args=[record.pk,"approve"]))
        self.assertContains(response,'name="coverage_role"')
        self.assertContains(response,self.event.name)


class DailyCoverageUpgradeTests(TransactionTestCase):
    """Exercise the actual migration against populated historical tables."""
    def test_upgrade_preserves_descriptive_roles_days_and_needs(self):
        executor=MigrationExecutor(connection)
        latest=executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([("staffing","0001_initial")])
            old_apps=executor.loader.project_state([("staffing","0001_initial")]).apps
            Event=old_apps.get_model("events","Event")
            Profile=old_apps.get_model("staffing","StaffProfile")
            Plan=old_apps.get_model("staffing","StaffingPlan")
            Need=old_apps.get_model("staffing","DailyStaffingNeed")
            Assignment=old_apps.get_model("staffing","StaffAssignment")
            Day=old_apps.get_model("staffing","AssignmentDay")
            event=Event.objects.create(name="SYNTHETIC populated upgrade",start_at=datetime(2031,1,1,9,tzinfo=UTC),
                end_at=datetime(2031,1,1,17,tzinfo=UTC),timezone_name="UTC",status="upcoming")
            plan=Plan.objects.create(event=event,required_staff=4)
            need=Need.objects.create(plan=plan,date=date(2031,1,1),required_staff=2)
            records=[]
            for i,role in enumerate(("Staff","Staff lead","Driver","Combined","Event staff","SYNTHETIC specialist","")):
                person=Profile.objects.create(first_name="SYNTHETIC",last_name=f"Upgrade {i}")
                a=Assignment.objects.create(event=event,staff=person,role=role)
                Day.objects.create(assignment=a,date=date(2031,1,1),starts_at=event.start_at,ends_at=event.end_at)
                records.append((a.pk,role))
            executor=MigrationExecutor(connection)
            executor.migrate(latest)
            self.assertEqual(DailyStaffingNeed.objects.get(pk=need.pk).required_staff,2)
            self.assertEqual(DailyStaffingNeed.objects.get(pk=need.pk).required_drivers,0)
            expected=["staff","staff_lead","driver","combined","staff","",""]
            for (pk,original),coverage in zip(records,expected):
                saved=StaffAssignment.objects.get(pk=pk)
                self.assertEqual(saved.role,original)
                self.assertEqual(saved.coverage_role,coverage)
                self.assertEqual(saved.days.count(),1)
            # Re-running classification is safe and does not overwrite a manager's new selection.
            saved.coverage_role="driver"; saved.save()
            import_module("apps.staffing.migrations.0002_daily_role_coverage").classify_known_roles(
                executor.loader.project_state(latest).apps,connection.schema_editor())
            saved.refresh_from_db()
            self.assertEqual(saved.coverage_role,"driver")
        finally:
            MigrationExecutor(connection).migrate(latest)
