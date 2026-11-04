"""Whole staff and participant journeys using the existing account/intake/task records."""
import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.participants.models import Participant,ParticipantAccount
from .test_matching import MatchingFixtures
from . import jobs,workflow,selection,foundation
from .models import CandidateInterest,CandidateOpportunity,RecruitingStage,OpportunityPublication,InterestReview,RecruitingTaskLink,CandidateOutreach


class WorkflowFixtures(MatchingFixtures):
    def workflow_setup(self):
        self.matching_setup()
        self.portal=get_user_model().objects.create_user(username='synthetic15-participant',password='synthetic-test-only')
        self.account=ParticipantAccount.objects.create(participant=self.person,user=self.portal,login_email='synthetic15-portal@example.test',status='active',email_verified_at=timezone.now(),password_set_at=timezone.now())
    def publish(self,**changes):
        self.opportunity.refresh_from_db()
        data={'actor':self.admin,'opportunity_id':self.opportunity.pk,'expected_revision':self.opportunity.revision,
            'expected_need_revision':self.opportunity.need.effective_revision_id,
            'action':'publish','details':{'title':'SYNTHETIC approved welding position','trade':'Welding','summary':'SYNTHETIC approved participant-facing opportunity','location':'Example City, GA','employment_type':'Full time'},'why':'SYNTHETIC reviewed public copy'}
        data.update(changes)
        row=workflow.publish(**data);self.opportunity=row
        return row
    def interest(self,**changes):
        data={'actor':self.portal,'opportunity_id':self.opportunity.pk,'publication_id':self.opportunity.publication_id};data.update(changes)
        return jobs.express_interest(**data)
    def review(self,row,**changes):
        row.refresh_from_db()
        conflicts=selection.conflict_set(self.admin,self.person,excluding=self.opportunity.pk)
        data={'actor':self.admin,'interest_id':row.pk,'status':'review','note':'SYNTHETIC review of participant interest','expected_revision':row.revision,
            'expected_need_revision':row.opportunity.need.effective_revision_id,
            'operation_id':uuid.uuid4(),'expected_conflicts':conflicts['fingerprint'],'acknowledged':conflicts['has_conflicts']}
        data.update(changes)
        return workflow.review_interest(**data)


