"""Explainable matching, policy gates, provenance and manual staff decisions."""
from decimal import Decimal
from datetime import timedelta
import hashlib
import json
import uuid
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.customers.test_domain import CustomerFixtures
from apps.participants import test_intake_submission_11b as intake
from apps.participants.models import Participant, ParticipantCareerProfile
from apps.participants.qualification_models import QualificationCategory, QualificationCatalogEntry, ParticipantQualification
from . import matching, selection, foundation
from .models import (RecruitingOpportunity, RecruitingProfile, Classification, CandidateOpportunity,
    ConsentWithdrawal, CandidateInterest, OpportunityPublication)


class MatchingFixtures(CustomerFixtures):
    answers=intake.IntakeSubmission11BTests.answers
    session=intake.IntakeSubmission11BTests.session
    submit=intake.IntakeSubmission11BTests.submit
    def matching_setup(self):
        intake.IntakeSubmission11BTests.setUp(self)
        self.person=self.submit(updates_preference='no').participant
        self.career=ParticipantCareerProfile.objects.get(participant=self.person)
        self.career.skills_interests='MIG welding';self.career.years_of_experience=Decimal('4');self.career.save()
        self.organization=self.org(name='SYNTHETIC Matching company')
        self.need=self.approved_need(self.organization)
        self.opportunity=selection.create_opportunity(actor=self.admin,need_id=self.need.pk,owner_id=self.admin.pk)
    def criteria(self,rules=None,opportunity=None):
        opportunity=opportunity or self.opportunity;opportunity.refresh_from_db()
        return selection.save_criteria(actor=self.admin,opportunity_id=opportunity.pk,
            rules=rules or [{'kind':'skill','label':'Welding','value':'welding','required':True}],
            expected_revision=opportunity.revision,why='SYNTHETIC reviewed position criteria')
    def select(self,opportunity=None,person=None,**changes):
        opportunity=opportunity or self.opportunity;person=person or self.person
        conflicts=selection.conflict_set(self.admin,person,excluding=opportunity.pk)
        data={'actor':self.admin,'opportunity_id':opportunity.pk,'participant_id':person.pk,
            'expected_conflicts':conflicts['fingerprint'],'acknowledged':conflicts['has_conflicts'],
            'why':'SYNTHETIC staff selection after reviewing qualifications'}
        data.update(changes)
        return selection.select_candidate(**data)


