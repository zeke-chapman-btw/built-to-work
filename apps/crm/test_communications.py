"""External draft provenance, manual texts and existing Task privacy."""
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.customers.models import CustomerContact
from apps.customers.test_portal import BACKEND
from apps.operations.models import Task, TaskNotification
from apps.recruiting.test_employer import EmployerFixtures
from apps.staffing.models import StaffProfile

from . import communications, services
from .models import ContactRelationship, Interaction
from .templatetags.crm_ui import crm_task_contexts


class CommunicationCrmTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup(); self.grant = self.release()
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.stamp = timezone.now() - timedelta(days=1)

    def row(self):
        return services.create_internal(actor=self.admin, participant_id=self.person.pk, operation_id=uuid.uuid4(),
            data={"activity_type":"call", "summary":"SYNTHETIC CRM task context", "occurred_at":self.stamp})

    def test_single_recipient_gmail_compose_does_not_send_or_record_an_activity(self):
        before = (Interaction.objects.count(), Task.objects.count(), TaskNotification.objects.count())
        with patch("django.core.mail.EmailMessage.send") as send:
            url = communications.gmail_compose("synthetic+review@example.test")
            page = self.client.get(reverse("participants:staff-profile", args=[self.person.pk]))
            self.assertEqual(page.status_code, 200)
            send.assert_not_called()
        parsed = urlsplit(url)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), ("https", "mail.google.com", "/mail/"))
        self.assertEqual(parse_qs(parsed.query), {"view":["cm"],"fs":["1"],"to":["synthetic+review@example.test"]})
        self.assertNotIn("/u/0", url)
        for bad in ["", "one@example.test,two@example.test", "one@example.test\r\nBcc: two@example.test", "javascript:alert(1)"]:
            self.assertEqual(communications.gmail_compose(bad), "")
        self.assertEqual(before, (Interaction.objects.count(), Task.objects.count(), TaskNotification.objects.count()))

    def test_recorded_updates_preference_is_not_replaced_by_ticket_or_disclosure_permission(self):
        with patch("apps.crm.communications.participant_preferences", return_value=("no", "Optional updates: No")):
            value = communications.relationship_communications(participant=self.person)
            self.assertEqual(value["email_url"], "")
            self.assertIn("declines", value["email_help"])
        with patch("apps.crm.communications.participant_preferences", return_value=("unknown", "Not recorded")):
            value = communications.relationship_communications(participant=self.person)
            self.assertIn("does not establish consent", value["email_help"])
        preference, label = communications.participant_preferences(self.person)
        self.assertIn(preference, ["yes", "no", "unknown"])
        self.assertIn("updates", label.lower())

    def test_inactive_contacts_and_method_suppression_disable_compose_without_auth_changes(self):
        contact = CustomerContact.objects.create(organization=self.organization, first_name="SYNTHETIC", last_name="Contact", email="synthetic-contact@example.test")
        self.assertTrue(communications.contact_compose(contact))
        services.save_contact_relationship(actor=self.admin, contact_id=contact.pk, expected_revision=0,
            data={"email_preference":"avoid"}, why="SYNTHETIC recorded no-email preference")
        self.assertEqual(communications.contact_compose(contact), "")
        self.organization.business_email=contact.email
        self.assertEqual(communications.relationship_communications(organization=self.organization)["email_url"], "")
        ContactRelationship.objects.filter(contact=contact).update(email_preference="unknown")
        contact.is_active=False;contact.save()
        self.assertEqual(communications.contact_compose(contact), "")

    def test_manual_email_and_sms_keep_manual_provenance_and_do_not_create_tasks_or_notifications(self):
        before = (Task.objects.count(), TaskNotification.objects.count())
        with patch("apps.events.communications._provider_send") as provider:
            for kind in ["email", "sms"]:
                for direction in ["inbound", "outbound"]:
                    response = self.client.post(reverse("crm:participant-new", args=[self.person.pk]),
                        {"operation_id":str(uuid.uuid4()),"activity_type":kind,"direction":direction,
                        "occurred_at":self.stamp.strftime("%Y-%m-%dT%H:%M"),"summary":"SYNTHETIC manually recorded " + kind})
                    self.assertEqual(response.status_code, 302)
            provider.assert_not_called()
        for row in Interaction.objects.all():
            self.assertEqual((row.source,row.provider,row.external_reference,row.delivery_status),("manual","","",""))
        self.assertEqual(before, (Task.objects.count(), TaskNotification.objects.count()))

    def test_existing_task_link_is_idempotent_visible_both_directions_and_does_not_widen_privacy(self):
        row = self.row()
        reader = get_user_model().objects.create_user("synthetic-crm-task-reader", is_staff=True)
        profile = StaffProfile.objects.create(user=reader, first_name="SYNTHETIC", last_name="Task reader")
        task = Task.objects.create(title="SYNTHETIC linked existing task",created_by=self.admin,assigned_staff=profile)
        url = reverse("crm:link-task", args=[row.pk])
        for _ in range(2):self.assertEqual(self.client.post(url,{"task":str(task.pk)}).status_code,302)
        self.assertEqual(row.task_links.count(),1)
        self.assertEqual(row.history.count(),2)
        self.assertContains(self.client.get(reverse("crm:detail",args=[row.pk])),task.title)
        self.assertContains(self.client.get(reverse("operations:task",args=[task.pk])),row.summary)
        self.assertEqual(crm_task_contexts(reader,task),[])
        self.client.force_login(reader,backend="django.contrib.auth.backends.ModelBackend")
        page=self.client.get(reverse("operations:task",args=[task.pk]))
        self.assertEqual(page.status_code,200);self.assertNotContains(page,row.summary)
        self.assertEqual(self.client.get(reverse("crm:detail",args=[row.pk])).status_code,403)

    def test_recent_activity_is_permission_filtered_and_keeps_two_histories(self):
        row=self.row()
        external=services.create_employer(actor=self.customer,organization_id=self.organization.pk,disclosure_id=self.grant.pk,
            operation_id=uuid.uuid4(),data={"activity_type":"interview","summary":"SYNTHETIC employer recent","occurred_at":self.stamp})
        page=self.client.get(reverse("crm:recent"))
        self.assertContains(page,row.summary);self.assertContains(page,external.summary)
        self.assertEqual([item["id"] for item in page.context["btw_items"]],[row.pk])
        self.assertEqual([item["id"] for item in page.context["employer_items"]],[external.pk])
        filtered=self.client.get(reverse("crm:recent"),{"q":"employer recent"})
        self.assertNotContains(filtered,row.summary);self.assertContains(filtered,external.summary)
        self.client.force_login(self.customer,backend=BACKEND)
        self.assertEqual(self.client.get(reverse("crm:recent")).status_code,403)
        self.assertNotContains(self.client.get(reverse("customer_portal:recruiting-candidate",args=[self.grant.pk])),"open Gmail")
