"""Real PostgreSQL concurrency; SQLite skips are explicitly reported."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.db import connection, close_old_connections
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from apps.participants.models import Participant
from .models import RecruitingLock, CandidateListEntry
from .foundation import save_list, change_list_member, consolidation_preview, consolidate


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL row-lock verification')
class FoundationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser(username='synthetic15-pg', password='synthetic-only-test')
        self.person=Participant.objects.create(first_name='SYNTHETIC', last_name='Concurrent person')
        RecruitingLock.objects.get_or_create(pk=1)
        self.list=save_list(actor=self.admin,name='SYNTHETIC concurrent list')

    def parallel(self, operation):
        barrier=Barrier(2)
        def job(index):
            close_old_connections()
            try:
                actor=get_user_model().objects.get(pk=self.admin.pk)
                barrier.wait(timeout=15)
                return operation(actor,index)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(job,range(2)))

    def test_duplicate_list_add_is_one_record(self):
        result=self.parallel(lambda actor,index: str(change_list_member(actor=actor,list_id=self.list.pk,participant_id=self.person.pk).pk))
        self.assertEqual(len(set(result)),1)
        self.assertEqual(CandidateListEntry.objects.filter(candidate_list=self.list,removed_at__isnull=True).count(),1)

    def test_duplicate_alias_adds_share_same_current_identity(self):
        source=Participant.objects.create(first_name='SYNTHETIC',last_name='Concurrent alias')
        preview=consolidation_preview(source.pk,self.person.pk)
        consolidate(actor=self.admin,source_id=source.pk,target_id=self.person.pk,expected_fingerprint=preview['fingerprint'],confirmed=True,why='SYNTHETIC same-person review')
        ids=[source.pk,self.person.pk]
        self.parallel(lambda actor,index: str(change_list_member(actor=actor,list_id=self.list.pk,participant_id=ids[index]).pk))
        self.assertEqual(CandidateListEntry.objects.filter(candidate_list=self.list,removed_at__isnull=True).count(),1)