class WorkflowTests(WorkflowFixtures,TestCase):
    def setUp(self):self.workflow_setup()

    def test_only_explicit_reviewed_publication_is_visible_and_current(self):
        self.assertEqual(jobs.browse(self.portal),[])
        row=self.publish()
        self.assertEqual(len(jobs.browse(self.portal,trade='weld',location='GA')),1)
        self.assertEqual(jobs.browse(self.portal,trade='carpentry'),[])
        projected=jobs.browse(self.portal)[0]
        self.assertNotIn('need',projected);self.assertNotIn('organization',projected)
        self.need.internal_note='SYNTHETIC confidential';self.need.save()
        self.assertNotIn('confidential',str(projected))
        with self.assertRaises(ValidationError):self.publish(details={'title':'SYNTHETIC','summary':'SYNTHETIC','contact_email':'private@example.test'})
        self.publish(action='unpublish');self.assertEqual(jobs.browse(self.portal),[])

    def test_interest_is_attributed_idempotent_and_separate_from_pipeline(self):
        self.publish();one=self.interest();two=self.interest()
        self.assertEqual(one.pk,two.pk);self.assertEqual(one.submitted_by,self.portal)
        self.assertEqual(one.source_participant,self.person);self.assertEqual(one.publication,self.opportunity.publication)
        self.assertFalse(CandidateOpportunity.objects.exists());self.assertEqual(one.status,'received')
        self.assertEqual(jobs.own_interests(self.portal).count(),1)

    def test_own_interest_history_is_complete_paginated_and_private(self):
        self.publish()
        for index in range(21):CandidateInterest.objects.create(opportunity=self.opportunity,publication=self.opportunity.publication,
            participant=self.person,source_participant=self.person,submitted_by=self.portal,active=False,status='withdrawn')
        other=Participant.objects.create(first_name='SYNTHETIC PRIVATE',last_name='Other interest owner')
        CandidateInterest.objects.create(opportunity=self.opportunity,publication=self.opportunity.publication,
            participant=other,source_participant=other,submitted_by=self.admin)
        self.client.force_login(self.portal,backend='apps.participants.auth.ParticipantAuthenticationBackend')
        response=self.client.get(reverse('participants:portal-jobs')+'?view=interests&interest_page=2')
        self.assertEqual(response.status_code,200);self.assertContains(response,'Page 2 of 2');self.assertNotContains(response,'Other interest owner')

    def test_incomplete_intake_interest_is_retained_until_staff_review(self):
        self.publish()
        other=Participant.objects.create(first_name='SYNTHETIC',last_name='Incomplete intake')
        user=get_user_model().objects.create_user(username='synthetic15-incomplete')
        ParticipantAccount.objects.create(user=user,participant=other,status='active',email_verified_at=timezone.now(),login_email='incomplete15@example.test')
        row=self.interest(actor=user)
        self.assertEqual(row.participant,other)
        self.assertFalse(foundation.readiness(other)['eligible'])
        with self.assertRaises(ValidationError):self.review(row,select=True)
        self.assertEqual(CandidateInterest.objects.filter(pk=row.pk).count(),1);self.assertFalse(CandidateOpportunity.objects.exists())
        self.review(row,status='information')
        row.refresh_from_db();self.assertEqual(row.get_status_display(),'Additional Information Needed')

    def test_interest_isolation_stale_publication_closing_and_expiration(self):
        self.publish();old=self.opportunity.publication_id
        self.publish()
        with self.assertRaises(ValidationError):self.interest(publication_id=old)
        row=self.interest()
        other=get_user_model().objects.create_user(username='synthetic15-wrong-person')
        with self.assertRaises(PermissionDenied):jobs.withdraw_interest(actor=other,interest_id=row.pk)
        self.publish(action='close')
        # An accepted idempotent repeat does not manufacture another record.
        self.assertEqual(self.interest().pk,row.pk)
        jobs.withdraw_interest(actor=self.portal,interest_id=row.pk)
        with self.assertRaises(ValidationError):self.interest()
        self.publish(expires_at=timezone.now()+timedelta(minutes=1))
        OpportunityPublication.objects.filter(pk=self.opportunity.publication_id).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(jobs.browse(self.portal),[])

    def test_staff_review_selection_preserves_interest_provenance_and_history(self):
        self.publish();row=self.interest();operation=uuid.uuid4()
        event=self.review(row,select=True,operation_id=operation)
        again=self.review(row,select=True,operation_id=operation)
        self.assertEqual(event.pk,again.pk);row.refresh_from_db()
        self.assertTrue(row.candidate_id);self.assertEqual(row.publication_id,self.opportunity.publication_id)
        self.assertEqual(InterestReview.objects.filter(interest=row).count(),1)
        self.assertEqual(CandidateOpportunity.objects.count(),1)

    def test_stages_are_explicit_versioned_and_hiring_requires_btw_confirmation(self):
        row=self.select()
        with self.assertRaises(ValidationError):workflow.transition(actor=self.admin,candidate_id=row.pk,stage_key='hired',expected_revision=row.revision,why='SYNTHETIC unconfirmed')
        row=workflow.transition(actor=self.admin,candidate_id=row.pk,stage_key='interview',expected_revision=row.revision,why='SYNTHETIC authorized skip')
        self.assertEqual(row.stage_id,'interview')
        with self.assertRaises(ValidationError):workflow.transition(actor=self.admin,candidate_id=row.pk,stage_key='contacting',expected_revision=0,why='SYNTHETIC stale')
        row=workflow.transition(actor=self.admin,candidate_id=row.pk,stage_key='hired',expected_revision=row.revision,why='SYNTHETIC actual hiring confirmed',confirm_hired=True)
        self.assertFalse(row.active);self.assertEqual(row.outcome,'hired')
        self.assertEqual(CandidateOpportunity.objects.get(pk=row.pk).source_participant,self.person)

    def test_stage_labels_do_not_change_terminal_semantics_and_retirement_is_safe(self):
        row=RecruitingStage.objects.get(pk='hired')
        saved=workflow.configure_stage(actor=self.admin,key='hired',label='SYNTHETIC Placement Confirmed',position=8,is_active=True,expected_revision=row.revision,why='SYNTHETIC label update')
        self.assertEqual(saved.semantic,'hired')
        with self.assertRaises(ValidationError):workflow.configure_stage(actor=self.admin,key='identified',label='Identified',position=0,is_active=False,expected_revision=0,why='SYNTHETIC invalid retirement')
        row=self.select();stage=RecruitingStage.objects.get(pk='interview')
        workflow.configure_stage(actor=self.admin,key=stage.pk,label=stage.label,position=stage.position,is_active=False,expected_revision=stage.revision,why='SYNTHETIC retire stage')
        with self.assertRaises(ValidationError):workflow.transition(actor=self.admin,candidate_id=row.pk,stage_key='interview',expected_revision=row.revision,why='SYNTHETIC retired transition')

    def test_collaboration_reassignment_and_recruiter_organization_boundary(self):
        self.opportunity.refresh_from_db()
        workflow.delegate(actor=self.admin,opportunity_id=self.opportunity.pk,user_id=self.recruiter.pk,mode='view',expected_revision=self.opportunity.revision,why='SYNTHETIC scoped collaboration')
        self.assertEqual(selection.opportunity_for(self.recruiter,self.opportunity.pk).pk,self.opportunity.pk)
        with self.assertRaises(PermissionDenied):selection.opportunity_for(self.recruiter,self.opportunity.pk,write=True)
        row=self.select();row=workflow.reassign(actor=self.admin,candidate_id=row.pk,user_id=self.recruiter.pk,expected_revision=row.revision,why='SYNTHETIC reassignment')
        self.assertEqual(row.assignee,self.recruiter)
        self.assertFalse(self.recruiter.has_perm('customers.manage_customers'))
        with self.assertRaises(PermissionDenied):foundation.group_positions(actor=self.recruiter,organization_id=self.organization.pk,name='SYNTHETIC unauthorized',need_ids=[self.need.pk])

    def test_existing_task_engine_is_reused_without_granting_task_access(self):
        from apps.staffing.models import StaffProfile
        from apps.operations.models import Task
        from apps.operations.task_access import require_task_access
        profile=StaffProfile.objects.create(user=self.admin,first_name='SYNTHETIC',last_name='Recruiting staff')
        row=self.select();operation=uuid.uuid4()
        data={'title':'SYNTHETIC Recruiting follow-up','description':'SYNTHETIC contact','assigned_staff':str(profile.pk),'event':'','due_date':'','priority':'normal','revision':'new'}
        task,form=workflow.create_followup(actor=self.admin,opportunity_id=self.opportunity.pk,candidate_id=row.pk,data=data,operation_id=operation)
        self.assertIsNotNone(task,form.errors);self.assertEqual(Task.objects.count(),1)
        again,_=workflow.create_followup(actor=self.admin,opportunity_id=self.opportunity.pk,candidate_id=row.pk,data=data,operation_id=operation)
        self.assertEqual(task.pk,again.pk);self.assertEqual(RecruitingTaskLink.objects.count(),1)
        with self.assertRaises(PermissionDenied):require_task_access(self.recruiter,task)
        event=workflow.record_outreach(actor=self.admin,candidate_id=row.pk,expected_revision=row.revision,operation_id=uuid.uuid4(),
            data={'method':'phone','contacted_at':timezone.now()-timedelta(minutes=5),'outcome':'SYNTHETIC Left voicemail','notes':'SYNTHETIC note','task':task.pk})
        self.assertEqual(event.task,task);self.assertEqual(CandidateOutreach.objects.count(),1)

    def test_legacy_reviewed_identity_changes_reconcile_candidate_collisions(self):
        from apps.events.models import ParticipantMatchReview,ParticipantIdentityLink
        from .identity_hooks import before_reviewed_write,after_reviewed_write
        source=self.submit(email='synthetic15-legacy@example.test',phone='2025550178',updates_preference='no').participant
        winner=self.select();old=self.select(person=source)
        with foundation.policy_transaction():
            before=before_reviewed_write('resolve_match',{})
            review=ParticipantMatchReview.objects.create(source_participant=source,candidate_participant=self.person,
                established_person_review=True,status='resolved',reviewed_by=self.admin,reviewed_at=timezone.now())
            ParticipantIdentityLink.objects.create(review=review,source_participant=source,candidate_participant=self.person)
            after_reviewed_write(before,self.admin)
        old.refresh_from_db();self.assertFalse(old.active);self.assertEqual(old.merged_into,winner)
        self.assertEqual(CandidateOpportunity.objects.filter(active=True).count(),1)
        from .models import IdentityConsolidation
        self.assertTrue(IdentityConsolidation.objects.filter(review=review).exists())
        with self.assertRaises(ValidationError):before_reviewed_write('reverse_match',{'review_id':review.pk})

    def test_staff_and_participant_routes_render_with_existing_shell(self):
        self.publish();interest=self.interest();candidate=self.select()
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        urls=[reverse('recruiting:overview'),reverse('recruiting:campaigns'),reverse('recruiting:pipeline'),reverse('recruiting:pipeline')+'?view=board',
            reverse('recruiting:candidate-detail',args=[candidate.pk]),reverse('recruiting:opportunity-settings',args=[self.opportunity.pk]),
            reverse('recruiting:interests'),reverse('recruiting:interest-review',args=[interest.pk]),reverse('recruiting:stages'),
            reverse('recruiting:request-group'),reverse('recruiting:identity-history')]
        for url in urls:self.assertEqual(self.client.get(url).status_code,200,url)
        self.client.force_login(self.portal,backend='apps.participants.auth.ParticipantAuthenticationBackend')
        for url in [reverse('participants:portal-jobs'),reverse('recruiting:portal-job',args=[self.opportunity.pk])]:
            response=self.client.get(url);self.assertEqual(response.status_code,200,url);self.assertContains(response,'Job Opportunities')
        self.assertEqual(self.client.get(reverse('recruiting:pipeline')).status_code,403)
