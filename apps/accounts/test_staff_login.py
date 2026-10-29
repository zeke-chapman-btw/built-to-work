from django.test import TestCase,Client
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.urls import reverse
from .roles import role_configuration,ROLE_GROUPS
from .staff_services import set_active,revision
from .login_views import home
U=get_user_model()
class StaffLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        role_configuration(apply=True)
        cls.admin=U.objects.create_superuser('synthetic-login-admin','admin@example.test','Synthetic-test-password-123')
        cls.worker=U.objects.create_user('synthetic-login-worker',email='worker@example.test',password='Synthetic-test-password-123',is_staff=True)
        cls.worker.groups.add(Group.objects.get(name=ROLE_GROUPS['event_staff']))
        cls.participant=U.objects.create_user('synthetic-login-participant',email='participant@example.test',password='Synthetic-test-password-123')
    def login(self,user=None,next=''):
        return self.client.post(reverse('accounts:login'),{'username':(user or self.worker).username,'password':'Synthetic-test-password-123','next':next})
    def test_branded_login_and_missing_route_fixed(self):
        r=self.client.get('/accounts/login/');self.assertEqual(r.status_code,200);self.assertContains(r,'built-to-work-linear.svg');self.assertContains(r,'Staff sign in')
    def test_default_internal_redirect_resolves(self):
        r=self.client.get('/internal/',follow=True);self.assertEqual(r.status_code,200)
    def test_worker_landing(self):
        self.assertRedirects(self.login(),reverse('staffing:general-schedule'),fetch_redirect_response=False)
    def test_administrator_compatible(self):
        self.assertRedirects(self.login(self.admin),reverse('internal:home'),fetch_redirect_response=False)
        self.assertEqual(self.client.get('/admin/').status_code,200)
    def test_participant_cannot_use_staff_login(self):
        self.assertEqual(self.login(self.participant).status_code,200);self.assertNotIn('_auth_user_id',self.client.session)
    def test_inactive_cannot_sign_in(self):
        self.worker.is_active=False;self.worker.save();self.assertEqual(self.login().status_code,200);self.assertNotIn('_auth_user_id',self.client.session)
    def test_external_and_portal_redirects_are_rejected(self):
        for target in ('https://evil.example/','//evil.example/','/participants/portal/','javascript:alert(1)'):
            self.client=Client();self.assertEqual(self.login(next=target).url,reverse('staffing:general-schedule'))
    def test_safe_same_origin_next_preserved(self):
        self.assertEqual(self.login(next='/operations/tasks/?view=mine').url,'/operations/tasks/?view=mine')
    def test_logout_requires_csrf_post(self):
        self.login();self.assertEqual(self.client.get(reverse('accounts:logout')).status_code,405)
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.worker,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(strict.post(reverse('accounts:logout')).status_code,403)
        self.assertEqual(self.client.post(reverse('accounts:logout')).status_code,302);self.assertNotIn('_auth_user_id',self.client.session)
    def test_login_post_requires_csrf(self):
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse('accounts:login'),{}).status_code,403)
    def test_recovery_is_generic_and_staff_only(self):
        known=self.client.post(reverse('accounts:recovery'),{'username':self.worker.username,'email':self.worker.email})
        self.assertEqual(len(mail.outbox),1)
        unknown=self.client.post(reverse('accounts:recovery'),{'username':'unknown','email':'unknown@example.test'})
        self.assertEqual(known.content,unknown.content)
        self.client.post(reverse('accounts:recovery'),{'username':self.participant.username,'email':self.participant.email})
        self.assertEqual(len(mail.outbox),1)
    def test_recovery_token_is_single_use(self):
        self.client.post(reverse('accounts:recovery'),{'username':self.worker.username,'email':self.worker.email})
        url=mail.outbox[0].body.split('https://')[-1] if 'https://' in mail.outbox[0].body else mail.outbox[0].body.split('http://')[-1]
        path='/'+url.split('/',1)[1].strip()
        r=self.client.post(path,{'new_password1':'New-Synthetic-Test-Password-123','new_password2':'New-Synthetic-Test-Password-123'})
        self.assertEqual(r.status_code,302);self.assertContains(self.client.get(path),'invalid, expired or already used')
    def test_password_change_keeps_current_session_revokes_other(self):
        self.login();other=Client();other.force_login(self.worker,backend='django.contrib.auth.backends.ModelBackend')
        r=self.client.post(reverse('accounts:password-change'),{'old_password':'Synthetic-test-password-123','new_password1':'Changed-Synthetic-Password-456','new_password2':'Changed-Synthetic-Password-456'})
        self.assertEqual(r.status_code,302);self.assertIn('_auth_user_id',self.client.session)
        self.assertEqual(other.get('/internal/').status_code,302)
    def test_login_throttle(self):
        for i in range(11):self.client.post(reverse('accounts:login'),{'username':self.worker.username,'password':'WRONG-SYNTHETIC'})
        self.client.post(reverse('accounts:logout'))
        r=self.login();self.assertEqual(r.status_code,200);self.assertNotIn('_auth_user_id',self.client.session)
    def test_native_admin_login_is_preserved(self):
        self.assertEqual(self.client.get('/admin/login/').status_code,200)
    def test_invalid_activation_is_branded(self):
        r=self.client.get(reverse('accounts:activate',args=['invalid']));self.assertContains(r,'built-to-work-linear.svg');self.assertContains(r,'invalid, expired or already used')
