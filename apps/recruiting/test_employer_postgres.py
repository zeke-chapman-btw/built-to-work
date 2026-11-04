from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import connection,close_old_connections
from django.test import TransactionTestCase
from apps.customers import services as customer_services
from .test_employer import EmployerFixtures
from .models import RecruitingStage,EmployerCandidateFeedback
from . import employer,foundation


@skipUnless(connection.vendor=='postgresql','PostgreSQL disclosure and membership concurrency')
class EmployerConcurrencyTests(EmployerFixtures,TransactionTestCase):
    def setUp(self):
        RecruitingStage.objects.get_or_create(pk='identified',defaults={'label':'Identified','semantic':'active'})
        self.employer_setup();self.grant=self.release()
    def user(self,pk):return get_user_model().objects.get(pk=pk)
    def together(self,actions):
        barrier=Barrier(len(actions))
        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                try:return {'ok':action()}
                except (ValidationError,PermissionDenied) as exc:return {'rejected':str(exc)}
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:return list(pool.map(run,actions))
    def response(self,actor=None,operation=None):
        return str(employer.feedback(actor=actor or self.user(self.customer.pk),organization_id=self.organization.pk,
            disclosure_id=self.grant.pk,kind='interview',message='SYNTHETIC concurrent employer response',operation_id=operation or uuid.uuid4()).pk)
    def test_replayed_employer_actions_are_single_attributed_responses(self):
        operation=uuid.uuid4();result=self.together([lambda:self.response(operation=operation),lambda:self.response(operation=operation)])
        self.assertEqual(len({r['ok'] for r in result}),1);self.assertEqual(EmployerCandidateFeedback.objects.count(),1)
    def test_withdrawal_versus_feedback_stops_every_subsequent_access(self):
        def withdraw():return str(employer.record_withdrawal(actor=self.user(self.admin.pk),participant_id=self.person.pk,why='SYNTHETIC concurrent recorded withdrawal').pk)
        self.together([withdraw,self.response])
        self.assertLessEqual(EmployerCandidateFeedback.objects.count(),1)
        self.assertEqual(employer.list_candidates(self.customer,self.organization.pk),[])
        with self.assertRaises(PermissionDenied):self.response()
    def test_withdrawal_versus_read_has_a_linearizable_result(self):
        def read():return employer.detail(self.user(self.customer.pk),self.organization.pk,self.grant.pk)['id']
        def withdraw():return str(employer.record_withdrawal(actor=self.user(self.admin.pk),participant_id=self.person.pk,why='SYNTHETIC withdrawal versus read').pk)
        result=self.together([withdraw,read]);self.assertEqual(len(result),2)
        with self.assertRaises(PermissionDenied):employer.detail(self.customer,self.organization.pk,self.grant.pk)
    def test_membership_revocation_versus_response_preserves_organization_guards(self):
        member=self.membership(self.organization,role='member');user=self.activate(member)
        employer.member_permission(actor=self.admin,disclosure_id=self.grant.pk,membership_id=member.pk,
            can_respond=True,expected_revision=0,why='SYNTHETIC scoped Member permission')
        self.organization.refresh_from_db();member.refresh_from_db()
        org_revision=self.organization.revision;member_revision=member.revision
        def revoke():
            customer_services.save_membership(actor=self.user(self.admin.pk),org_id=self.organization.pk,
                expected_org_revision=org_revision,membership_id=member.pk,expected_revision=member_revision,
                role='member',state='suspended',designate_primary=False,why='SYNTHETIC concurrent membership suspension')
            return 'suspended'
        self.together([revoke,lambda:self.response(actor=self.user(user.pk))])
        member.refresh_from_db();self.assertEqual(member.state,'suspended')
        with self.assertRaises(PermissionDenied):self.response(actor=user)
    def test_exclusivity_early_release_versus_response_never_bypasses_active_restriction(self):
        other=self.org(name='SYNTHETIC concurrent sourcing employer');campaign=self.campaign(other)
        source=foundation.source_campaign(actor=self.admin,campaign_id=campaign.pk,participant_id=self.person.pk)
        def release():return str(foundation.release_exclusivity(actor=self.user(self.admin.pk),source_id=source.pk,why='SYNTHETIC concurrent authorized early release').pk)
        self.together([release,self.response])
        self.assertEqual(len(employer.list_candidates(self.customer,self.organization.pk)),1)
        self.assertLessEqual(EmployerCandidateFeedback.objects.count(),1)
