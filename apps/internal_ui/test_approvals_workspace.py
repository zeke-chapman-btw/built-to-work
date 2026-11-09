"""Synthetic central-workspace tests; source services remain decision authorities."""
from datetime import timedelta
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.staffing import tests as staffing_cases
from apps.staffing.models import AvailabilityRequest, StaffAssignment, AssignmentDay
from apps.recruiting.test_employer import EmployerFixtures
from apps.recruiting.models import CandidateDisclosure, OpportunityCollaboration, CandidateOpportunity
from apps.participants.qualification_models import QualificationCategory, QualificationCatalogEntry, ParticipantQualification
from apps.participants.verification_models import ParticipantVerificationRecord
from apps.participants.verification_services import qualification_snapshot
from apps.customers.models import WorkforceNeed, WorkforceNeedRevision

URL = '/internal/workspaces/approvals/'
BACKEND = 'django.contrib.auth.backends.ModelBackend'


class StaffApprovalsHubTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        staffing_cases.Staffing9CTests.setUpTestData.__func__(cls)
    request = staffing_cases.Staffing9CTests.request
    decision = staffing_cases.Staffing9CTests.decision
    def setUp(self):
        self.client.force_login(self.manager,backend=BACKEND)
    def test_staff_requests_are_not_participant_registrations_and_get_has_no_side_effects(self):
        row=self.request()
        before={model._meta.label:model.objects.count()for model in [AvailabilityRequest,StaffAssignment,AssignmentDay,AuditLog,*apps.get_app_config('workdays').get_models()]}
        response=self.client.get(URL,{'tab':'events'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertContains(response,str(self.person))
        self.assertContains(response,'Requested days: 2030-07-01, 2030-07-02')
        self.assertContains(response,'not attendance or pay')
        self.assertContains(response,reverse('staffing:event',args=[self.event.pk]))
        self.assertEqual(before,{model._meta.label:model.objects.count()for model in [AvailabilityRequest,StaffAssignment,AssignmentDay,AuditLog,*apps.get_app_config('workdays').get_models()]})
        self.assertEqual(self.client.post(URL,{'tab':'events','decision':'approve'}).status_code,405)
        row.refresh_from_db();self.assertEqual(row.status,'pending')
    def test_existing_approval_confirms_days_and_history_without_creating_workdays(self):
        row=self.request()
        before={m._meta.label:m.objects.count()for m in apps.get_app_config('workdays').get_models()}
        saved,form=self.decision(row)
        self.assertIsNotNone(saved,form.errors)
        self.assertEqual(StaffAssignment.objects.filter(source_request=row,status='confirmed').count(),1)
        self.assertEqual(AssignmentDay.objects.filter(assignment__source_request=row).count(),2)
        self.assertEqual(before,{m._meta.label:m.objects.count()for m in apps.get_app_config('workdays').get_models()})
        response=self.client.get(URL,{'tab':'events','state':'approved'})
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertContains(response,'SYNTHETIC manager decision')
        self.assertTrue(AuditLog.objects.filter(action__startswith='staffing.request.').exists())
    def test_staffing_authority_does_not_reveal_other_categories(self):
        response=self.client.get(URL)
        self.assertEqual([t['label']for t in response.context['approval_tabs']],['Events'])
        for tab in ['verification','customer','jobs']:
            self.assertEqual(self.client.get(URL,{'tab':tab}).status_code,403)
    def test_ordinary_staff_and_inactive_sessions_cannot_read_hub(self):
        self.client.force_login(self.employee,backend=BACKEND)
        self.assertEqual(self.client.get(URL).status_code,403)
        get_user_model().objects.filter(pk=self.manager.pk).update(is_active=False)
        self.client.force_login(self.manager,backend=BACKEND)
        self.assertIn(self.client.get(URL).status_code,[302,403])
    def test_search_states_and_pagination_preserve_source_filters(self):
        for i in range(28):
            profile=type(self.person).objects.create(first_name='SYNTHETIC Scale',last_name=str(i))
            AvailabilityRequest.objects.create(event=self.event,staff=profile,requested_dates=['2030-07-01'])
        response=self.client.get(URL,{'tab':'events','q':'SYNTHETIC Scale','state':'pending','page':'2'})
        self.assertEqual(response.context['page'].paginator.count,28)
        self.assertEqual(len(response.context['rows']),3)
        self.assertIn('tab=events',response.context['previous_page_url'])
        self.assertIn('state=pending',response.context['previous_page_url'])
        self.assertIn('q=SYNTHETIC+Scale',response.context['previous_page_url'])


class DomainApprovalsHubTests(EmployerFixtures,TestCase):
    def setUp(self):
        self.employer_setup()
        self.client.force_login(self.admin,backend=BACKEND)
    def test_all_four_tabs_are_real_and_no_placeholder_remains(self):
        response=self.client.get(URL)
        self.assertEqual([t['label']for t in response.context['approval_tabs']],['Events','Verification','Customer Approval','Job Postings'])
        self.assertNotContains(response,'Approvals is planned')
        self.assertContains(response,'aria-current="page"')
        self.assertEqual(self.client.get(URL,{'tab':'unknown'}).status_code,404)
    def test_selection_is_not_a_pending_disclosure_request_and_no_get_releases(self):
        before=CandidateDisclosure.objects.count()
        response=self.client.get(URL,{'tab':'customer'})
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertContains(response,'Selected / not released')
        self.assertContains(response,'Review release')
        self.assertContains(response,reverse('recruiting:disclosure',args=[self.candidate.pk]))
        self.assertNotContains(response,'Pending request')
        self.assertEqual(CandidateDisclosure.objects.count(),before)
    def test_release_and_revocation_are_retained_history_not_assumed_current_access(self):
        grant=self.release()
        self.assertEqual(self.client.get(URL,{'tab':'customer'}).context['page'].paginator.count,0)
        history=self.client.get(URL,{'tab':'customer','state':'history'})
        self.assertContains(history,'Release recorded')
        from apps.recruiting import employer
        employer.revoke(actor=self.admin,disclosure_id=grant.pk,expected_revision=grant.revision,why='SYNTHETIC revoked release fixture')
        response=self.client.get(URL,{'tab':'customer','state':'revoked'})
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertContains(response,'Revoked')
    def test_read_only_recruiter_can_view_scoped_records_but_not_unavailable_release_actions(self):
        link=OpportunityCollaboration.objects.create(opportunity=self.opportunity,user=self.recruiter,granted_by=self.admin,can_manage=False)
        self.client.force_login(self.recruiter,backend=BACKEND)
        response=self.client.get(URL,{'tab':'customer'})
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertContains(response,'View candidate')
        self.assertNotContains(response,reverse('recruiting:disclosure',args=[self.candidate.pk]))
        link.revoked_at=timezone.now();link.save(update_fields=['revoked_at'])
        response=self.client.get(URL,{'tab':'customer'})
        self.assertEqual(response.context['page'].paginator.count,0)
        self.assertNotContains(response,self.organization.name)
    def test_customer_and_participant_realms_cannot_read_any_category(self):
        for actor in [self.customer,self.portal]:
            self.client.force_login(actor,backend=BACKEND)
            for tab in ['events','verification','customer','jobs']:
                self.assertEqual(self.client.get(URL,{'tab':tab}).status_code,403)
    def test_verification_uses_authoritative_expiry_and_append_only_history(self):
        category=QualificationCategory.objects.create(name='SYNTHETIC Approvals',slug='synthetic-approval-hub')
        catalog=QualificationCatalogEntry.objects.create(kind='skill',name='SYNTHETIC evidence',category=category)
        item=ParticipantQualification.objects.create(participant=self.person,catalog_entry=catalog,source='other',is_self_reported=True)
        self.assertEqual(self.client.get(URL,{'tab':'verification'}).context['page'].paginator.count,1)
        record=ParticipantVerificationRecord.objects.create(participant=self.person,qualification=item,status='verified',method='staff_confirmed',reason='SYNTHETIC reviewed evidence',evidence_notes='SYNTHETIC observed practice',reviewer=self.admin,reviewed_at=timezone.now(),qualification_snapshot=qualification_snapshot(item))
        verified=self.client.get(URL,{'tab':'verification','state':'verified'})
        self.assertEqual(verified.context['page'].paginator.count,1)
        self.assertContains(verified,reverse('participants:staff-qualification-approval-detail',args=['qualification',item.pk]))
        item.expiration_date=timezone.localdate()-timedelta(days=1);item.save()
        self.assertEqual(self.client.get(URL,{'tab':'verification','state':'verified'}).context['page'].paginator.count,0)
        self.assertEqual(self.client.get(URL,{'tab':'verification'}).context['page'].paginator.count,1)
        self.assertEqual(ParticipantVerificationRecord.objects.filter(pk=record.pk).count(),1)
    def test_verification_viewer_can_use_existing_queue_without_decision_authority(self):
        reviewer=get_user_model().objects.create_user('synthetic-hub-reviewer',is_staff=True)
        reviewer.user_permissions.add(Permission.objects.get(content_type__app_label='participants',codename='view_qualificationapprovals'))
        self.client.force_login(reviewer,backend=BACKEND)
        response=self.client.get(URL,{'tab':'verification'})
        self.assertEqual(response.status_code,200)
        self.assertEqual([t['label']for t in response.context['approval_tabs']],['Verification'])
        self.assertEqual(self.client.get(URL,{'tab':'events'}).status_code,403)
    def test_position_review_counts_only_submitted_or_review_revisions(self):
        need=WorkforceNeed.objects.create(organization=self.organization,status='submitted')
        revision=WorkforceNeedRevision.objects.create(need=need,organization=self.organization,number=1,state='submitted',title='SYNTHETIC submitted position',worker_quantity=2,submitted_at=timezone.now(),submitted_by=self.customer)
        need.working_revision=revision;need.save()
        response=self.client.get(URL,{'tab':'jobs','state':'positions'})
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertContains(response,revision.title)
        self.assertContains(response,reverse('customers:need',args=[self.organization.pk,need.pk]))
        self.assertEqual(self.client.get(URL,{'tab':'jobs','state':'publication'}).context['page'].paginator.count,1)
    def test_publication_and_get_state_do_not_advance_or_publish_records(self):
        before=(self.opportunity.publication_state,CandidateOpportunity.objects.count(),CandidateDisclosure.objects.count(),AuditLog.objects.count())
        response=self.client.get(URL,{'tab':'jobs','state':'publication'})
        self.assertContains(response,'Unpublished')
        self.opportunity.refresh_from_db()
        self.assertEqual(before,(self.opportunity.publication_state,CandidateOpportunity.objects.count(),CandidateDisclosure.objects.count(),AuditLog.objects.count()))

    def test_current_publication_selector_moves_completed_copy_to_secondary_history(self):
        from unittest.mock import patch
        from apps.recruiting.models import RecruitingOpportunity
        before=AuditLog.objects.count()
        current=RecruitingOpportunity.objects.filter(pk=self.opportunity.pk)
        with patch('apps.recruiting.jobs.published',return_value=current):
            pending=self.client.get(URL,{'tab':'jobs','state':'publication'})
            history=self.client.get(URL,{'tab':'jobs','state':'publication_history'})
        self.assertEqual(pending.context['page'].paginator.count,0)
        self.assertEqual(history.context['page'].paginator.count,1)
        self.assertEqual(AuditLog.objects.count(),before)
