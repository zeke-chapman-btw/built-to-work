"""Synthetic credential-resolution and session-bound selection security contracts."""
import copy
import time
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.test import TestCase, Client, RequestFactory, override_settings
from apps.customers import test_portal_preview as preview_fixtures
from django.urls import reverse
from django.utils import timezone
from apps.customers.test_portal import PortalFixtures, PASSWORD
from apps.customers.models import CustomerIdentity, CustomerMembership
from apps.participants.models import Participant, ParticipantAccount, PortalAccessAttempt
from apps.accounts.models import StaffAccessAttempt
from . import unified_auth as auth

User=get_user_model()

class UnifiedLoginTests(PortalFixtures,TestCase):
    def setUp(self):
        self.org1,self.member,self.customer=self.lead()
        self.email=self.member.identity.email
        self.admin.set_password(PASSWORD);self.admin.save()
        self.person=Participant.objects.create(first_name='SYNTHETIC',last_name='Unified login')
        self.participant=User.objects.create_user('synthetic-unified-participant',password=PASSWORD)
        self.account=ParticipantAccount.objects.create(user=self.participant,participant=self.person,
            login_email='synthetic-unified-participant@example.test',status='active',email_verified_at=timezone.now())

    def attempt(self,identifier,password=PASSWORD,**extra):
        return self.client.post(reverse('accounts:login'),dict(username=identifier,password=password,**extra))

    def reset_budget(self):
        StaffAccessAttempt.objects.all().delete();PortalAccessAttempt.objects.all().delete()

    def collide(self):
        self.admin.email=self.email;self.admin.save(update_fields=['email'])
        self.account.login_email=self.email;self.account.save()

    def begin(self):
        self.collide();response=self.attempt(self.email)
        self.assertRedirects(response,reverse('accounts:identity-choice'),fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id',self.client.session)
        return copy.deepcopy(self.client.session[auth.PENDING])

    def pick(self,pending,index,**kwargs):
        return self.client.post(reverse('accounts:identity-choice'),dict(challenge=pending['nonce'],choice=str(index),**kwargs))

    def test_single_page_has_no_precredential_realm_selector(self):
        for route in ('accounts:login','customer_portal:login','participants:login'):
            response=self.client.get(reverse(route),{'account_type':'customer'})
            self.assertContains(response,'Email or username');self.assertContains(response,'autocomplete="username"')
            self.assertContains(response,'autocomplete="current-password"');self.assertContains(response,'Forgot your password?')
            self.assertNotContains(response,'name="account_type"');self.assertNotContains(response,'Choose the account type')
            self.assertIn('private',response['Cache-Control']);self.assertIn('no-store',response['Cache-Control'])

    def test_staff_username_and_email_authenticate_same_existing_identity(self):
        for identifier in (self.admin.username,self.admin.email.upper()):
            self.client=Client();response=self.attempt(identifier)
            self.assertEqual(response.url,reverse('internal:home'))
            self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))
            self.assertEqual(self.client.session['_auth_user_backend'],auth.BACKENDS['staff'])

    def test_customer_email_uses_customer_backend_and_company(self):
        response=self.attempt(self.email.upper(),account_type='staff')
        self.assertEqual(response.url,reverse('customer_portal:home'))
        self.assertEqual(self.client.session['_auth_user_backend'],auth.BACKENDS['customer'])
        self.assertEqual(self.client.get(response.url).status_code,200)
        self.assertEqual(self.client.get('/internal/').status_code,403)

    def test_participant_email_uses_own_portal_and_backend(self):
        response=self.attempt(self.account.login_email,account_type='customer')
        self.assertEqual(response.url,reverse('participants:portal-home'))
        self.assertEqual(self.client.session['_auth_user_backend'],auth.BACKENDS['participant'])
        self.assertEqual(self.client.get(response.url).status_code,200)
        self.assertEqual(self.client.get('/customer/').status_code,403)

    def test_internal_portal_usernames_do_not_become_public_identifiers(self):
        for user in (self.customer,self.participant):
            self.assertEqual(self.attempt(user.username).status_code,200)
            self.assertNotIn('_auth_user_id',self.client.session)

    def test_unknown_wrong_inactive_and_unactivated_have_identical_errors(self):
        errors=[]
        for identifier,password in [(self.admin.username,'wrong'),('missing@example.test','wrong'),(self.email,'wrong'),(self.account.login_email,'wrong')]:
            response=self.attempt(identifier,password);errors.append(str(response.context['form'].non_field_errors()))
        pending_member=self.membership(self.org1,role='member')
        self.admin.is_active=False;self.admin.save()
        errors.append(str(self.attempt(self.admin.username).context['form'].non_field_errors()))
        errors.append(str(self.attempt(pending_member.identity.email).context['form'].non_field_errors()))
        self.assertEqual(len(set(errors)),1);self.assertNotIn('_auth_user_id',self.client.session)

    def test_customer_requires_accepted_enabled_active_provisioned_membership(self):
        member=self.membership(self.org1,role='member');user=self.activate(member)
        email=member.identity.email
        member.state='suspended';member.save()
        self.assertEqual(self.attempt(email).status_code,200);self.assertNotIn('_auth_user_id',self.client.session)
        member.state='enabled';member.save()
        self.org1.status='inactive';self.org1.save()
        self.assertEqual(self.attempt(email).status_code,200);self.assertNotIn('_auth_user_id',self.client.session)
        # An existing activated identity may have a prepared but unaccepted membership.
        unprovisioned=self.org(name='SYNTHETIC not provisioned')
        self.assertFalse(unprovisioned.provisioned)
        provisioned=self.portal_org(name='SYNTHETIC prepared membership');lead=self.membership(provisioned);self.activate(lead)
        pending=self.membership(provisioned,role='member',identity=member.identity,designate=False)
        self.assertIsNone(pending.accepted_at)
        self.assertEqual(self.attempt(email).status_code,200);self.assertNotIn('_auth_user_id',self.client.session)

    def test_participant_disabled_unverified_or_archived_is_rejected(self):
        for field,value in [('status','disabled'),('email_verified_at',None)]:
            original=getattr(self.account,field);setattr(self.account,field,value);self.account.save()
            self.assertEqual(self.attempt(self.account.login_email).status_code,200);self.assertNotIn('_auth_user_id',self.client.session)
            setattr(self.account,field,original);self.account.save()
        self.person.archived_at=timezone.now();self.person.save()
        self.assertEqual(self.attempt(self.account.login_email).status_code,200);self.assertNotIn('_auth_user_id',self.client.session)

    def test_same_identifier_different_passwords_routes_only_authenticated_identity(self):
        self.collide();self.admin.set_password('Synthetic-distinct-staff-password!51');self.admin.save()
        self.participant.set_password('Synthetic-distinct-participant-password!51');self.participant.save()
        response=self.attempt(self.email)
        self.assertEqual(response.url,reverse('customer_portal:home'))
        self.assertEqual(self.client.session['_auth_user_id'],str(self.customer.pk))

    def test_shared_valid_credentials_require_protected_choice_no_merge(self):
        pending=self.begin();self.assertEqual(len(pending['identities']),3)
        response=self.client.get(reverse('accounts:identity-choice'))
        self.assertContains(response,'Verified accounts');self.assertNotContains(response,str(self.customer.pk))
        index=next(n for n,x in enumerate(pending['identities']) if x['realm']=='participant')
        self.assertEqual(self.pick(pending,index).url,reverse('participants:portal-home'))
        self.assertEqual(self.client.session['_auth_user_id'],str(self.participant.pk))
        self.assertEqual(len({self.customer.pk,self.admin.pk,self.participant.pk}),3)
        self.assertNotIn(auth.PENDING,self.client.session)

    def test_duplicate_staff_email_verifies_all_passwords_not_first_match(self):
        self.admin.email='synthetic-collision@example.test';self.admin.save()
        other=User.objects.create_user('synthetic-other-staff',email=self.admin.email,is_staff=True,password='Synthetic-Other-Password!61')
        self.assertEqual(self.attempt(self.admin.email,'Synthetic-Other-Password!61').url,reverse('internal:home'))
        self.assertEqual(self.client.session['_auth_user_id'],str(other.pk))

    def test_choice_tampering_and_cross_session_attempt_fail_closed(self):
        pending=self.begin()
        other=Client();self.assertEqual(other.post(reverse('accounts:identity-choice'),{'challenge':pending['nonce'],'choice':'0'}).url,reverse('accounts:login'))
        response=self.pick(pending,-1)
        self.assertEqual(response.url,reverse('accounts:login'));self.assertNotIn('_auth_user_id',self.client.session)
        self.assertNotIn(auth.PENDING,self.client.session)

    def test_choice_nonce_tampering_fails_closed(self):
        pending=self.begin();pending['nonce']='wrong'
        self.pick(pending,0);self.assertNotIn('_auth_user_id',self.client.session)

    def test_choice_expiry_and_security_revision_rechecked(self):
        pending=self.begin();session=self.client.session;session[auth.PENDING]={**pending,'created':time.time()-301};session.save()
        self.pick(pending,0);self.assertNotIn('_auth_user_id',self.client.session)
        self.reset_budget();pending=self.begin();index=next(n for n,x in enumerate(pending['identities']) if x['realm']=='staff')
        self.admin.security_revision+=1;self.admin.save()
        self.pick(pending,index);self.assertNotIn('_auth_user_id',self.client.session)

    def test_choice_rechecks_membership_and_participant_state(self):
        pending=self.begin();index=next(n for n,x in enumerate(pending['identities']) if x['realm']=='customer')
        replacement=self.membership(self.org1,role='lead');self.activate(replacement)
        from apps.customers import services
        self.org1.refresh_from_db()
        services.transfer_primary(actor=self.admin,org_id=self.org1.pk,expected_org_revision=self.org1.revision,target_id=replacement.pk,why='Synthetic fixture transition')
        self.member.refresh_from_db();self.member.state='suspended';self.member.save()
        self.pick(pending,index);self.assertNotIn('_auth_user_id',self.client.session)

    def test_old_choice_snapshot_cannot_be_replayed(self):
        pending=self.begin();response=self.pick(pending,0);self.assertIn('_auth_user_id',self.client.session)
        self.client.logout();session=self.client.session;session[auth.PENDING]=pending;session.save()
        response=self.pick(pending,0);self.assertEqual(response.url,reverse('accounts:login'));self.assertNotIn('_auth_user_id',self.client.session)

    def test_credential_proof_does_not_survive_password_change(self):
        pending=self.begin();index=next(n for n,x in enumerate(pending['identities']) if x['realm']=='participant')
        self.participant.set_password('Synthetic-Changed-Password!72');self.participant.save()
        self.pick(pending,index);self.assertNotIn('_auth_user_id',self.client.session)

    def test_login_rotates_anonymous_session_and_does_not_store_password(self):
        session=self.client.session;session['synthetic_probe']=True;session.save();old=session.session_key
        self.attempt(self.admin.username)
        self.assertNotEqual(self.client.session.session_key,old)
        self.assertNotIn(PASSWORD,str(dict(self.client.session)))

    def test_cross_realm_return_urls_and_unsafe_paths_are_rejected(self):
        for value in ('https://evil.example/','//evil.example/','/customer/','/participants/profile/','/admin/','/customer/../internal/'):
            self.client=Client();self.reset_budget()
            self.assertEqual(self.attempt(self.admin.username,next=value).url,reverse('internal:home'))
        self.client=Client();self.reset_budget()
        self.assertEqual(self.attempt(self.admin.username,next='/operations/tasks/?view=mine').url,'/operations/tasks/?view=mine')

    def test_customer_cross_company_record_return_is_rejected(self):
        other=self.portal_org(name='SYNTHETIC other company')
        from apps.customers import services
        need=services.save_hiring_draft(actor=self.admin,org_id=other.pk,expected_org_revision=other.revision,data={'title':'SYNTHETIC private role'},why='Synthetic test')
        response=self.attempt(self.email,next=reverse('customer_portal:need',args=[need.pk]))
        self.assertEqual(response.url,reverse('customer_portal:home'))
        self.assertEqual(self.client.get(reverse('customer_portal:need',args=[need.pk])).status_code,404)

    def test_customer_multi_company_membership_is_not_merged_or_expanded(self):
        second=self.portal_org(name='SYNTHETIC second company')
        member2=self.membership(second,identity=self.member.identity,designate=True)
        _,raw=self.issue(member2)
        from apps.customers import invitations
        invitations.accept_invitation(raw=raw,actor=self.customer)
        self.attempt(self.email);response=self.client.get(reverse('customer_portal:home'))
        self.assertEqual(len(response.context['companies']),2)

    def test_throttling_budget_is_shared_across_entry_points_and_realm_hints(self):
        for n in range(11):
            route=['accounts:login','customer_portal:login','participants:login'][n%3]
            self.client.post(reverse(route),{'username':self.email,'password':'wrong','account_type':['staff','participant','customer'][n%3]})
        self.assertEqual(self.attempt(self.email).status_code,200);self.assertNotIn('_auth_user_id',self.client.session)

    def test_csrf_required_for_login_and_identity_selection(self):
        strict=Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse('accounts:login'),{'username':self.admin.username,'password':PASSWORD}).status_code,403)
        pending=self.begin();strict.cookies=self.client.cookies
        self.assertEqual(strict.post(reverse('accounts:identity-choice'),{'challenge':pending['nonce'],'choice':'0'}).status_code,403)

    def test_post_logout_and_customer_session_invalidation_preserved(self):
        self.attempt(self.email);self.assertEqual(self.client.get(reverse('accounts:logout')).status_code,405)
        self.customer.security_revision+=1;self.customer.save()
        self.assertEqual(self.client.get(reverse('customer_portal:home')).status_code,302)
        self.reset_budget();self.attempt(self.account.login_email)
        self.client.post(reverse('accounts:logout'));self.assertNotIn('_auth_user_id',self.client.session)

    def test_account_type_hint_never_authenticates_an_unrelated_identity(self):
        response=self.attempt(self.account.login_email,account_type='staff',next='/internal/')
        self.assertEqual(response.url,reverse('participants:portal-home'))
        self.assertEqual(self.client.session['_auth_user_id'],str(self.participant.pk))

    @override_settings(**preview_fixtures.OPTIONS)
    def test_preview_entry_and_return_do_not_become_real_authentication(self):
        self.client.defaults['HTTP_AUTHORIZATION']=preview_fixtures.GATE
        self.client.force_login(self.admin,backend=auth.BACKENDS['staff'])
        response=self.client.post(reverse('internal:portal-preview-customer-enter'))
        self.assertEqual(response.status_code,302)
        response=self.client.post(reverse('accounts:login'),{'username':self.email,'password':PASSWORD})
        self.assertEqual(response.url,reverse('customer_portal:home'))
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))

    def test_customer_unauthenticated_deep_link_keeps_its_safe_return(self):
        from urllib.parse import urlsplit,parse_qs
        path=reverse('customer_portal:workspace',args=['needs'])
        response=self.client.get(path)
        self.assertEqual(urlsplit(response.url).path,reverse('accounts:login'))
        self.assertEqual(parse_qs(urlsplit(response.url).query)['next'],[path])

    def test_central_customer_logout_retains_existing_audit(self):
        from apps.core.models import AuditLog
        self.attempt(self.email)
        self.client.post(reverse('accounts:logout'))
        self.assertNotIn('_auth_user_id',self.client.session)
        self.assertTrue(AuditLog.objects.filter(action='customer.signed_out',actor=self.customer).exists())

    @override_settings(**preview_fixtures.OPTIONS)
    def test_central_logout_in_preview_returns_to_administrator(self):
        self.client.defaults['HTTP_AUTHORIZATION']=preview_fixtures.GATE
        self.client.force_login(self.admin,backend=auth.BACKENDS['staff'])
        self.client.post(reverse('internal:portal-preview-customer-enter'))
        response=self.client.post(reverse('accounts:logout'))
        self.assertEqual(response.url,reverse('internal:portal-preview'))
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))
