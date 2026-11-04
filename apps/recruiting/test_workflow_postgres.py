from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import connection,close_old_connections
from django.test import TransactionTestCase
from .test_workflow import WorkflowFixtures
from .models import RecruitingStage,CandidateInterest
from . import jobs,workflow


@skipUnless(connection.vendor=='postgresql','PostgreSQL recruiting workflow concurrency')
class WorkflowConcurrencyTests(WorkflowFixtures,TransactionTestCase):
    def setUp(self):
        for key,label in [('identified','Identified'),('interview','Interview')]:RecruitingStage.objects.get_or_create(pk=key,defaults={'label':label})
        self.workflow_setup();self.publish()
    def together(self,actions):
        barrier=Barrier(len(actions))
        def worker(action):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                try:return {'ok':str(action())}
                except (ValidationError,PermissionDenied) as exc:return {'rejected':str(exc)}
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:return list(pool.map(worker,actions))
    def user(self,pk):return get_user_model().objects.get(pk=pk)
    def interest_action(self):return jobs.express_interest(actor=self.user(self.portal.pk),opportunity_id=self.opportunity.pk,publication_id=self.opportunity.publication_id).pk
    def test_duplicate_interest_is_single_attributed_record(self):
        results=self.together([self.interest_action,self.interest_action])
        self.assertEqual(len({r['ok'] for r in results}),1);self.assertEqual(CandidateInterest.objects.count(),1)
    def test_interest_versus_close_has_a_serialized_truthful_result(self):
        revision=self.opportunity.revision
        def close():return workflow.publish(actor=self.user(self.admin.pk),opportunity_id=self.opportunity.pk,expected_revision=revision,action='close',why='SYNTHETIC concurrent closing').pk
        self.together([self.interest_action,close]);self.opportunity.refresh_from_db()
        self.assertEqual(self.opportunity.publication_state,'closed')
        self.assertLessEqual(CandidateInterest.objects.count(),1)
        if not CandidateInterest.objects.exists():
            with self.assertRaises(ValidationError):self.interest_action()
    def test_stage_retirement_versus_transition_preserves_valid_history(self):
        candidate=self.select();stage=RecruitingStage.objects.get(pk='interview')
        def retire():return workflow.configure_stage(actor=self.user(self.admin.pk),key=stage.pk,label=stage.label,position=stage.position,is_active=False,expected_revision=stage.revision,why='SYNTHETIC concurrent retirement').pk
        def advance():return workflow.transition(actor=self.user(self.admin.pk),candidate_id=candidate.pk,stage_key='interview',expected_revision=candidate.revision,why='SYNTHETIC concurrent advancement').pk
        self.together([retire,advance]);candidate.refresh_from_db();stage.refresh_from_db()
        self.assertFalse(stage.is_active);self.assertIn(candidate.stage_id,{'identified','interview'})
        if candidate.stage_id=='interview':self.assertEqual(candidate.revision,1)
