"""Explicit disclosure, existing-consent reuse and separate employer/staff outcomes."""
import json,uuid
from django.core.exceptions import ValidationError,PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.customers.test_portal import PortalFixtures,BACKEND
from apps.customers import services as customer_services
from apps.participants.models import ConsentAcceptance,RegistrationForm,Participant
from .test_workflow import WorkflowFixtures
from . import employer,workflow,selection,foundation
from .models import CandidateDisclosure,EmployerCandidateFeedback,CandidateOpportunity,ConsentWithdrawal


class EmployerFixtures(WorkflowFixtures,PortalFixtures):
    def employer_setup(self):
        self.workflow_setup()
        self.organization.refresh_from_db()
        self.organization=customer_services.save_organization(actor=self.admin,org_id=self.organization.pk,
            expected_revision=self.organization.revision,data={'status':'active'},why='SYNTHETIC employer portal readiness')
        self.lead_member=self.membership(self.organization)
        self.customer=self.activate(self.lead_member)
        self.candidate=self.select()
    def release(self,**changes):
        self.candidate.refresh_from_db()
        data={'actor':self.admin,'candidate_id':self.candidate.pk,'categories':['identity','summary','region','experience','qualifications'],
            'purpose':'SYNTHETIC reviewed professional information for this approved position',
            'expected_revision':self.candidate.revision,'confirmed':True}
        data.update(changes)
        candidate=CandidateOpportunity.objects.select_related('participant','opportunity__need').get(pk=data['candidate_id'])
        data.setdefault('expected_need_revision',candidate.opportunity.need.effective_revision_id)
        data.setdefault('expected_projection',employer.digest(employer.build_projection(data['actor'],candidate.participant,data['categories'])))
        return employer.release(**data)
    def respond(self,grant,**changes):
        data={'actor':self.customer,'organization_id':self.organization.pk,'disclosure_id':grant.pk,
            'kind':'interested','message':'SYNTHETIC employer feedback','operation_id':uuid.uuid4()}
        data.update(changes)
        return employer.feedback(**data)


