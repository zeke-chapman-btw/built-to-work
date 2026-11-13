"""Shared chrome contracts on real guarded views, including ordinary portal logins."""
from html.parser import HTMLParser
from datetime import date
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.customers.test_portal import PortalFixtures, PASSWORD
from apps.customers import invitations, services
from apps.customers.test_domain import WHY
from apps.customers.portal_views import TABS
from apps.participants.models import Participant, ParticipantAccount, ParticipantCareerProfile
from apps.operations.templatetags.application_shell import PARTICIPANT_LINKS


class ShellLinks(HTMLParser):
    def __init__(self, text):
        super().__init__();self.links=[];self.current=None;self.feed(text)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='a' and 'nav-item' in attrs.get('class','').split():
            self.current={'url':attrs.get('href'),'active':attrs.get('aria-current')=='page','text':''}
    def handle_data(self,text):
        if self.current is not None:self.current['text']+=text
    def handle_endtag(self,tag):
        if tag=='a' and self.current is not None:
            self.current['text']=' '.join(self.current['text'].split());self.links.append(self.current);self.current=None


def chrome(test,response):
    test.assertEqual(response.status_code,200)
    test.assertTemplateUsed(response,'shared/application_base.html')
    test.assertContains(response,'id="internal-sidebar"',count=1)
    test.assertContains(response,'class="topbar"',count=1)
    test.assertContains(response,'id="main-content"',count=1)
    test.assertContains(response,'data-menu-toggle',count=1)
    test.assertContains(response,'data-scrim',count=1)
    test.assertContains(response,'internal/js/shell.js',count=1)
    return ShellLinks(response.content.decode()).links


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CustomerSharedShellTests(PortalFixtures,TestCase):
    def test_seven_destinations_active_states_and_forms_use_shared_frame(self):
        org,member,user=self.lead()
        # The existing identity relation is authoritative, not a Staff preview.
        result=self.client.post(reverse('accounts:login'),{'account_type':'customer','email':member.identity.email,'password':PASSWORD})
        self.assertEqual(result.status_code,302)
        for key,label in TABS:
            with self.subTest(section=key):
                response=self.client.get(reverse('customer_portal:workspace',args=[key]));links=chrome(self,response)
                self.assertEqual([x['url'] for x in links],[reverse('customer_portal:workspace',args=[k]) for k,_ in TABS])
                self.assertEqual([x['url'] for x in links if x['active']],[reverse('customer_portal:workspace',args=[key])])
                self.assertNotContains(response,'class="portal-tabs"');self.assertNotContains(response,'data-section-chooser')
                self.assertNotContains(response,'data-task-create');self.assertNotContains(response,'notification-bell')
        response=self.client.get(reverse('customer_portal:need-create'));chrome(self,response)
        self.assertContains(response,'data-structured-form');self.assertEqual(len(response.context['form'].groups),7)
        self.assertContains(response,'Number of workers needed');self.assertContains(response,'Save draft')
        self.assertEqual([x['url'] for x in ShellLinks(response.content.decode()).links if x['active']],
                         [reverse('customer_portal:workspace',args=['recruiting'])])

    def test_member_flags_do_not_become_staff_or_lead_navigation(self):
        org,lead,lead_user=self.lead();self.plan(org,member_limit=4)
        for needs,campaigns in [(False,False),(True,False),(False,True),(True,True)]:
            with self.subTest(needs=needs,campaigns=campaigns):
                member=self.membership(org,role='member');user=self.activate(member);org.refresh_from_db();member.refresh_from_db()
                invitations.manage_member(actor=lead_user,org_id=org.pk,expected_org_revision=org.revision,
                    membership_id=member.pk,expected_revision=member.revision,state='enabled',manage_needs=needs,manage_campaigns=campaigns)
                self.client.logout();self.assertEqual(self.client.post(reverse('accounts:login'),
                    {'account_type':'customer','email':member.identity.email,'password':PASSWORD}).status_code,302)
                response=self.client.get(reverse('customer_portal:home'));links=chrome(self,response)
                self.assertEqual(len(links),4)
                self.assertFalse(any(x['url'].startswith(('/internal/','/operations/','/participants/')) for x in links))
                self.assertEqual(self.client.get(reverse('customer_portal:need-create')).status_code,200 if needs else 403)
                self.assertEqual(self.client.get(reverse('customer_portal:campaign-create')).status_code,200 if campaigns else 403)
                self.assertEqual(self.client.get(reverse('customer_portal:invite')).status_code,403)
                self.assertEqual(self.client.get('/internal/').status_code,403)
                self.assertEqual(self.client.get(reverse('participants:profile')).status_code,403)

    def test_organization_switch_remains_guarded_and_context_is_not_a_role_switcher(self):
        a,lead,user=self.lead();b=self.portal_org(name='SYNTHETIC second shell company')
        second=self.membership(b,identity=lead.identity);_,raw=self.issue(second);invitations.accept_invitation(raw=raw,actor=user)
        self.customer_login(user);session=self.client.session;session['customer_organization']=str(a.pk);session.save()
        response=self.client.get(reverse('customer_portal:home'));chrome(self,response)
        self.assertContains(response,'application-company-switch');self.assertContains(response,'name="organization"')
        self.assertContains(response,reverse('customer_portal:switch'))
        self.assertEqual(self.client.post(reverse('customer_portal:switch'),{'organization':str(b.pk)}).status_code,302)
        response=self.client.get(reverse('customer_portal:home'));self.assertEqual(response.context['company']['id'],b.pk)
        outsider=self.portal_org(name='SYNTHETIC unauthorized shell company')
        self.assertEqual(self.client.post(reverse('customer_portal:switch'),{'organization':str(outsider.pk)}).status_code,403)
        self.assertEqual(self.client.session['_auth_user_backend'],'apps.customers.auth.CustomerAuthenticationBackend')

    def test_logout_is_post_csrf_protected_and_preserves_the_existing_endpoint(self):
        org,member,user=self.lead();browser=Client(enforce_csrf_checks=True)
        browser.force_login(user,backend='apps.customers.auth.CustomerAuthenticationBackend')
        response=browser.get(reverse('customer_portal:home'));chrome(self,response)
        self.assertContains(response,'action="'+reverse('customer_portal:logout')+'"')
        self.assertEqual(browser.post(reverse('customer_portal:logout')).status_code,403)
        token=browser.cookies['csrftoken'].value
        self.assertEqual(browser.post(reverse('customer_portal:logout'),{'csrfmiddlewaretoken':token}).status_code,302)
        self.assertNotIn('_auth_user_id',browser.session)

    def test_no_company_access_does_not_fall_back_to_staff_menus(self):
        org,member,user=self.lead();self.customer_login(user);org.refresh_from_db()
        services.save_organization(actor=self.admin,org_id=org.pk,expected_revision=org.revision,data={'status':'inactive'},why=WHY)
        response=self.client.get(reverse('customer_portal:home'));self.assertEqual(response.status_code,403)
        self.assertEqual(ShellLinks(response.content.decode()).links,[])
        self.assertNotContains(response,'Internal workspace',status_code=403)


class ParticipantSharedShellTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('synthetic-shell-participant',password=PASSWORD)
        self.person=Participant.objects.create(first_name='SYNTHETIC',last_name='Shell Participant',date_of_birth=date(2000,1,1),contact_email='shell-contact@example.test')
        ParticipantCareerProfile.objects.create(participant=self.person,career_interests='Synthetic skilled trades')
        self.account=ParticipantAccount.objects.create(user=self.user,participant=self.person,
            login_email='shell-login@example.test',status='active',email_verified_at=timezone.now())
        response=self.client.post(reverse('accounts:login'),{'account_type':'participant','email':self.account.login_email,'password':PASSWORD})
        self.assertEqual(response.status_code,302)

    def test_five_destinations_share_chrome_and_keep_profile_anchors_and_activity_title(self):
        for key,label,icon,route in PARTICIPANT_LINKS:
            with self.subTest(section=key):
                response=self.client.get(reverse(route));links=chrome(self,response)
                self.assertEqual([x['url'] for x in links],[reverse(row[3]) for row in PARTICIPANT_LINKS])
                self.assertEqual([x['url'] for x in links if x['active']],[reverse(route)])
                self.assertNotContains(response,'class="portal-header"');self.assertNotContains(response,'class="portal-nav"')
                self.assertNotContains(response,'data-task-create');self.assertNotContains(response,'notification-bell')
        profile=self.client.get(reverse('participants:profile'))
        self.assertContains(profile,'My Profile sections');self.assertContains(profile,'#documents')
        self.assertContains(profile,'profile-name');self.assertContains(profile,'SYNTHETIC Shell Participant')
        activity=self.client.get(reverse('participants:portal-activity'))
        self.assertContains(activity,'<title>My activity · Built to Work</title>',html=True)
        self.assertContains(activity,'Recorded participation',count=1)

    def test_independent_editor_retains_values_and_active_profile_navigation(self):
        redirected=self.client.get(reverse('participants:portal-profile-edit',args=['availability']))
        self.assertRedirects(redirected, reverse('recruiting:portal-preferences'), fetch_redirect_response=False)
        response=self.client.get(reverse('recruiting:portal-preferences'));links=chrome(self,response)
        self.assertEqual([x['url'] for x in links if x['active']],[reverse('participants:profile')])
        self.assertContains(response,'Current availability');self.assertContains(response,'Save preferences')

    def test_no_staff_customer_links_and_cross_role_denial(self):
        response=self.client.get(reverse('participants:profile'));links=chrome(self,response)
        self.assertFalse(any(x['url'].startswith(('/internal/','/customer/','/operations/')) for x in links))
        self.assertEqual(self.client.get('/internal/').status_code,403)
        self.assertEqual(self.client.get(reverse('customer_portal:home')).status_code,403)

    def test_sidebar_signout_uses_existing_post_csrf_flow(self):
        browser=Client(enforce_csrf_checks=True);browser.force_login(self.user,backend='apps.participants.auth.ParticipantAuthenticationBackend')
        response=browser.get(reverse('participants:profile'));chrome(self,response)
        self.assertContains(response,'action="'+reverse('participants:logout')+'"')
        self.assertEqual(browser.post(reverse('participants:logout')).status_code,403)
        token=browser.cookies['csrftoken'].value
        self.assertEqual(browser.post(reverse('participants:logout'),{'csrfmiddlewaretoken':token}).status_code,302)
        self.assertNotIn('_auth_user_id',browser.session)


