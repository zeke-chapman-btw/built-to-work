import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.db import close_old_connections,connection
from django.test import TransactionTestCase,override_settings
from django.utils import timezone
from unittest import skipUnless
from apps.events.models import Event,TrailerInstance,OfflineEventPreparation
from .models import ControlConfiguration,SyncRunRequest,Device,PublicationGrant
from .services import request_sync,accept_heartbeat,enroll_device,publish_score
from apps.participants.models import Participant
from apps.events.models import EventRegistration

@skipUnless(connection.vendor=="postgresql","PostgreSQL row-lock concurrency verification requires isolated PostgreSQL")
@override_settings(TRAILER_CONTROL_ENABLED=True,TRAILER_CONTROL_IDENTITY="SYNTHETIC-TCC-PG")
class TrailerConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor=get_user_model().objects.create_superuser(username="synthetic-pg",password="test-only")
        self.trailer=TrailerInstance.objects.create(identity="SYNTHETIC-TCC-PG",display_name="Synthetic PG")
        now=timezone.now();event=Event.objects.create(name="Synthetic",start_at=now,end_at=now+timedelta(hours=1))
        self.prep=OfflineEventPreparation.objects.create(trailer=self.trailer,event=event)
        ControlConfiguration.objects.create(trailer=self.trailer,preparation=self.prep)

    def parallel(self,fn):
        gate=threading.Barrier(2)
        def task(i):
            close_old_connections()
            try:gate.wait();return fn(i)
            finally:close_old_connections()
        with ThreadPoolExecutor(2) as pool:return list(pool.map(task,range(2)))

    def test_concurrent_requests_one_active_row(self):
        def run(i):
            actor=get_user_model().objects.get(pk=self.actor.pk)
            trailer=TrailerInstance.objects.get(pk=self.trailer.pk)
            job,_=request_sync(actor,trailer,direction="outbound",key=uuid.uuid4(),reason="Concurrent request")
            return job.pk
        ids=self.parallel(run)
        self.assertEqual(ids[0],ids[1]);self.assertEqual(SyncRunRequest.objects.count(),1)

    def test_concurrent_publication_preserves_single_alias(self):
        p=Participant.objects.create(first_name="Synthetic",last_name="Person")
        EventRegistration.objects.create(event=self.prep.event,participant=p)
        def run(i):
            actor=get_user_model().objects.get(pk=self.actor.pk)
            prep=OfflineEventPreparation.objects.select_related("event").get(pk=self.prep.pk)
            return publish_score(actor,prep,p.pk,publish=True,reason="Concurrent explicit grant").alias
        aliases=self.parallel(run)
        self.assertEqual(aliases[0],aliases[1]);self.assertEqual(PublicationGrant.objects.count(),1)
