from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from apps.staffing.models import StaffProfile
from .roles import role_configuration
from .staff_onboarding import add_staff_member, selection_token


@skipUnless(connection.vendor == 'postgresql', 'Row-lock concurrency requires PostgreSQL.')
class StaffSetupConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actors = [get_user_model().objects.create_user(username='owner3-race-'+str(i), is_staff=True, is_superuser=True) for i in range(2)]
        role_configuration(apply=True)

    def race(self, entries):
        barrier = Barrier(2)
        def run(entry):
            close_old_connections()
            actor, data = entry
            try:
                actor = get_user_model().objects.get(pk=actor.pk)
                barrier.wait(timeout=10)
                profile, invitation, form = add_staff_member(actor=actor, data=data)
                return ('saved', str(profile.pk)) if profile else ('validation', str(form.errors))
            except ValidationError:
                return ('validation', None)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(run, entries))
        self.assertEqual(sorted(r[0] for r in result), ['saved', 'validation'])

    def data(self, **extra):
        return dict(first_name='Race', last_name='Fixture', email='race@example.com', is_active=True,
            login_mode='new', username='owner3-race-new', login_email='race-login@example.com',
            roles=['event_staff'], my_pay_access='no', confirm_identity=True, **extra)

    def test_concurrent_create_does_not_duplicate_profile_or_account(self):
        self.race([(actor, self.data()) for actor in self.actors])
        self.assertEqual(StaffProfile.objects.filter(email='race@example.com').count(), 1)
        self.assertEqual(get_user_model().objects.filter(username='owner3-race-new').count(), 1)

    def test_concurrent_existing_profile_connection_has_no_partial_account(self):
        profile = StaffProfile.objects.create(first_name='Existing', last_name='Race')
        entries = []
        for index, actor in enumerate(self.actors):
            data = self.data(existing_profile=selection_token(actor, profile))
            data.update(username='owner3-linked-'+str(index), login_email='linked-'+str(index)+'@example.com')
            entries.append((actor, data))
        self.race(entries)
        profile.refresh_from_db()
        self.assertIsNotNone(profile.user_id)
        self.assertEqual(StaffProfile.objects.count(), 1)
        self.assertEqual(get_user_model().objects.filter(username__startswith='owner3-linked-').count(), 1)
