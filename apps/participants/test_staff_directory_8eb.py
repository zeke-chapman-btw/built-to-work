from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.participants.models import Participant


class StaffParticipantDirectoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user(username="directory-staff", password="test-password", is_staff=True)
        permission = Permission.objects.get(
            codename="view_participant",
            content_type__app_label="participants",
            content_type__model="participant",
        )
        cls.staff.user_permissions.add(permission)
        cls.person = Participant.objects.create(
            first_name="Jordan", last_name="Rivers", preferred_name="Jordy",
            contact_email="jordan.rivers@example.test", contact_phone="(555) 111-2222",
        )

    def test_directory_requires_authentication_and_participant_permission(self):
        url = reverse("participants:staff-directory")
        self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user(username="unprivileged", password="test-password", is_staff=True)
        self.assertTrue(self.client.login(username="unprivileged", password="test-password"))
        self.assertEqual(self.client.get(url).status_code, 403)
        permitted_nonstaff = get_user_model().objects.create_user(username="participant-with-permission", password="test-password")
        permitted_nonstaff.user_permissions.add(Permission.objects.get(codename="view_participant", content_type__app_label="participants", content_type__model="participant"))
        self.assertTrue(self.client.login(username="participant-with-permission", password="test-password"))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_search_matches_full_name_email_and_phone(self):
        self.assertTrue(self.client.login(username="directory-staff", password="test-password"))
        url = reverse("participants:staff-directory")
        for query in ("Jordan Rivers", "jordan.rivers@example.test", "5551112222"):
            with self.subTest(query=query):
                response = self.client.get(url, {"q": query})
                self.assertEqual(response.status_code, 200, getattr(response, "url", None))
                self.assertContains(response, "Jordy")
                self.assertContains(response, "Rivers")
                self.assertContains(response, "1 matching participant")

    def test_profile_is_authorized_and_shows_safe_empty_sections(self):
        self.assertTrue(self.client.login(username="directory-staff", password="test-password"))
        response = self.client.get(reverse("participants:staff-profile", args=[self.person.pk]))
        self.assertEqual(response.status_code, 200, getattr(response, "url", None))
        self.assertNotContains(response, "Event registrations")
        self.assertNotContains(response, "Documents")
        self.assertNotContains(response, "Quiz activity")
        self.assertNotContains(response, "Game activity")

    def test_search_pagination_preserves_query(self):
        for index in range(26):
            Participant.objects.create(first_name=f"Directory{index}", last_name="Record")
        self.assertTrue(self.client.login(username="directory-staff", password="test-password"))
        response = self.client.get(reverse("participants:staff-directory"), {"q": "Directory", "page": 2})
        self.assertEqual(response.status_code, 200, getattr(response, "url", None))
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertContains(response, "q=Directory&amp;page=1")


    def test_profile_sections_require_their_own_permissions(self):
        self.assertTrue(self.client.login(username="directory-staff", password="test-password"))
        url = reverse("participants:staff-profile", args=[self.person.pk])
        response = self.client.get(url)
        self.assertNotContains(response, "Event registrations")
        self.assertNotContains(response, "Private document files remain available only")
        permissions = Permission.objects.filter(
            content_type__app_label="events", codename="view_eventregistration"
        ) | Permission.objects.filter(
            content_type__app_label="participants", codename="view_participantdocument"
        )
        self.staff.user_permissions.add(*permissions)
        response = self.client.get(url)
        self.assertContains(response, "Event registrations")
        self.assertContains(response, "Private document files remain available only through the authorized document workflow")

    def test_missing_participant_returns_404(self):
        self.assertTrue(self.client.login(username="directory-staff", password="test-password"))
        response = self.client.get(reverse("participants:staff-profile", args=["999999999"]))
        self.assertEqual(response.status_code, 404)