class StaffSharedShellTests(PortalFixtures,TestCase):
    def test_staff_management_templates_inside_portal_directories_keep_staff_chrome(self):
        org,member,user=self.lead()
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        for path in [reverse('customer_portal:staff-members',args=[org.pk]),
                     reverse('customer_portal:staff-invitation',args=[org.pk,member.pk,'resend']),
                     reverse('participants:portal-invitations')]:
            with self.subTest(path=path):
                response=self.client.get(path);links=chrome(self,response)
                self.assertEqual([x['url'] for x in links],[x['url'] for x in response.context['primary_navigation']])
                self.assertNotContains(response,'has-portal-shell')
                self.assertContains(response,'data-notification-bell')

    def test_existing_staff_navigation_and_task_dialog_remain_staff_only(self):
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get('/internal/');links=chrome(self,response)
        self.assertEqual([x['url'] for x in links],[x['url'] for x in response.context['primary_navigation']])
        self.assertContains(response,'data-notification-bell')
        self.assertContains(response,'id="task-create-dialog"',count=1)
        self.assertNotContains(response,'has-portal-shell');self.assertNotContains(response,'application-context')


from apps.customers.test_portal_preview import OPTIONS,GATE
@override_settings(**OPTIONS)
class PreviewSharedShellTests(PortalFixtures,TestCase):
    def test_preview_navigation_is_portal_scoped_and_return_keeps_staff_session(self):
        self.client.defaults['HTTP_AUTHORIZATION']=GATE
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.post(reverse('internal:portal-preview-customer-enter')).status_code,302)
        response=self.client.get(reverse('customer_portal:home'));links=chrome(self,response)
        self.assertEqual(len(links),4);self.assertTrue(all(x['url'].startswith('/customer/') for x in links))
        self.assertContains(response,'View-only customer preview');self.assertContains(response,'Return to Admin',count=1)
        self.assertNotContains(response,'data-task-create');self.assertContains(response,'application-preview-notice')
        self.assertEqual(self.client.post(reverse('customer_portal:logout')).status_code,302)
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))
        self.assertEqual(self.client.post(reverse('internal:portal-preview-enter')).status_code,302)
        response=self.client.get(reverse('participants:profile'));links=chrome(self,response)
        self.assertEqual(len(links),6);self.assertTrue(all(x['url'].startswith('/participants/') for x in links))
        self.assertContains(response,'Admin Preview — Participant Portal');self.assertContains(response,'Return to Admin',count=1)
        self.assertNotContains(response,'data-task-create');self.assertNotContains(response,'class="sign-out-form"')
        self.assertEqual(self.client.post(reverse('internal:portal-preview-exit')).status_code,302)
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))
