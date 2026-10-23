"""PostgreSQL-only account, registration and delivery races with disposable fixtures."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.db import connection, close_old_connections
from django.test import TransactionTestCase, override_settings
from django.core.exceptions import ValidationError
from django.core import mail
from . import test_portal_12 as fixture
from .models import Participant, ParticipantAccount, PortalInvitation, PortalDelivery
from apps.events.models import EventRegistration, QrTicket
from .portal_accounts import activate, invite_token, deliver_invitation
from .portal_services import register_for_event, contract_hash


@skipUnless(connection.vendor == 'postgresql', 'Row-lock races require PostgreSQL')
@override_settings(PARTICIPANT_PORTAL_BASE_URL='https://portal.example.test')
class PortalConcurrency12Tests(TransactionTestCase):
    submit = fixture.Portal12Tests.submit
    event = fixture.Portal12Tests.event
    def setUp(self):
        fixture.Portal12Tests.setUp(self)
    def race(self, *calls):
        barrier = Barrier(len(calls))
        def run(call):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return call()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return [future.result(timeout=45) for future in [pool.submit(run, call) for call in calls]]

    def test_two_activation_posts_create_one_account(self):
        token = invite_token(self.invite)
        def claim():
            try:
                return activate(token, fixture.PASSWORD).pk
            except ValidationError:
                return None
        results = self.race(claim, claim)
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(ParticipantAccount.objects.count(), 1)
        self.assertEqual(Participant.objects.count(), 1)

    def test_duplicate_registration_posts_keep_one_qr(self):
        account = activate(invite_token(self.invite), fixture.PASSWORD)
        event = self.event()
        stamp = contract_hash(event)
        def register():
            registration, created = register_for_event(account.user, event, {'consent': True}, stamp)
            return str(registration.pk), created
        results = self.race(register, register)
        self.assertEqual(len({result[0] for result in results}), 1)
        self.assertEqual(sum(result[1] for result in results), 1)
        self.assertEqual(QrTicket.objects.count(), 1)

    def test_capacity_one_never_overbooks(self):
        one = activate(invite_token(self.invite), fixture.PASSWORD)
        source = self.submit(email='second@example.test', phone='5550109191')
        two = activate(invite_token(PortalInvitation.objects.get(participant=source.participant)), fixture.PASSWORD)
        event = self.event(registration_capacity=1)
        stamp = contract_hash(event)
        def register(account):
            try:
                return str(register_for_event(account.user, event, {'consent': True}, stamp)[0].pk)
            except ValidationError:
                return None
        results = self.race(lambda: register(one), lambda: register(two))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(EventRegistration.objects.count(), 1)

    def test_delivery_claim_sends_only_once(self):
        # Initial callback ran after fixture commit; make only the reminder due.
        from django.utils import timezone
        mail.outbox.clear()
        delivery = self.invite.deliveries.get(slot='day3')
        PortalDelivery.objects.filter(pk=delivery.pk).update(due_at=timezone.now())
        self.race(lambda: deliver_invitation(delivery.pk), lambda: deliver_invitation(delivery.pk))
        self.assertEqual(len(mail.outbox), 1)
