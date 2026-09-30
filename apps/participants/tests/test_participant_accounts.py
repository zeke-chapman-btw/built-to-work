from django.utils import timezone
import re
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.core.models import AuditLog
from apps.participants.models import Participant, ParticipantAccount, ParticipantAccountRequest
from apps.participants.normalization import normalize_email, normalize_phone
from apps.participants.services import find_participant_matches
User = get_user_model()

@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ParticipantIdentityTests(TestCase):
    def participant(self, **kwargs):
        data = {"first_name":"Rae", "last_name":"Example", "contact_email":"rae@example.com", "contact_phone":"5551234567"}
        data.update(kwargs)
        return Participant.objects.create(**data)

    def test_normalization_and_safe_candidate_matching(self):
        self.assertEqual(normalize_email(" Rae@Example.COM "), "rae@example.com")
        self.assertEqual(normalize_phone("(555) 123-4567"), "5551234567")
        person = self.participant()
        self.assertEqual(list(find_participant_matches("RAE@example.com", "")), [person])

    def test_ambiguous_request_requires_staff_review(self):
        self.participant()
        self.participant(first_name="Ray", contact_email="other@example.com")
        self.client.post(reverse("participants:account-request"), {"first_name":"Rae", "last_name":"Example", "contact_email":"new@example.com", "contact_phone":"5551234567", "login_email":"new@example.com"})
        obj = ParticipantAccountRequest.objects.get()
        token = re.search(r"/verify/([^/]+)", mail.outbox[0].body).group(1)
        self.client.get(reverse("participants:verify-request", kwargs={"token":token}))
        obj.refresh_from_db()
        self.assertEqual(obj.status, obj.Status.PENDING_REVIEW)
        self.assertEqual(obj.match_count, 2)
        self.assertEqual(ParticipantAccount.objects.count(), 0)

    def test_verified_unique_contact_email_creates_pending_account_and_password_activates(self):
        person = self.participant()
        self.client.post(reverse("participants:account-request"), {"first_name":"Rae", "last_name":"Example", "contact_email":"RAE@example.com", "contact_phone":"5551234567", "login_email":"rae@example.com"})
        obj = ParticipantAccountRequest.objects.get()
        token = re.search(r"/verify/([^/]+)", mail.outbox[0].body).group(1)
        self.client.get(reverse("participants:verify-request", kwargs={"token":token}))
        account = ParticipantAccount.objects.get(participant=person)
        self.assertEqual(account.status, account.Status.PENDING)
        password_token = re.search(r"/set-password/([^/]+)", mail.outbox[-1].body).group(1)
        response = self.client.post(reverse("participants:set-password", kwargs={"token":password_token}), {"password1":"Long-random-passphrase-284!", "password2":"Long-random-passphrase-284!"})
        self.assertEqual(response.url.split("?")[0], reverse("participants:login"))
        account.refresh_from_db()
        self.assertEqual(account.status, account.Status.ACTIVE)
        self.assertTrue(account.user.check_password("Long-random-passphrase-284!"))

    def test_unverified_active_account_cannot_access_profile(self):
        person = self.participant()
        user = User.objects.create_user(
            username="unverified-participant",
            email="unverified@example.com",
            password="ParticipantPass123!",
        )
        user.is_active = True
        user.save(update_fields=["is_active"])
        ParticipantAccount.objects.create(
            participant=person,
            user=user,
            login_email="unverified@example.com",
            status=ParticipantAccount.Status.ACTIVE,
        )
        self.client.force_login(user, backend="apps.participants.auth.ParticipantAuthenticationBackend")

        response = self.client.get(reverse("participants:profile"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url.split("?")[0], reverse("participants:login"))

    def test_login_and_profile_are_scoped_to_account(self):
        person = self.participant()
        user = User.objects.create_user(username="participant_test", email="rae@example.com", password="Long-random-passphrase-284!", is_active=True)
        account = ParticipantAccount.objects.create(participant=person, user=user, login_email="rae@example.com", status=ParticipantAccount.Status.ACTIVE, email_verified_at=timezone.now())
        self.assertTrue(user.is_active)
        self.assertEqual(account.status, ParticipantAccount.Status.ACTIVE)
        self.assertIsNotNone(account.email_verified_at)

        response = self.client.post(reverse("participants:login"), {"email":"RAE@example.com", "password":"Long-random-passphrase-284!"})
        self.assertRedirects(response, reverse("participants:profile"))
        profile_response = self.client.get(reverse("participants:profile"))
        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(ParticipantAccount.objects.get(user=profile_response.wsgi_request.user).participant_id, person.pk)
        self.assertContains(profile_response, "rae@example.com")

        response = self.client.post(reverse("participants:profile"), {"first_name":"Updated", "last_name":"Example", "preferred_name":"", "contact_email":"rae@example.com", "contact_phone":"5551234567"})
        self.assertRedirects(response, reverse("participants:profile"))
        person.refresh_from_db()
        self.assertEqual(person.first_name, "Updated")
        self.assertTrue(AuditLog.objects.filter(action="participant_profile_updated").exists())

    def test_staff_user_alone_cannot_use_participant_login(self):
        User.objects.create_user(username="internal", email="rae@example.com", password="Long-random-passphrase-284!", is_active=True)
        response = self.client.post(reverse("participants:login"), {"email":"rae@example.com", "password":"Long-random-passphrase-284!"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_must_choose_participant_when_resolving_verified_request(self):
        person = self.participant()
        obj = ParticipantAccountRequest.objects.create(first_name="Rae", last_name="Example", contact_email="rae@example.com", contact_phone="5551234567", login_email="new-login@example.com", status=ParticipantAccountRequest.Status.PENDING_REVIEW, verified_at=timezone.now(), match_count=2)
        staff = User.objects.create_user(username="staff", password="staff-secret", is_staff=True, is_active=True)
        self.client.force_login(staff, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.post(reverse("participants:staff-resolve-request", kwargs={"request_id":obj.pk}), {"participant":person.pk})
        self.assertRedirects(response, reverse("participants:staff-requests"))
        obj.refresh_from_db()
        self.assertEqual(obj.status, obj.Status.LINKED)
        self.assertEqual(ParticipantAccount.objects.get(participant=person).login_email, "new-login@example.com")