class MatchingTests(MatchingFixtures,TestCase):
    def setUp(self):self.matching_setup()

    def test_deterministic_results_and_informational_status_neutrality(self):
        criteria=self.criteria([{'kind':'skill','label':'Welding','value':'welding','weight':3},
            {'kind':'experience','label':'Experience','minimum':'3','weight':2}])
        before=matching.score_candidate(self.admin,self.person,criteria)
        self.career.employment_status='Employed / Nearing Retirement';self.career.employment_interest='not_seeking';self.career.save()
        self.person.life_stage='nearing_retirement';self.person.date_of_birth=timezone.localdate().replace(year=1950);self.person.save()
        classification=Classification.objects.create(kind='employment',key='retirement',label='Nearing Retirement')
        RecruitingProfile.objects.create(participant=self.person,employment_classification=classification)
        after=matching.score_candidate(self.admin,self.person,criteria)
        self.assertEqual(before['score'],after['score']);self.assertEqual(before['coverage'],after['coverage'])
        form,results,not_ready=matching.recommendations(self.admin,self.opportunity,{})
        form2,again,not_ready2=matching.recommendations(self.admin,self.opportunity,{})
        self.assertEqual([r['participant'].pk for r in results],[r['participant'].pk for r in again])
        self.assertEqual(results[0]['participant'].pk,self.person.pk)

    def test_missing_assessment_is_unknown_not_failure_or_automatic_exclusion(self):
        criteria=self.criteria([{'kind':'skill','label':'Welding','value':'welding','weight':1},
            {'kind':'assessment','label':'Compatible simulator','assessment_type':'simulator','profile':str(uuid.uuid4()),
             'profile_version':'SYNTHETIC-v1','minimum':'75','weight':1}])
        result=matching.score_candidate(self.admin,self.person,criteria)
        self.assertEqual(result['score'],Decimal('100'))
        self.assertEqual(result['coverage'],Decimal('50'))
        self.assertEqual(result['components'][1]['status'],'unknown')
        self.assertEqual(self.select().participant,self.person)

    def test_scoring_rejects_age_employment_and_incompatible_units(self):
        for rule in [{'kind':'age','label':'Disallowed','minimum':'20'},
                     {'kind':'employment_status','label':'Disallowed','value':'employed'},
                     {'kind':'assessment','label':'Mixed units','assessment_type':'simulator','minimum':'70','unit':'game points'},
                     {'kind':'skill','label':'Invalid importance','value':'welding','weight':-1}]:
            with self.assertRaises(ValidationError):matching.validate_rules([rule])

    def test_self_reported_required_credential_gap_is_visible_but_manual_selection_allowed(self):
        category=QualificationCategory.objects.create(name='SYNTHETIC credential',slug='synthetic15-match')
        entry=QualificationCatalogEntry.objects.create(category=category,name='SYNTHETIC certificate',identifier='synthetic15-cert',kind='certification')
        ParticipantQualification.objects.create(participant=self.person,catalog_entry=entry,source='participant',is_self_reported=True)
        criteria=self.criteria([{'kind':'certification','label':entry.name,'catalog':str(entry.stable_id),'verified_only':True,'required':True}])
        score=matching.score_candidate(self.admin,self.person,criteria)
        self.assertEqual(score['components'][0]['status'],'gap');self.assertFalse(score['components'][0]['verified'])
        selected=self.select(criteria_id=criteria.pk)
        self.assertEqual(selected.stage_id,'identified')
        self.assertEqual(selected.selected_need_revision_id,self.need.effective_revision_id)

    def test_required_intake_and_consent_cannot_be_bypassed_by_manual_selection(self):
        incomplete=Participant.objects.create(first_name='SYNTHETIC',last_name='Incomplete lead')
        with self.assertRaises(ValidationError):self.select(person=incomplete)
        ConsentWithdrawal.objects.create(participant=self.person,actor=self.admin,reason='SYNTHETIC recorded withdrawal')
        with self.assertRaises(ValidationError):self.select()
        self.assertFalse(CandidateOpportunity.objects.exists())

    def test_exclusivity_blocks_another_employer_but_not_discovery(self):
        other=self.org(name='SYNTHETIC Exclusive source company')
        campaign=self.campaign(other)
        foundation.source_campaign(actor=self.admin,campaign_id=campaign.pk,participant_id=self.person.pk)
        self.criteria()
        self.assertTrue(matching.recommendations(self.admin,self.opportunity,{})[1])
        with self.assertRaises(ValidationError):self.select()

    def test_working_revision_does_not_replace_approved_matching_terms(self):
        criteria=self.criteria()
        payload=self.payload(self.organization);payload['title']='SYNTHETIC unapproved proposal'
        self.draft(self.organization,payload,need=self.need)
        current=selection.opportunity_for(self.admin,self.opportunity.pk)
        self.assertEqual(current.criteria.need_revision_id,current.need.effective_revision_id)
        self.assertEqual(current.criteria.need_revision_id,criteria.need_revision_id)
        self.assertNotEqual(current.need.working_revision_id,current.need.effective_revision_id)
        with self.assertRaises(ValidationError):
            criteria.rules=[];criteria.save()

    def test_new_approved_revision_requires_criteria_review_and_manual_provenance(self):
        old=self.criteria()
        payload=self.payload(self.organization);payload['title']='SYNTHETIC approved new title'
        self.draft(self.organization,payload,need=self.need)
        for action in ['submit','review','approve']:self.transition(self.organization,self.need,action)
        with self.assertRaises(ValidationError):matching.recommendations(self.admin,self.opportunity,{})
        with self.assertRaises(ValidationError):self.select(criteria_id=old.pk)
        selected=self.select()
        self.assertIsNone(selected.selection_criteria_id)
        self.need.refresh_from_db();self.assertEqual(selected.selected_need_revision_id,self.need.effective_revision_id)

    def test_conflict_acknowledgement_is_bound_to_current_activity(self):
        second_need=self.approved_need(self.organization)
        second=selection.create_opportunity(actor=self.admin,need_id=second_need.pk,owner_id=self.admin.pk)
        stale=selection.conflict_set(self.admin,self.person,excluding=second.pk)['fingerprint']
        self.select()
        with self.assertRaises(ValidationError):self.select(opportunity=second,expected_conflicts=stale,acknowledged=True)
        with self.assertRaises(ValidationError):self.select(opportunity=second,acknowledged=False)
        self.assertEqual(self.select(opportunity=second).opportunity,second)
        self.assertEqual(CandidateOpportunity.objects.filter(participant=self.person,active=True).count(),2)

    def test_duplicate_selection_and_identity_collisions_preserve_history(self):
        first=self.select();again=self.select();self.assertEqual(first.pk,again.pk)
        source=self.submit(email='synthetic15-alias@example.test',phone='2025550189',first_name='SYNTHETIC Alias',updates_preference='no').participant
        source_selected=self.select(person=source)
        before=foundation.consolidation_preview(source.pk,self.person.pk)
        foundation.consolidate(actor=self.admin,source_id=source.pk,target_id=self.person.pk,
            expected_fingerprint=before['fingerprint'],confirmed=True,why='SYNTHETIC established-person review')
        source_selected.refresh_from_db()
        self.assertFalse(source_selected.active);self.assertEqual(source_selected.merged_into,first)
        self.assertEqual(source_selected.source_participant,source)
        self.assertEqual(CandidateOpportunity.objects.filter(opportunity=self.opportunity,active=True).count(),1)
        self.assertEqual(CandidateOpportunity.objects.filter(opportunity=self.opportunity).count(),2)

    def test_interest_foundation_is_separate_from_official_pipeline(self):
        publication=OpportunityPublication.objects.create(opportunity=self.opportunity,need_revision=self.need.effective_revision,
            number=1,safe_details={'title':'SYNTHETIC approved job'},published_by=self.admin)
        interest=CandidateInterest.objects.create(opportunity=self.opportunity,publication=publication,participant=self.person,
            source_participant=self.person,submitted_by=self.admin)
        self.assertEqual(interest.status,'received');self.assertEqual(interest.source,'participant_portal')
        self.assertFalse(CandidateOpportunity.objects.exists())

    def test_unauthorized_opportunity_and_limited_evidence_remain_scoped(self):
        criteria=self.criteria()
        with self.assertRaises(PermissionDenied):selection.opportunity_for(self.recruiter,self.opportunity.pk)
        self.opportunity.owner=self.recruiter;self.opportunity.save()
        self.assertEqual(selection.opportunity_for(self.recruiter,self.opportunity.pk).pk,self.opportunity.pk)
        self.assertFalse(self.recruiter.has_perm('customers.manage_customers'))
        with self.assertRaises(PermissionDenied):selection.create_opportunity(actor=self.recruiter,need_id=self.need.pk,owner_id=self.recruiter.pk)
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        for url in [reverse('recruiting:matching-directory'),reverse('recruiting:matching',args=[self.opportunity.pk]),reverse('recruiting:criteria',args=[self.opportunity.pk]),reverse('recruiting:select-candidate',args=[self.opportunity.pk,self.person.pk])]:
            self.assertEqual(self.client.get(url).status_code,200)

    def official_experience(self):
        from apps.events.models import Event
        from apps.events.services import register_participant
        from apps.stations.models import ExperienceSession
        stamp=timezone.now()
        event=Event.objects.create(name='SYNTHETIC Build15 evidence event',start_at=stamp+timedelta(days=1),end_at=stamp+timedelta(days=2),status='upcoming')
        registration,_=register_participant(event=event,participant=self.person,actor=self.admin,source='staff')
        session=ExperienceSession.objects.create(event=event,registration=registration,participant=self.person,mode='official',started_at=stamp,completed_at=stamp)
        return event,registration,session

    def test_accepted_simulator_percent_and_profile_versions_stay_separate(self):
        from apps.simulators.models import SimulatorCaptureProfile,SimulatorCapture
        from apps.stations.models import Station
        event,registration,session=self.official_experience()
        profile=SimulatorCaptureProfile.objects.create(name='SYNTHETIC matching simulator',simulator_type='SYNTHETIC equipment',profile_version='1')
        station=Station.objects.create(code='synthetic15-simulator',name='SYNTHETIC simulator station',station_type='simulator')
        capture=SimulatorCapture.objects.create(submission_id=uuid.uuid4(),request_fingerprint='synthetic15',event=event,station=station,
            profile=profile,profile_version='1',profile_snapshot={'version':'1'},raw_identifier=str(registration.pk),
            normalized_identifier=str(registration.pk),raw_total_score='82.25',total_score=Decimal('82.25'),captured_at=timezone.now(),
            status='matched',mode='official',is_official=True,registration=registration,participant=self.person,experience_session=session)
        rule={'kind':'assessment','label':'Compatible official simulator','assessment_type':'simulator','profile':str(profile.pk),
            'profile_version':'1','minimum':'80'}
        criteria=self.criteria([rule]);result=matching.score_candidate(self.admin,self.person,criteria)
        self.assertEqual(result['components'][0]['status'],'strength');self.assertIn('82.25',result['components'][0]['detail'])
        rule['profile_version']='2';criteria2=self.criteria([rule])
        self.assertEqual(matching.score_candidate(self.admin,self.person,criteria2)['components'][0]['status'],'unknown')
        restricted=matching.score_candidate(self.recruiter,self.person,criteria)
        self.assertEqual(restricted['components'][0]['status'],'unknown');self.assertNotIn('82.25',restricted['components'][0]['detail'])
        capture.mode='demo';capture.is_official=False;capture.save()
        self.assertEqual(matching.score_candidate(self.admin,self.person,criteria)['components'][0]['status'],'unknown')

    def test_finalized_quiz_comparison_is_pinned_to_question_snapshot(self):
        from apps.assessments.models import AssessmentCategory,QuestionSet,QuizAttempt,AssessmentSection,AssessmentResponse
        event,registration,session=self.official_experience();stamp=timezone.now()
        category=AssessmentCategory.objects.create(name='SYNTHETIC matching quiz')
        question_set=QuestionSet.objects.create(category=category,name='SYNTHETIC question set',version=1,status='published')
        attempt=QuizAttempt.objects.create(experience_session=session,participant=self.person,is_official=True,status='complete',completed_at=stamp)
        questions=[{'key':'synthetic-one'},{'key':'synthetic-two'}]
        section=AssessmentSection.objects.create(attempt=attempt,category=category,question_set=question_set,position=1,
            category_name_snapshot=category.name,questions_snapshot=questions,status='complete',completed_at=stamp)
        AssessmentResponse.objects.create(section=section,question_position=1,selected_choice_key='synthetic-choice',is_correct=True)
        digest=hashlib.sha256(json.dumps(questions,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        rule={'kind':'assessment','label':'Pinned official quiz','assessment_type':'quiz','category':str(category.pk),'snapshot_hash':digest,'minimum':'50'}
        criteria=self.criteria([rule]);result=matching.score_candidate(self.admin,self.person,criteria)
        self.assertEqual(result['components'][0]['status'],'strength');self.assertIn('1/2',result['components'][0]['detail'])
        rule['snapshot_hash']='b'*64;other=self.criteria([rule])
        self.assertEqual(matching.score_candidate(self.admin,self.person,other)['components'][0]['status'],'unknown')
        attempt.status='void';attempt.voided_at=timezone.now();attempt.save()
        self.assertEqual(matching.score_candidate(self.admin,self.person,criteria)['components'][0]['status'],'unknown')
