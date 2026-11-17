from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.participants.models import Participant

from . import store
from .models import MessageTemplate

BACKEND = "django.contrib.auth.backends.ModelBackend"


class TemplateTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="t-admin", password="x-test-only", first_name="Zeke")
        self.client.force_login(self.admin, backend=BACKEND)
        self.person = Participant.objects.create(first_name="SYNTHETIC", last_name="Jordan", contact_phone="7065550199",
                                                 contact_email="j@example.test")
        store.save(key="eztexting", values={"username": "btw"}, actor=self.admin, new_secret="pw-test-only")

    def test_settings_tab_create_and_use_in_text_popup(self):
        self.assertContains(self.client.get(reverse("internal:settings")), reverse("messaging:templates"))
        self.assertTrue(reverse("messaging:templates").startswith("/settings/"))
        response = self.client.post(reverse("messaging:template-new"), {"name": "Event reminder", "channel": "both",
            "subject": "See you {first_name}", "body": "Hi {first_name}, see you Saturday. - {my_name}", "is_active": "on"})
        self.assertEqual(response.status_code, 302)
        t = MessageTemplate.objects.get()
        page = self.client.get(reverse("messaging:text-participant", args=[self.person.pk]))
        self.assertContains(page, "Event reminder")
        page = self.client.get(reverse("messaging:text-participant", args=[self.person.pk]) + f"?template={t.pk}")
        self.assertContains(page, "Hi SYNTHETIC, see you Saturday. - Zeke")

    def test_email_only_and_hidden_templates(self):
        MessageTemplate.objects.create(name="Docs request", channel="email", subject="Documents", body="Hi {first_name}")
        MessageTemplate.objects.create(name="Old one", channel="both", body="x", is_active=False)
        text = self.client.get(reverse("messaging:text-participant", args=[self.person.pk]))
        self.assertNotContains(text, "Docs request")
        self.assertNotContains(text, "Old one")

    def test_delete_template(self):
        t = MessageTemplate.objects.create(name="Temp", body="x")
        self.client.post(reverse("messaging:template-edit", args=[t.pk]), {"remove": "1"})
        self.assertFalse(MessageTemplate.objects.exists())
