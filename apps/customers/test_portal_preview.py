"""Admin review preserves staff authentication and cannot change customer data."""
import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection, close_old_connections
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.urls import reverse
from apps.accounts.roles import role_configuration
from apps.core.models import AuditLog
from . import portal_preview as preview
from .models import CustomerIdentity, CustomerOrganization, WorkforceNeed, Campaign

GATE='Basic '+base64.b64encode(b'preview-reviewer:synthetic-test-only').decode()
OPTIONS=dict(BTW_PREVIEW_GATE_ENABLED=True, BTW_PREVIEW_GATE_USERNAME='preview-reviewer',
    BTW_PREVIEW_GATE_PASSWORD='synthetic-test-only',EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')


@override_settings(**OPTIONS)
class CustomerPortalPreviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        role_configuration(apply=True)
        User=get_user_model()
        cls.admin=User.objects.create_superuser('synthetic-customer-preview-admin','admin-preview@example.test','synthetic-test-password')
        cls.other=User.objects.create_superuser('synthetic-other-customer-preview','other-preview@example.test','synthetic-test-password')
        cls.staff=User.objects.create_user('synthetic-review-staff',is_staff=True)

    def setUp(self):
        self.client.defaults['HTTP_AUTHORIZATION']=GATE
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')

    def enter(self):
        response=self.client.post(reverse('internal:portal-preview-customer-enter'))
        self.assertRedirects(response,reverse('customer_portal:home'))
        return CustomerIdentity.objects.get(email=preview.fixture_email(self.admin))

    def test_landing_has_both_review_buttons_without_seeding(self):
        response=self.client.get(reverse('internal:portal-preview'))
        self.assertContains(response,'Go to Customer Portal')
        self.assertContains(response,'Go to Participant Portal')
        self.assertEqual(CustomerIdentity.objects.count(),0)

    def test_entry_exit_and_header_return_preserve_admin_identity(self):
        original=self.admin.password
        self.enter()
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))
        self.assertEqual(self.client.session['_auth_user_backend'],'django.contrib.auth.backends.ModelBackend')
        response=self.client.get(reverse('customer_portal:home'))
        self.assertContains(response,'Admin Preview — Customer Portal')
        self.assertContains(response,'Return to Admin')
        self.assertContains(response,'View only')
        self.assertEqual(response.wsgi_request.user.pk,self.admin.pk)
        self.assertRedirects(self.client.post(reverse('customer_portal:logout')),reverse('internal:portal-preview'))
        self.assertNotIn(preview.SESSION_KEY,self.client.session)
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))
        self.admin.refresh_from_db();self.assertEqual(self.admin.password,original)
        self.assertEqual(self.client.get('/internal/').status_code,200)

    def test_fixture_is_reused_and_never_sends_mail(self):
        first=self.enter();counts=(CustomerIdentity.objects.count(),CustomerOrganization.objects.count(),WorkforceNeed.objects.count(),Campaign.objects.count())
        self.client.post(reverse('internal:portal-preview-customer-exit'))
        second=self.enter()
        self.assertEqual(first.pk,second.pk)
        self.assertEqual(counts,(CustomerIdentity.objects.count(),CustomerOrganization.objects.count(),WorkforceNeed.objects.count(),Campaign.objects.count()))
        self.assertEqual(len(mail.outbox),0)
        self.assertFalse(first.user.is_staff or first.user.is_superuser or first.user.groups.exists())

    def test_customer_tabs_details_and_edit_forms_are_read_only(self):
        identity=self.enter();member=identity.memberships.get();org=member.organization
        need=org.workforce_needs.get();campaign=org.campaigns.get()
        counts=(AuditLog.objects.count(),WorkforceNeed.objects.count(),Campaign.objects.count())
        paths=[reverse('customer_portal:workspace',args=[tab]) for tab in ('overview','company','needs','campaigns','users','access','history')]
        paths += [reverse('customer_portal:need',args=[need.pk]),reverse('customer_portal:need-edit',args=[need.pk]),
            reverse('customer_portal:campaign',args=[campaign.pk]),reverse('customer_portal:campaign-edit',args=[campaign.pk])]
        for path in paths:
            response=self.client.get(path)
            self.assertContains(response,'Admin Preview — Customer Portal')
            self.assertContains(response,'disabled')
            self.assertIn('no-store',response['Cache-Control'])
        for path in [reverse('customer_portal:need-create'),reverse('customer_portal:campaign-create'),
                reverse('customer_portal:invite'),reverse('customer_portal:switch')]:
            self.assertEqual(self.client.post(path,{}).status_code,403)
        self.assertEqual(counts,(AuditLog.objects.count(),WorkforceNeed.objects.count(),Campaign.objects.count()))

    def test_non_administrators_and_real_customers_cannot_enter(self):
        identity=self.enter();self.client.post(reverse('internal:portal-preview-customer-exit'))
        for user,backend in [(self.staff,'django.contrib.auth.backends.ModelBackend'),
                (identity.user,'apps.customers.auth.CustomerAuthenticationBackend')]:
            self.client.force_login(user,backend=backend)
            self.assertEqual(self.client.post(reverse('internal:portal-preview-customer-enter')).status_code,403)
            self.assertEqual(self.client.get(reverse('internal:portal-preview')).status_code,403)

    def test_forged_other_admin_fixture_is_denied(self):
        identity=self.enter()
        self.client.force_login(self.other,backend='django.contrib.auth.backends.ModelBackend')
        session=self.client.session
        session[preview.SESSION_KEY]={'administrator':str(self.other.pk),'identity':str(identity.pk),'started':__import__('time').time()}
        session.save()
        self.assertEqual(self.client.get(reverse('customer_portal:home')).status_code,403)

    def test_expired_session_can_return_without_logging_out(self):
        self.enter();session=self.client.session;selected=session[preview.SESSION_KEY]
        selected['started']-=3601;session[preview.SESSION_KEY]=selected;session.save()
        self.assertEqual(self.client.get(reverse('customer_portal:home')).status_code,403)
        self.assertEqual(self.client.post(reverse('internal:portal-preview-customer-exit')).status_code,302)
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))

    def test_account_changes_and_recovery_are_blocked(self):
        self.enter()
        for path in [reverse('customer_portal:recovery'),reverse('customer_portal:accept',args=['synthetic-invalid-link']),
                reverse('customer_portal:reset-confirm',args=['synthetic-invalid-link'])]:
            self.assertEqual(self.client.get(path).status_code,403)
            self.assertEqual(self.client.post(path,{}).status_code,403)
        self.assertEqual(len(mail.outbox),0)

    def test_csrf_and_post_only_entry_exit(self):
        client=Client(enforce_csrf_checks=True,HTTP_AUTHORIZATION=GATE)
        client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        enter=reverse('internal:portal-preview-customer-enter');exit=reverse('internal:portal-preview-customer-exit')
        client.get(reverse('internal:portal-preview'));csrf=client.cookies['csrftoken'].value
        self.assertEqual(client.get(enter).status_code,405)
        self.assertEqual(client.post(enter).status_code,403)
        self.assertEqual(client.post(enter,{'csrfmiddlewaretoken':csrf}).status_code,302)
        self.assertEqual(client.get(exit).status_code,405)
        self.assertEqual(client.post(exit).status_code,403)
        self.assertEqual(client.post(exit,{'csrfmiddlewaretoken':csrf}).status_code,302)

    @override_settings(BTW_PREVIEW_GATE_ENABLED=False)
    def test_disabled_environment_cannot_seed(self):
        self.assertEqual(self.client.post(reverse('internal:portal-preview-customer-enter')).status_code,403)
        self.assertEqual(CustomerOrganization.objects.count(),0)

    def test_other_render_service_cannot_seed(self):
        with patch.dict('os.environ',{'RENDER_SERVICE_ID':'srv-synthetic-production'}):
            self.assertEqual(self.client.post(reverse('internal:portal-preview-customer-enter')).status_code,403)
        self.assertEqual(CustomerOrganization.objects.count(),0)

    def test_fixture_tampering_fails_closed(self):
        identity=self.enter();org=identity.memberships.get().organization
        from . import services
        campaign=org.campaigns.get()
        services.save_campaign(actor=self.admin,org_id=org.pk,expected_org_revision=org.revision,
            campaign_id=campaign.pk,expected_revision=campaign.revision,data={'name':'Unrelated business record'},why='Synthetic fixture tampering test')
        self.assertEqual(self.client.get(reverse('customer_portal:home')).status_code,403)

    def test_switching_preview_types_retains_admin_and_clears_other_context(self):
        self.enter()
        self.assertEqual(self.client.post(reverse('internal:portal-preview-enter')).status_code,302)
        from apps.participants.portal_preview import SESSION_KEY as PARTICIPANT
        self.assertNotIn(preview.SESSION_KEY,self.client.session)
        self.assertIn(PARTICIPANT,self.client.session)
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))
        self.enter()
        self.assertNotIn(PARTICIPANT,self.client.session)
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))


@skipUnless(connection.vendor=='postgresql','Requires independent PostgreSQL connections')
@override_settings(**OPTIONS)
class CustomerPreviewConcurrencyTests(TransactionTestCase):
    def test_concurrent_first_entry_reuses_one_private_company(self):
        role_configuration(apply=True)
        admin=get_user_model().objects.create_superuser('synthetic-preview-race','preview-race@example.test','synthetic-test-password')
        barrier=Barrier(2)
        def worker():
            close_old_connections()
            try:
                user=get_user_model().objects.get(pk=admin.pk)
                barrier.wait(timeout=20)
                return str(preview.fixture_for(user).identity_id)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures=[executor.submit(worker) for _ in range(2)]
            result=[f.result(timeout=60) for f in futures]
        self.assertEqual(len(set(result)),1)
        self.assertEqual(CustomerOrganization.objects.filter(name=preview.fixture_name(admin)).count(),1)
