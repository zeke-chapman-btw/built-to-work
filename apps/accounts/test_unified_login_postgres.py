"""PostgreSQL-only simultaneous replay of credential-proven selection state."""
import copy
import time
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection, connections
from django.test import TransactionTestCase, RequestFactory
from apps.customers.test_portal import PortalFixtures
from . import unified_auth as auth

@skipUnless(connection.vendor == 'postgresql','Requires isolated PostgreSQL row locks')
class UnifiedSelectionConcurrencyTests(PortalFixtures,TransactionTestCase):
    def setUp(self):
        type(self).setUpTestData()

    def test_same_nonce_can_establish_only_one_session_under_concurrent_replay(self):
        item={'realm':'staff','user':str(self.admin.pk),'proof':self.admin.get_session_auth_hash()}
        payload={'created':time.time(),'nonce':secrets.token_urlsafe(32),'identities':[item],'next':''}
        barrier=Barrier(2)
        def replay():
            connections.close_all()
            try:
                request=RequestFactory().post('/accounts/login/choose/')
                request.user=AnonymousUser();request.session=SessionStore()
                request.session[auth.PENDING]=copy.deepcopy(payload)
                barrier.wait(timeout=15)
                return bool(auth.choose(request,payload['nonce'],'0'))
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:replay(),range(2)))
        self.assertEqual(sum(results),1)