class EmployerTests(EmployerFixtures,TestCase):
    def setUp(self):self.employer_setup()

    def test_feedback_review_is_scoped_audited_idempotent_and_separate_from_stages(self):
        grant=self.release();event=self.respond(grant,kind='interview')
        with self.assertRaises(PermissionDenied):
            employer.review_feedback(actor=self.customer,feedback_id=event.pk,why='SYNTHETIC unauthorized review')
        with self.assertRaises(PermissionDenied):
            employer.review_feedback(actor=self.recruiter,feedback_id=event.pk,why='SYNTHETIC unrelated recruiter')
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        before=self.client.get(reverse('recruiting:overview'))
        self.assertEqual(before.context['pending_response_count'],1)
        self.assertEqual(before.context['interview_count'],1)
        one=employer.review_feedback(actor=self.admin,feedback_id=event.pk,why='SYNTHETIC interview request reviewed')
        again=employer.review_feedback(actor=self.admin,feedback_id=event.pk,why='SYNTHETIC retry')
        self.assertEqual(one.pk,again.pk)
        self.candidate.refresh_from_db();self.assertEqual(self.candidate.stage_id,'identified')
        event.refresh_from_db();self.assertIsNone(event.confirmed_at)
        after=self.client.get(reverse('recruiting:overview'))
        self.assertEqual(after.context['pending_response_count'],0)
        self.assertEqual(after.context['interview_count'],0)

    def test_overview_uses_current_position_and_task_permissions_without_mock_counts(self):
        from datetime import timedelta
        from apps.staffing.models import StaffProfile
        from apps.operations.models import Task
        from .models import RecruitingTaskLink
        profile=StaffProfile.objects.create(user=self.admin,first_name='SYNTHETIC',last_name='Overview')
        task=Task.objects.create(title='SYNTHETIC overdue recruiting task',assigned_staff=profile,
            due_date=timezone.localdate()-timedelta(days=1),created_by=self.admin)
        RecruitingTaskLink.objects.create(task=task,opportunity=self.opportunity,candidate=self.candidate,added_by=self.admin)
        foundation.group_positions(actor=self.admin,organization_id=self.organization.pk,name='SYNTHETIC grouped request',need_ids=[self.need.pk])
        workflow.transition(actor=self.admin,candidate_id=self.candidate.pk,stage_key='contacting',
            expected_revision=self.candidate.revision,why='SYNTHETIC contact needed')
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(reverse('recruiting:overview'))
        self.assertEqual(response.status_code,200)
        for name in ['open_position_count','request_count','engagement_count','outreach_count','overdue_count']:
            self.assertEqual(response.context[name],1,name)
        self.assertContains(response,task.title)
        self.client.force_login(self.recruiter,backend='django.contrib.auth.backends.ModelBackend')
        private=self.client.get(reverse('recruiting:overview'))
        for name in ['open_position_count','request_count','engagement_count','outreach_count','overdue_count']:
            self.assertEqual(private.context[name],0,name)
        self.assertNotContains(private,task.title)

    def test_independent_staff_forms_have_unique_label_targets(self):
        import re
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(reverse('recruiting:candidate-detail',args=[self.candidate.pk]))
        html=response.content.decode()
        ids=re.findall(r'\bid="(recruiting_[^"]+)"',html)
        self.assertEqual(len(ids),len(set(ids)))
        for prefix in ['stage_form','reassignment_form','outreach_form']:
            self.assertIn('id="recruiting_'+prefix+'_reason"',html)
            self.assertNotIn('for="recruiting_'+prefix+'_reason"',html)

    def test_only_explicit_btw_release_exposes_allowlisted_information(self):
        self.assertEqual(employer.list_candidates(self.customer,self.organization.pk),[])
        consent_count=ConsentAcceptance.objects.count();form_count=RegistrationForm.objects.count()
        grant=self.release()
        data=employer.detail(self.customer,self.organization.pk,grant.pk)
        dumped=json.dumps(data,default=str)
        self.assertIn(self.person.last_name,dumped)
        for forbidden in ['contact_email','contact_phone','date_of_birth','intake_metadata','selection_rationale','match_score','participant_id','document_path','document_uuid']:
            self.assertNotIn(forbidden,dumped)
        self.assertNotIn(self.person.contact_email,dumped)
        self.assertEqual(ConsentAcceptance.objects.count(),consent_count);self.assertEqual(RegistrationForm.objects.count(),form_count)
        self.assertEqual(grant.acceptance,selection.applicable_consent(self.person))

    def test_readonly_members_need_explicit_scoped_response_permission(self):
        grant=self.release()
        member=self.membership(self.organization,role='member');user=self.activate(member)
        self.assertEqual(len(employer.list_candidates(user,self.organization.pk)),1)
        with self.assertRaises(PermissionDenied):self.respond(grant,actor=user)
        permission=employer.member_permission(actor=self.admin,disclosure_id=grant.pk,membership_id=member.pk,
            can_respond=True,expected_revision=0,why='SYNTHETIC explicit release-specific response permission')
        self.assertEqual(self.respond(grant,actor=user).membership,member)
        employer.member_permission(actor=self.admin,disclosure_id=grant.pk,membership_id=member.pk,can_respond=False,
            expected_revision=permission.revision,why='SYNTHETIC permission revoked')
        with self.assertRaises(PermissionDenied):self.respond(grant,actor=user)

    def test_foreign_employer_and_position_mismatch_fail_closed(self):
        grant=self.release();other,member,user=self.lead()
        self.assertEqual(employer.list_candidates(user,other.pk),[])
        with self.assertRaises(PermissionDenied):employer.detail(user,other.pk,grant.pk)
        with self.assertRaises(PermissionDenied):self.respond(grant,actor=user,organization_id=other.pk)
        with self.assertRaises(PermissionDenied):employer.member_permission(actor=self.admin,disclosure_id=grant.pk,membership_id=member.pk,can_respond=True,expected_revision=0,why='SYNTHETIC wrong organization')
        other_need=self.approved_need(self.organization)
        CandidateDisclosure.objects.filter(pk=grant.pk).update(need=other_need)
        with self.assertRaises(PermissionDenied):employer.detail(self.customer,self.organization.pk,grant.pk)

    def test_responses_do_not_advance_pipeline_and_hiring_requires_btw_confirmation(self):
        grant=self.release()
        for kind in ['interested','interview','not_interested','feedback']:self.respond(grant,kind=kind)
        event=self.respond(grant,kind='report_hired')
        self.candidate.refresh_from_db();self.assertEqual(self.candidate.stage_id,'identified');self.assertTrue(self.candidate.active)
        with self.assertRaises(PermissionDenied):employer.confirm_hire(actor=self.customer,feedback_id=event.pk,expected_candidate_revision=self.candidate.revision,why='SYNTHETIC customer cannot confirm')
        row=employer.confirm_hire(actor=self.admin,feedback_id=event.pk,expected_candidate_revision=self.candidate.revision,why='SYNTHETIC BTW confirmed actual hire')
        self.assertEqual(row.outcome,'hired');self.assertFalse(row.active)
        event.refresh_from_db();self.assertEqual(event.confirmed_by,self.admin);self.assertIsNotNone(event.confirmed_at)
        self.assertEqual(len(employer.list_candidates(self.customer,self.organization.pk)),1)
        self.assertEqual(employer.confirm_hire(actor=self.admin,feedback_id=event.pk,expected_candidate_revision=0,why='SYNTHETIC retry').pk,row.pk)

    def test_replayed_employer_response_is_idempotent_and_payload_bound(self):
        grant=self.release();operation=uuid.uuid4()
        one=self.respond(grant,operation_id=operation);again=self.respond(grant,operation_id=operation)
        self.assertEqual(one.pk,again.pk);self.assertEqual(EmployerCandidateFeedback.objects.count(),1)
        with self.assertRaises(ValidationError):self.respond(grant,operation_id=operation,kind='report_hired')
        one.message='Unattributed alteration'
        with self.assertRaises(ValidationError):one.save()

    def test_disclosure_approval_is_bound_to_the_reviewed_projection(self):
        preview=employer.build_projection(self.admin,self.person,['summary'])
        self.career.skills_interests='SYNTHETIC changed after staff preview';self.career.save()
        with self.assertRaises(ValidationError):self.release(categories=['summary'],expected_projection=employer.digest(preview))
        self.assertFalse(CandidateDisclosure.objects.exists())
        one=self.release();again=self.release();self.assertEqual(one.pk,again.pk)

    def test_revocation_and_recorded_withdrawal_stop_reads_and_writes(self):
        grant=self.release();employer.revoke(actor=self.admin,disclosure_id=grant.pk,expected_revision=grant.revision,why='SYNTHETIC explicit revocation')
        with self.assertRaises(PermissionDenied):employer.detail(self.customer,self.organization.pk,grant.pk)
        with self.assertRaises(PermissionDenied):self.respond(grant)
        second=self.release();count=ConsentAcceptance.objects.count()
        employer.record_withdrawal(actor=self.admin,participant_id=self.person.pk,why='SYNTHETIC participant reported employer-sharing withdrawal')
        self.assertEqual(ConsentAcceptance.objects.count(),count);self.assertEqual(ConsentWithdrawal.objects.count(),1)
        with self.assertRaises(PermissionDenied):employer.detail(self.customer,self.organization.pk,second.pk)
        with self.assertRaises(ValidationError):self.release()

    def test_profile_changes_require_new_review_and_old_release_remains_immutable(self):
        grant=self.release()
        self.career.skills_interests='SYNTHETIC newly reported unreviewed experience';self.career.save()
        self.assertEqual(employer.list_candidates(self.customer,self.organization.pk),[])
        grant.categories=['identity']
        with self.assertRaises(ValidationError):grant.save()
        newer=self.release();grant.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)
        self.assertIn('newly reported',employer.detail(self.customer,self.organization.pk,newer.pk)['candidate']['summary']['skills'])

    def test_withdrawn_pipeline_and_changed_approved_position_block_disclosure(self):
        grant=self.release()
        row=workflow.transition(actor=self.admin,candidate_id=self.candidate.pk,stage_key='withdrawn',expected_revision=self.candidate.revision,why='SYNTHETIC participant withdrew from this position')
        self.assertEqual(employer.list_candidates(self.customer,self.organization.pk),[])
        self.assertTrue(CandidateDisclosure.objects.filter(pk=grant.pk).exists())

    def test_existing_campaign_restriction_applies_on_every_read_and_response(self):
        grant=self.release();other=self.org(name='SYNTHETIC exclusive sourcing employer');campaign=self.campaign(other)
        foundation.source_campaign(actor=self.admin,campaign_id=campaign.pk,participant_id=self.person.pk)
        self.assertEqual(employer.list_candidates(self.customer,self.organization.pk),[])
        with self.assertRaises(PermissionDenied):self.respond(grant)
        source=self.person.campaign_sources.get(campaign=campaign)
        foundation.release_exclusivity(actor=self.admin,source_id=source.pk,why='SYNTHETIC authorized early release')
        self.assertEqual(len(employer.list_candidates(self.customer,self.organization.pk)),1)

    def test_identity_consolidation_revokes_approved_disclosure_without_losing_history(self):
        # Use a source without an account; authentication ownership is never merged.
        source=self.submit(email='synthetic15-disclosure-alias@example.test',phone='2025550177',updates_preference='no').participant
        source_candidate=self.select(person=source)
        grant=self.release(candidate_id=source_candidate.pk,expected_revision=source_candidate.revision)
        before=foundation.consolidation_preview(source.pk,self.person.pk)
        foundation.consolidate(actor=self.admin,source_id=source.pk,target_id=self.person.pk,expected_fingerprint=before['fingerprint'],confirmed=True,why='SYNTHETIC staff identity consolidation')
        grant.refresh_from_db();self.assertIsNotNone(grant.revoked_at)
        self.assertTrue(CandidateDisclosure.objects.filter(pk=grant.pk).exists())
        with self.assertRaises(PermissionDenied):employer.detail(self.customer,self.organization.pk,grant.pk)

    def test_participant_origin_journey_has_no_automatic_employer_disclosure(self):
        self.publish();interest=self.interest()
        self.assertFalse(CandidateDisclosure.objects.exists())
        self.review(interest,select=True)
        self.assertFalse(CandidateDisclosure.objects.exists())
        grant=self.release();response=self.respond(grant,kind='interview')
        self.assertEqual(response.disclosure,grant)
        interest.refresh_from_db();self.assertEqual(interest.candidate,self.candidate)
        self.assertEqual(interest.publication_id,self.opportunity.publication_id)

    def test_participant_interest_withdrawal_revokes_release_without_automatic_stage_change(self):
        from . import jobs
        self.publish();interest=self.interest();self.review(interest,select=True);grant=self.release()
        jobs.withdraw_interest(actor=self.portal,interest_id=interest.pk)
        self.candidate.refresh_from_db();grant.refresh_from_db()
        self.assertEqual(self.candidate.stage_id,'identified');self.assertIsNotNone(grant.revoked_at)
        with self.assertRaises(ValidationError):self.release()
        self.assertEqual(employer.list_candidates(self.customer,self.organization.pk),[])

    def test_recruiter_delegation_does_not_grant_private_evidence_or_customer_administration(self):
        self.opportunity.refresh_from_db()
        workflow.delegate(actor=self.admin,opportunity_id=self.opportunity.pk,user_id=self.recruiter.pk,mode='owner',expected_revision=self.opportunity.revision,why='SYNTHETIC recruiting delegation')
        self.assertFalse(self.recruiter.has_perm('customers.manage_customers'))
        with self.assertRaises(PermissionDenied):self.release(actor=self.recruiter,categories=['assessments'])
        grant=self.release(actor=self.recruiter,categories=['identity','region','experience'])
        self.assertEqual(len(employer.list_candidates(self.customer,self.organization.pk)),1)
        with self.assertRaises(PermissionDenied):employer.record_withdrawal(actor=self.recruiter,participant_id=self.person.pk,why='SYNTHETIC unauthorized consent change')

    def test_assigned_recruiter_can_write_only_their_scoped_recruiting_workflow(self):
        self.opportunity.refresh_from_db()
        workflow.delegate(actor=self.admin,opportunity_id=self.opportunity.pk,user_id=self.recruiter.pk,mode='owner',expected_revision=self.opportunity.revision,why='SYNTHETIC delegated recruiting ownership')
        self.opportunity.refresh_from_db()
        selection.save_criteria(actor=self.recruiter,opportunity_id=self.opportunity.pk,
            rules=[{'kind':'skill','label':'Welding','value':'welding'}],expected_revision=self.opportunity.revision,
            expected_need_revision=self.need.effective_revision_id,why='SYNTHETIC recruiter criteria review')
        self.opportunity.refresh_from_db()
        workflow.publish(actor=self.recruiter,opportunity_id=self.opportunity.pk,expected_revision=self.opportunity.revision,
            expected_need_revision=self.need.effective_revision_id,action='publish',details={'title':'SYNTHETIC job','summary':'SYNTHETIC reviewed approved position'},why='SYNTHETIC delegated publication')
        new_person=self.submit(email='synthetic15-recruiter-selection@example.test',phone='2025550175',updates_preference='no').participant
        conflicts=selection.conflict_set(self.recruiter,new_person,excluding=self.opportunity.pk)
        selected=selection.select_candidate(actor=self.recruiter,opportunity_id=self.opportunity.pk,participant_id=new_person.pk,
            expected_need_revision=self.need.effective_revision_id,expected_conflicts=conflicts['fingerprint'],acknowledged=False,why='SYNTHETIC recruiter selection')
        advanced=workflow.transition(actor=self.recruiter,candidate_id=selected.pk,stage_key='contacting',expected_revision=selected.revision,why='SYNTHETIC recruiter follow-up')
        self.assertEqual(advanced.stage_id,'contacting');self.assertEqual(advanced.assignee,self.recruiter)
        self.assertFalse(self.recruiter.has_perm('customers.manage_customers'))

    def test_campaign_sourcing_is_available_in_ui_and_scoped_to_recruiting_responsibility(self):
        own=self.campaign(self.organization)
        self.need.campaign=own;self.need.save()
        other=self.org(name='SYNTHETIC unassigned sourcing company');foreign=self.campaign(other)
        self.opportunity.refresh_from_db()
        workflow.delegate(actor=self.admin,opportunity_id=self.opportunity.pk,user_id=self.recruiter.pk,mode='owner',expected_revision=self.opportunity.revision,why='SYNTHETIC sourcing responsibility')
        with self.assertRaises(PermissionDenied):foundation.source_campaign(actor=self.recruiter,campaign_id=foreign.pk,participant_id=self.person.pk)
        self.client.force_login(self.recruiter,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.post(reverse('recruiting:candidate',args=[self.person.pk]),{'operation':'source_campaign','campaign':str(own.pk),'exclusive':'on','reason':'SYNTHETIC original Campaign source'})
        self.assertEqual(response.status_code,302)
        row=self.person.campaign_sources.get(campaign=own);self.assertEqual(row.sourced_by,self.recruiter)
        response=self.client.get(reverse('recruiting:candidate',args=[self.person.pk]));self.assertContains(response,'Campaign sourcing');self.assertContains(response,'completion/duration setup needed')

    def test_prior_employer_approval_does_not_expand_staff_evidence_permissions(self):
        from apps.simulators.models import SimulatorCaptureProfile,SimulatorCapture
        from apps.stations.models import Station
        from .test_matching import MatchingTests
        from decimal import Decimal
        event,registration,session=MatchingTests.official_experience(self)
        profile=SimulatorCaptureProfile.objects.create(name='SYNTHETIC RESTRICTED assessment',simulator_type='SYNTHETIC equipment',profile_version='1')
        station=Station.objects.create(code='synthetic15-private-simulator',name='SYNTHETIC simulator',station_type='simulator')
        SimulatorCapture.objects.create(submission_id=uuid.uuid4(),request_fingerprint='synthetic15',event=event,station=station,
            profile=profile,profile_version='1',profile_snapshot={'version':'1'},raw_identifier=str(registration.pk),normalized_identifier=str(registration.pk),
            raw_total_score='82.25',total_score=Decimal('82.25'),captured_at=timezone.now(),status='matched',mode='official',is_official=True,registration=registration,participant=self.person,experience_session=session)
        grant=self.release(categories=['identity','assessments'])
        self.opportunity.refresh_from_db()
        workflow.delegate(actor=self.admin,opportunity_id=self.opportunity.pk,user_id=self.recruiter.pk,mode='owner',expected_revision=self.opportunity.revision,why='SYNTHETIC scoped recruiting ownership')
        self.client.force_login(self.recruiter,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(reverse('recruiting:disclosure',args=[self.candidate.pk]))
        self.assertEqual(response.status_code,200);self.assertNotContains(response,'SYNTHETIC RESTRICTED assessment');self.assertNotContains(response,'82.25')
        self.assertFalse(self.recruiter.has_perm('simulators.view_simulatorcapture'))

    def test_customer_and_staff_routes_keep_existing_realms_and_viewonly_preview(self):
        grant=self.release()
        self.client.force_login(self.customer,backend=BACKEND)
        for url in [reverse('customer_portal:workspace',args=['candidates']),reverse('customer_portal:recruiting-candidate',args=[grant.pk])]:
            response=self.client.get(url);self.assertEqual(response.status_code,200);self.assertContains(response,'Candidate')
        self.assertEqual(self.client.get(reverse('recruiting:pipeline')).status_code,403)
        self.assertEqual(self.client.get(reverse('participants:staff-profile',args=[self.person.pk])).status_code,403)
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        for url in [reverse('recruiting:disclosure',args=[self.candidate.pk]),reverse('recruiting:sharing-withdrawal',args=[self.person.pk])]:self.assertEqual(self.client.get(url).status_code,200)
