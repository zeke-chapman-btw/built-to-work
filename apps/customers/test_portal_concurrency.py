"""PostgreSQL-only invitation and acceptance races on independent connections."""
from unittest import skipUnless
from django.db import connection
from django.test import TransactionTestCase
from apps.customers.test_portal import PortalFixtures, PASSWORD
from apps.customers import test_concurrency as legacy_concurrency
from apps.customers import invitations as i, services as s
from apps.customers.models import CustomerMembership as Membership, CustomerAccessToken as Token


@skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL independent transactions')
class PortalConcurrencyTests(PortalFixtures, TransactionTestCase):
    race = legacy_concurrency.CustomerConcurrencyTests.race

    def setUp(self):
        self.setUpTestData()

    def test_last_member_invitation_reserves_exactly_one_seat(self):
        org, lead, user = self.lead()
        self.plan(org, member_limit=1)
        revision = org.revision
        def invite(actor, email):
            return lambda: i.invite_member(actor=actor, org_id=org.pk, expected_org_revision=revision,
                email=email, first_name='SYNTHETIC', origin='https://testserver')
        self.race(invite(user, 'race-one@example.test'), invite(self.admin, 'race-two@example.test'))
        self.assertEqual(s.seat_usage(org)['member'], 1)
        self.assertEqual(Membership.objects.filter(organization=org, role='member').count(), 1)
        self.assertEqual(Token.objects.filter(membership__organization=org, membership__role='member', purpose='invite', revoked_at__isnull=True).count(), 1)

    def test_invitation_acceptance_is_single_use_under_race(self):
        org = self.portal_org(); member = self.membership(org)
        item, raw = self.issue(member)
        self.race(lambda: i.accept_invitation(raw=raw, password=PASSWORD), lambda: i.accept_invitation(raw=raw, password=PASSWORD))
        self.assertEqual(s.seat_usage(org)['lead'], 1)
        self.assertEqual(Membership.objects.filter(pk=member.pk, accepted_at__isnull=False).count(), 1)

    def test_existing_identity_acceptance_race_does_not_add_a_second_seat(self):
        a, lead, user = self.lead()
        b = self.portal_org(name='SYNTHETIC second company')
        member = self.membership(b, identity=lead.identity)
        item, raw = self.issue(member)
        self.race(lambda: i.accept_invitation(raw=raw, actor=user), lambda: i.accept_invitation(raw=raw, actor=user))
        self.assertEqual(s.seat_usage(b)['lead'], 1)
