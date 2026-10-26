from html.parser import HTMLParser
from unittest.mock import patch
import re

from django.core import mail
from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.participants.models import Participant, ParticipantAccount
from apps.participants.tokens import SET_PASSWORD_SALT, make_token
from .navigation import visible_workspaces


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


@override_settings(AUTHENTICATION_BACKENDS=['django.contrib.auth.backends.ModelBackend'])
class StaffLogout9GTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username='synthetic-logout-9g', is_staff=True)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.staff)

    def test_rendered_logout_is_csrf_protected_post_at_existing_endpoint(self):
        response = self.client.get(reverse('internal:home'))
        elements = Elements(response.content.decode()).elements
        forms = [attrs for tag, attrs in elements if tag == 'form' and attrs.get('action') == reverse('admin:logout')]
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0]['method'], 'post')
        html = response.content.decode()
        sidebar = html.split('<aside class="sidebar"', 1)[1].split('</aside>', 1)[0]
        header = html.split('<header class="topbar">', 1)[1].split('</header>', 1)[0]
        self.assertIn('class="sign-out-form"', sidebar)
        self.assertIn('Personal settings', sidebar)
        self.assertNotIn('class="sign-out-form"', header)
        self.assertNotIn('topbar-brand', header)
        self.assertIn('built-to-work-linear.svg', sidebar)
        self.assertTrue(any(tag == 'input' and attrs.get('name') == 'csrfmiddlewaretoken' for tag, attrs in elements))
        self.assertFalse(any(tag == 'a' and attrs.get('href') == reverse('admin:logout') for tag, attrs in elements))

    def test_get_cannot_end_session(self):
        self.assertEqual(self.client.get(reverse('admin:logout')).status_code, 405)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.staff.pk))

    def test_post_without_csrf_is_rejected_without_ending_session(self):
        self.assertEqual(self.client.post(reverse('admin:logout')).status_code, 403)
        self.assertIn(SESSION_KEY, self.client.session)

    def test_valid_post_ends_only_test_session_and_preserves_logged_out_destination(self):
        self.client.get(reverse('internal:home'))
        token = self.client.cookies['csrftoken'].value
        response = self.client.post(reverse('admin:logout'), {'csrfmiddlewaretoken': token})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/logged_out.html')
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get(reverse('internal:home')).status_code, 302)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ParticipantReset9GTests(TestCase):
    def setUp(self):
        self.person = Participant.objects.create(first_name='Synthetic', last_name='Reset9G')
        self.user = get_user_model().objects.create_user(username='synthetic-reset-9g', password='original-test-password')
        self.account = ParticipantAccount.objects.create(participant=self.person, user=self.user,
            login_email='synthetic-reset@example.test', status=ParticipantAccount.Status.ACTIVE,
            email_verified_at=timezone.now())
        self.url = reverse('participants:password-reset')

    def test_get_renders_labeled_email_and_submit_and_sign_in_navigation(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<label for="id_email">Email:</label>', html=True)
        self.assertContains(response, 'type="email"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'Send reset link')
        self.assertContains(response, reverse('participants:login'))
        self.assertEqual(client.post(self.url, {'email': self.account.login_email}).status_code, 403)

    def test_known_unknown_and_inactive_addresses_have_identical_response(self):
        known = self.client.post(self.url, {'email': ' SYNTHETIC-RESET@EXAMPLE.TEST '})
        self.assertEqual(known.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['synthetic-reset@example.test'])
        unknown = self.client.post(self.url, {'email': 'unknown@example.test'})
        self.assertEqual(unknown.content, known.content)
        self.assertEqual(len(mail.outbox), 1)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        inactive = self.client.post(self.url, {'email': self.account.login_email})
        self.assertEqual(inactive.content, known.content)
        self.assertEqual(len(mail.outbox), 1)

    def test_invalid_input_retains_form_and_sends_no_mail(self):
        for email in ['', 'not-an-email']:
            with self.subTest(email=email):
                response = self.client.post(self.url, {'email': email})
                self.assertFormError(response.context['form'], 'email', 'This field is required.' if not email else 'Enter a valid email address.')
        self.assertEqual(len(mail.outbox), 0)

    def test_valid_csrf_submission_uses_existing_token_recovery(self):
        client = Client(enforce_csrf_checks=True)
        client.get(self.url)
        response = client.post(self.url, {'email': self.account.login_email, 'csrfmiddlewaretoken': client.cookies['csrftoken'].value})
        self.assertEqual(response.status_code, 200)
        path = re.search(r'http://testserver(\S+)', mail.outbox[0].body).group(1)
        self.assertEqual(client.get(path).status_code, 200)
        token = client.cookies['csrftoken'].value
        mismatch = client.post(path, {'password1': 'new-test-password-9g', 'password2': 'different-test-password', 'csrfmiddlewaretoken': token})
        self.assertEqual(mismatch.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('original-test-password'))
        completed = client.post(path, {'password1': 'new-test-password-9g', 'password2': 'new-test-password-9g', 'csrfmiddlewaretoken': token})
        self.assertRedirects(completed, reverse('participants:login'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('new-test-password-9g'))
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, ParticipantAccount.Status.ACTIVE)
        self.assertEqual(self.account.login_email, 'synthetic-reset@example.test')

    def test_invalid_expired_and_wrong_purpose_tokens_remain_rejected(self):
        valid = make_token(self.user.pk, SET_PASSWORD_SALT + '.reset')
        for token in ['invalid-token', make_token(self.user.pk, SET_PASSWORD_SALT)]:
            self.assertEqual(self.client.get(reverse('participants:password-reset-confirm', args=[token])).status_code, 400)
        import time
        with patch('django.core.signing.time.time', return_value=time.time() + 86401):
            self.assertEqual(self.client.get(reverse('participants:password-reset-confirm', args=[valid])).status_code, 400)


@override_settings(AUTHENTICATION_BACKENDS=['django.contrib.auth.backends.ModelBackend'])
class NavigationAndHeading9GTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_superuser(username='synthetic-nav-9g', email='nav@example.test', password='test-password')
        self.client.force_login(self.staff)

    def test_affected_roots_and_nested_routes_have_exactly_one_expected_workspace(self):
        paths = {
            '/quizzes/': 'operations', '/quizzes/categories/': 'operations',
            '/quizzes/question-sets/12/': 'operations', '/quizzes/question-sets/12/edit/': 'operations',
            '/participants/staff/registration-forms/': 'administration',
            '/participants/staff/registration-forms/questions/add/': 'administration',
            '/participants/staff/registration-forms/questions/12/': 'administration',
            '/participants/staff/registration-forms/preview/': 'administration',
            '/participants/staff/registration-forms/publish/': 'administration',
            '/participants/staff/testing/': 'administration',
            '/participants/staff/directory/': 'participants',
            '/participants/staff/directory/12/': 'participants',
        }
        for path, expected in paths.items():
            with self.subTest(path=path):
                nav = visible_workspaces(self.staff, path)
                self.assertEqual([item['id'] for item in nav if item['active']], [expected])

    def test_real_assessment_and_form_builder_pages_expose_aria_current(self):
        for name, expected in [('assessments:dashboard', 'operations'), ('participants:registration-forms', 'administration')]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertEqual([item['id'] for item in response.context['navigation'] if item['active']], [expected])
            url = reverse('internal:workspace', kwargs={'workspace_id': expected})
            self.assertContains(response, f'href="{url}" aria-current="page"')

    def test_corrected_route_context_does_not_grant_workspace_visibility(self):
        limited = get_user_model().objects.create_user(username='synthetic-limited-9g', is_staff=True)
        for path in ['/quizzes/', '/participants/staff/registration-forms/', '/participants/staff/testing/']:
            self.assertEqual([item['id'] for item in visible_workspaces(limited, path)], ['home', 'tasks', 'customers', 'campaigns', 'approvals'])
        self.client.force_login(limited)
        # The existing assessment landing page is staff-accessible without a workspace grant.
        self.assertEqual(self.client.get(reverse('assessments:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('participants:registration-forms')).status_code, 403)
        self.assertEqual(self.client.get(reverse('participants:staff-directory')).status_code, 403)

    def test_directory_has_one_primary_heading_in_empty_and_filtered_states(self):
        for query in ['', 'no-synthetic-match']:
            response = self.client.get(reverse('participants:staff-directory'), {'q': query})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '<h1 class="page-title">Participants</h1>', html=True)
            self.assertEqual(sum(tag == 'h1' for tag, attrs in Elements(response.content.decode()).elements), 1)
            self.assertContains(response, 'name="q"')


# Keep the dependency-free shell regression discoverable by the project runner.
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.test import SimpleTestCase


class ShellFocus9GTests(SimpleTestCase):
    @skipUnless(shutil.which("node"), "Shell focus regression requires the existing Node.js runtime")
    def test_shell_breakpoint_focus_and_keyboard_regressions(self):
        result = subprocess.run(
            [shutil.which("node"), "--test", str(Path(settings.BASE_DIR) / "scripts/test_shell_focus_9g.cjs")],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
