"""PostgreSQL writers race over the canonical current-preference revision."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection,connections
from django.test import TransactionTestCase
from apps.recruiting.models import RecruitingProfile
from apps.recruiting.foundation import save_preferences
from .models import Participant,ParticipantCareerProfile

@skipUnless(connection.vendor=='postgresql','PostgreSQL row-lock semantics required')
class CurrentPreferenceConcurrencyTests(TransactionTestCase):
    def test_two_stale_forms_cannot_overwrite_current_status_or_legacy_facts(self):
        actor=get_user_model().objects.create_superuser(username='SYNTHETIC-concurrency')
        person=Participant.objects.create(first_name='SYNTHETIC',last_name='Concurrent preference')
        ParticipantCareerProfile.objects.create(participant=person,employment_status='SYNTHETIC retained',preferred_work_type='contract')
        RecruitingProfile.objects.create(participant=person)
        barrier=Barrier(2)
        def writer(value):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout='4s'")
                    cursor.execute("SET statement_timeout='8s'")
                user=get_user_model().objects.get(pk=actor.pk)
                barrier.wait(timeout=10)
                try:
                    save_preferences(actor=user,participant_id=person.pk,data={'current_employment_status':value,'willing_to_relocate':'maybe'},expected_revision=0,why='SYNTHETIC simultaneous form')
                    return 'saved'
                except ValidationError:
                    return 'stale'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(writer,value) for value in ['full_time','unemployed']]
            results=[future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(results),['saved','stale'])
        profile=RecruitingProfile.objects.get(participant=person)
        self.assertEqual(profile.revision,1)
        self.assertIn(profile.current_employment_status,['full_time','unemployed'])
        self.assertEqual(profile.relocation_preference,'maybe')
        career=ParticipantCareerProfile.objects.get(participant=person)
        self.assertEqual(career.employment_status,'SYNTHETIC retained')
        self.assertEqual(career.preferred_work_type,'contract')
