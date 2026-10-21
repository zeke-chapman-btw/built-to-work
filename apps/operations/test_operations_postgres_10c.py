"""PostgreSQL-only row-lock and published-history tests using disposable Django test DBs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event as ThreadEvent
from time import monotonic
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, connections, close_old_connections, transaction, IntegrityError
from django.test import TransactionTestCase
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from .template_models import OperationsTemplateResource, EventOperationsApplication
from . import template_services as svc

@skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL for actual locking')
class OperationsPostgres10CTests(TransactionTestCase):
    def setUp(self):
        self.actor=get_user_model().objects.create_superuser('synthetic-10c-pg',email='pg@example.test',password=None)
        now=timezone.now()
        self.event=Event.objects.create(name='SYNTHETIC PostgreSQL event',start_at=now+timedelta(days=2),end_at=now+timedelta(days=3),status='upcoming')
        self.revision=svc.save_draft(actor=self.actor,values=dict(name='SYNTHETIC PostgreSQL template',description='',question_sets=[],stations=[],game=None,not_applicable=[],registration_group_mode='optional',registration_group_label='SYNTHETIC group',registration_capacity=40))

    def publish(self):
        return svc.publish(actor=self.actor,revision=self.revision,expected_version=self.revision.updated_at.isoformat())

    def token(self,revision):
        plan=svc.application_plan(actor=self.actor,event=self.event,revision=revision)
        self.assertFalse(plan['conflicts'])
        return svc.preview_token(actor=self.actor,event=self.event,revision=revision,plan=plan)

    def worker(self,operation):
        close_old_connections()
        try:
            with connections['default'].cursor() as cursor:
                cursor.execute("SET lock_timeout = '4s'")
                cursor.execute("SET statement_timeout = '8s'")
            return operation()
        finally:
            connections['default'].close()

    def test_concurrent_same_confirmation_applies_once_and_returns_same_history(self):
        revision=self.publish();token=self.token(revision);barrier=Barrier(2)
        def apply():
            barrier.wait(timeout=5)
            application,created=svc.apply_template(actor=self.actor,event=self.event,revision=revision,token=token)
            return str(application.pk),created
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(self.worker,apply) for _ in range(2)]
            results=[f.result(timeout=12) for f in futures]
        self.assertEqual(results[0][0],results[1][0]);self.assertEqual(sum(created for _,created in results),1)
        self.assertEqual(EventOperationsApplication.objects.filter(event=self.event).count(),1)
        self.assertEqual(AuditLog.objects.filter(action='operations.template.applied').count(),1)
        self.event.refresh_from_db();self.assertEqual(self.event.registration_capacity,40)

    def test_concurrent_different_confirmations_never_replace_first_application(self):
        revision=self.publish();tokens=[self.token(revision),self.token(revision)];barrier=Barrier(2)
        def apply(token):
            barrier.wait(timeout=5)
            try:
                application,created=svc.apply_template(actor=self.actor,event=self.event,revision=revision,token=token)
                return 'applied' if created else 'retry'
            except ValidationError as exc:
                self.assertIn('already has an applied template',str(exc));return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(self.worker,lambda token=token:apply(token)) for token in tokens]
            results=[f.result(timeout=12) for f in futures]
        self.assertCountEqual(results,['applied','blocked'])
        self.assertEqual(EventOperationsApplication.objects.filter(event=self.event).count(),1)
        self.assertEqual(AuditLog.objects.filter(action='operations.template.applied').count(),1)

    def test_resource_insert_waits_for_publication_then_database_guard_rejects_it(self):
        published=ThreadEvent();release=ThreadEvent();attempted=ThreadEvent();pid_holder=[]
        revision=self.revision
        def publish_and_hold():
            with transaction.atomic():
                svc.publish(actor=self.actor,revision=revision,expected_version=revision.updated_at.isoformat())
                published.set()
                if not release.wait(timeout=8):raise AssertionError('Test did not release publication transaction')
        def insert_resource():
            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()');pid_holder.append(cursor.fetchone()[0])
            attempted.set()
            try:
                OperationsTemplateResource.objects.bulk_create([OperationsTemplateResource(revision_id=revision.pk,kind='registration',options={'group_mode':'optional','group_label':'SYNTHETIC','capacity':20})])
            except IntegrityError as exc:
                return getattr(exc.__cause__,'sqlstate',None),str(exc)
            return None,'insert unexpectedly succeeded'
        before=OperationsTemplateResource.objects.filter(revision=revision).count()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.worker,publish_and_hold)
            try:
                self.assertTrue(published.wait(timeout=5))
                second=pool.submit(self.worker,insert_resource)
                self.assertTrue(attempted.wait(timeout=5))
                waiting=False;deadline=monotonic()+2.5
                while monotonic()<deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s',[pid_holder[0]])
                        row=cursor.fetchone()
                    if row and row[0]=='Lock':waiting=True;break
                    ThreadEvent().wait(.02)
                self.assertTrue(waiting,'Resource insertion did not wait on the publication row lock')
            finally:
                release.set()
            first.result(timeout=12);code,message=second.result(timeout=12)
        self.assertEqual(code,'23514');self.assertIn('Published operations resources are immutable',message)
        self.assertEqual(OperationsTemplateResource.objects.filter(revision=revision).count(),before)

    def test_application_history_is_protected_below_orm_on_postgresql(self):
        revision=self.publish();application,_=svc.apply_template(actor=self.actor,event=self.event,revision=revision,token=self.token(revision))
        for operation in (lambda:EventOperationsApplication.objects.filter(pk=application.pk).update(event_before_digest='tamper'),lambda:EventOperationsApplication.objects.filter(pk=application.pk).delete()):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():operation()
        application.refresh_from_db();self.assertEqual(application.snapshot,revision.snapshot)
