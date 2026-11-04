from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from apps.participants.identity import canonical_id
from .test_matching import MatchingFixtures
from .models import RecruitingStage, CandidateOpportunity
from . import selection, foundation


@skipUnless(connection.vendor=='postgresql','PostgreSQL recruiting concurrency')
class MatchingConcurrencyTests(MatchingFixtures,TransactionTestCase):
    def setUp(self):
        RecruitingStage.objects.get_or_create(pk='identified',defaults={'label':'Identified','semantic':'active'})
        self.matching_setup()

    def together(self,function):
        barrier=Barrier(2)
        def job(index):
            close_old_connections()
            try:
                user=get_user_model().objects.get(pk=self.admin.pk)
                barrier.wait(timeout=15)
                try:return {'value':str(function(user,index))}
                except ValidationError as exc:return {'rejected':exc.messages}
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(job,range(2)))

    def test_duplicate_active_selection_is_idempotent(self):
        ack=selection.conflict_set(self.admin,self.person,excluding=self.opportunity.pk)['fingerprint']
        results=self.together(lambda user,index:selection.select_candidate(actor=user,opportunity_id=self.opportunity.pk,
            participant_id=self.person.pk,expected_conflicts=ack,acknowledged=False,why='SYNTHETIC concurrent selection').pk)
        self.assertEqual(len({r['value'] for r in results}),1)
        self.assertEqual(CandidateOpportunity.objects.filter(opportunity=self.opportunity,active=True).count(),1)

    def test_parallel_other_opportunities_cannot_replay_empty_conflict_ack(self):
        second=self.approved_need(self.organization)
        opportunity=selection.create_opportunity(actor=self.admin,need_id=second.pk,owner_id=self.admin.pk)
        ids=[self.opportunity.pk,opportunity.pk]
        ack=selection.conflict_set(self.admin,self.person)['fingerprint']
        results=self.together(lambda user,index:selection.select_candidate(actor=user,opportunity_id=ids[index],
            participant_id=self.person.pk,expected_conflicts=ack,acknowledged=True,why='SYNTHETIC concurrent engagements').pk)
        self.assertEqual(sum('value' in r for r in results),1)
        self.assertEqual(sum('rejected' in r for r in results),1)
        self.assertEqual(CandidateOpportunity.objects.filter(participant=self.person,active=True).count(),1)

    def test_consolidation_versus_selection_keeps_one_current_identity(self):
        source=self.submit(email='synthetic15-pg-alias@example.test',phone='2025550188',updates_preference='no').participant
        comparison=foundation.consolidation_preview(source.pk,self.person.pk)
        ack=selection.conflict_set(self.admin,source,excluding=self.opportunity.pk)['fingerprint']
        def action(user,index):
            if index==0:
                return foundation.consolidate(actor=user,source_id=source.pk,target_id=self.person.pk,
                    expected_fingerprint=comparison['fingerprint'],confirmed=True,why='SYNTHETIC concurrent identity review').pk
            return selection.select_candidate(actor=user,opportunity_id=self.opportunity.pk,participant_id=source.pk,
                expected_conflicts=ack,acknowledged=False,why='SYNTHETIC concurrent manual selection').pk
        self.together(action)
        rows=list(CandidateOpportunity.objects.filter(opportunity=self.opportunity,active=True))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].participant_id,canonical_id(rows[0].source_participant_id))
