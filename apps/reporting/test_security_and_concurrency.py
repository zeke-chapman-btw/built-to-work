import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,UTC,timedelta
from threading import Barrier
from django.test import TestCase,TransactionTestCase,skipUnlessDBFeature
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group,Permission
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import close_old_connections
from django.urls import reverse
from apps.accounts.roles import role_configuration,ROLE_GROUPS
from apps.accounts.staff_services import save_account,revision
from apps.accounts.staff_forms import StaffAccountForm
from apps.recruiting.test_workflow import WorkflowFixtures
from apps.recruiting import workflow
from apps.events.models import Event
from . import metrics,services,payroll
from .models import EventReportVersion,PayrollVersion
from .test_metrics import ReportingFixtures

class ReportingDesignationTests(ReportingFixtures):
    def test_staff_form_and_explicit_grant_revoke_preserve_unrelated_permission(self):
        role_configuration(apply=True)
        target=get_user_model().objects.create_user('synthetic-report-manager',email='manager@example.test',is_staff=True)
        target.groups.add(Group.objects.get(name=ROLE_GROUPS['manager']))
        keep=Permission.objects.get(content_type__app_label='events',codename='view_event');target.user_permissions.add(keep)
        data=dict(username=target.username,email=target.email,first_name='SYNTHETIC',last_name='Manager',roles=['manager'],confirm_identity=True,reason='SYNTHETIC report designation',revision=revision(target),is_active=True,can_finalize_event_reports=True,can_approve_weekly_payroll=False)
        saved=save_account(actor=self.admin,user_id=target.pk,data=data)
        self.assertTrue(saved.can_finalize_event_reports);self.assertTrue(saved.has_perm('reporting.finalize_event_reports'));self.assertTrue(saved.user_permissions.filter(pk=keep.pk).exists())
        self.assertNotIn('can_finalize_event_reports',StaffAccountForm(actor=saved).fields)
        data.update(revision=revision(saved),can_finalize_event_reports=False)
        saved=save_account(actor=self.admin,user_id=saved.pk,data=data)
        self.assertFalse(saved.can_finalize_event_reports);self.assertFalse(saved.user_permissions.filter(codename='finalize_event_reports').exists());self.assertTrue(saved.user_permissions.filter(pk=keep.pk).exists())
    def test_manager_and_ineligible_staff_cannot_self_designate(self):
        role_configuration(apply=True)
        actor=get_user_model().objects.create_user('synthetic-manager-forge',email='forge@example.test',is_staff=True);actor.groups.add(Group.objects.get(name=ROLE_GROUPS['manager']))
        data=dict(username='synthetic-flag-forge',email='new@example.test',roles=['event_staff'],confirm_identity=True,reason='SYNTHETIC',can_finalize_event_reports=True)
        with self.assertRaises(PermissionDenied):save_account(actor=actor,data=data)
        with self.assertRaises(ValidationError):save_account(actor=self.admin,data=data)

class ReportingRecruitingTests(WorkflowFixtures,TestCase):
    def setUp(self):self.workflow_setup()
    def test_only_actual_confirmed_hire_counts_and_confirmation_date_is_used(self):
        candidate=self.select();self.assertEqual(metrics.confirmed_hires(self.admin,{}).count(),0)
        candidate=workflow.transition(actor=self.admin,candidate_id=candidate.pk,stage_key='hired',expected_revision=candidate.revision,why='SYNTHETIC confirmed hiring',confirm_hired=True)
        hires=metrics.confirmed_hires(self.admin,{});self.assertEqual(hires.count(),1);self.assertIsNotNone(hires.first().confirmed_on)
        self.assertEqual(metrics.confirmed_hires(self.admin,{'start':date(2100,1,1)}).count(),0)
        self.assertEqual(self.client.get(reverse('reporting:workspace'),{'tab':'hires'}).status_code,302)
    def test_customer_opportunity_filter_restricts_the_customer_population(self):
        from apps.customers.models import CustomerOrganization
        CustomerOrganization.objects.create(name="SYNTHETIC unrelated report customer")
        customers,needs,counts=metrics.customer_projection(self.admin,{"opportunity":self.opportunity})
        self.assertEqual(set(customers.values_list("pk",flat=True)),{self.need.organization_id})
    def test_recruiter_cannot_report_unassigned_employer_opportunity(self):
        role_configuration(apply=True)
        user=get_user_model().objects.create_user('synthetic-restricted-report-recruiter',is_staff=True);user.groups.add(Group.objects.get(name=ROLE_GROUPS['recruiter']))
        self.assertEqual(metrics.recruiting_cohort(user,{}).count(),0)
        self.client.force_login(user,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(reverse('reporting:workspace'),{'tab':'opportunities'});self.assertEqual(response.status_code,200);self.assertNotContains(response,self.need.organization.name)
        self.assertEqual(self.client.get(reverse('reporting:workspace'),{'tab':'customers'}).status_code,403)

@skipUnlessDBFeature('has_select_for_update')
class ReportingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor=get_user_model().objects.create_superuser('synthetic-concurrent-report-admin',password='synthetic-only-test',can_finalize_event_reports=True,can_approve_weekly_payroll=True)
        now=datetime(2026,11,2,12,tzinfo=UTC);self.event=Event.objects.create(name='SYNTHETIC concurrent report',start_at=now,end_at=now+timedelta(hours=2))
    def compete(self,operation):
        barrier=Barrier(2);actor_id=self.actor.pk
        def work():
            close_old_connections();actor=get_user_model().objects.get(pk=actor_id);barrier.wait(timeout=10)
            try:operation(actor);return 'created'
            except ValidationError:return 'stale'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:work(),range(2)))
        self.assertEqual(sorted(results),['created','stale'])
    def test_concurrent_event_finalization_has_one_version_and_stale_rejection(self):
        self.compete(lambda actor:services.finalize_event(actor=actor,event_id=self.event.pk,expected_revision=0,why='SYNTHETIC concurrent finalization'))
        self.assertEqual(EventReportVersion.objects.count(),1)
    def test_concurrent_payroll_compilation_has_one_version_and_stale_rejection(self):
        self.compete(lambda actor:payroll.compile_payroll(actor=actor,starts_on=date(2026,11,2),expected_revision=0,why='SYNTHETIC concurrent compilation'))
        self.assertEqual(PayrollVersion.objects.count(),1)
