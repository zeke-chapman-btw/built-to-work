"""Admins and managers can archive, restore and delete a participant, always with a confirmation."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts.roles import ROLE_GROUPS
from apps.core.models import AuditLog

from .models import Participant, ParticipantNote


class RemovalTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_superuser("rm-admin", "rm@example.test", "x-test-only")
        self.manager = users.objects.create_user("rm-manager", "rmm@example.test", "x-test-only", is_staff=True)
        self.manager.groups.add(Group.objects.get_or_create(name=ROLE_GROUPS["manager"])[0])
        self.recruiter = users.objects.create_user("rm-recruiter", "rmr@example.test", "x-test-only", is_staff=True)
        self.recruiter.groups.add(Group.objects.get_or_create(name=ROLE_GROUPS["recruiter"])[0])
        self.person = Participant.objects.create(first_name="SYNTHETIC", last_name="Remove", contact_email="rm-person@example.test")
        ParticipantNote.objects.create(participant=self.person, content="SYNTHETIC note", author=self.admin)

    def login(self, user):
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")

    def test_manager_archives_and_restores_with_confirmation(self):
        self.login(self.manager)
        url = reverse("participants:participant-archive", args=[self.person.pk])
        self.assertContains(self.client.get(url, HTTP_X_BTW_EDITOR="1"), "Archive SYNTHETIC Remove")
        self.client.post(url, {}, HTTP_X_BTW_EDITOR="1")
        self.person.refresh_from_db()
        self.assertIsNone(self.person.archived_at)  # nothing happens without ticking the confirmation
        self.client.post(url, {"confirm": "on"}, HTTP_X_BTW_EDITOR="1")
        self.person.refresh_from_db()
        self.assertIsNotNone(self.person.archived_at)
        self.client.post(reverse("participants:participant-restore", args=[self.person.pk]), {"confirm": "on"}, HTTP_X_BTW_EDITOR="1")
        self.person.refresh_from_db()
        self.assertIsNone(self.person.archived_at)
        self.assertEqual(AuditLog.objects.filter(action__in=["participants.participant_archived", "participants.participant_restored"]).count(), 2)

    def test_admin_deletes_person_and_their_records(self):
        self.login(self.admin)
        url = reverse("participants:participant-delete", args=[self.person.pk])
        page = self.client.get(url, HTTP_X_BTW_EDITOR="1").content.decode()
        self.assertIn("This can&#x27;t be undone", page)
        self.client.post(url, {"confirm": "on"}, HTTP_X_BTW_EDITOR="1")
        self.assertFalse(Participant.objects.filter(pk=self.person.pk).exists())
        self.assertFalse(ParticipantNote.objects.filter(content="SYNTHETIC note").exists())
        self.assertTrue(AuditLog.objects.filter(action="participants.participant_deleted").exists())

    def test_other_staff_cannot(self):
        self.login(self.recruiter)
        for name in ("participant-archive", "participant-delete"):
            self.assertEqual(self.client.post(reverse("participants:" + name, args=[self.person.pk]), {"confirm": "on"}).status_code, 403)
        self.assertTrue(Participant.objects.filter(pk=self.person.pk, archived_at__isnull=True).exists())
