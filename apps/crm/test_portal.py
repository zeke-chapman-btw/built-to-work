"""Full employer entry/correction journeys and fail-closed disclosure boundaries."""
import uuid
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.customers.test_portal import BACKEND
from apps.operations.models import Task, TaskNotification
from apps.recruiting import employer
from apps.recruiting.models import CandidateDisclosure, EmployerCandidateFeedback
from apps.recruiting.test_employer import EmployerFixtures

from . import services
from .models import Interaction, InteractionRevision


class EmployerCrmTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        self.grant = self.release()
        self.client.force_login(self.customer, backend=BACKEND)
        self.stamp = timezone.now() - timedelta(days=1)

    def url(self, name="employer-new", row=None, grant=None, action=None):
        args = [(grant or self.grant).pk]
        if row:
            args.append(row.pk)
        if action:
            args.append(action)
        return reverse("crm:" + name, args=args)

    def data(self, **changes):
        data = {"operation_id": str(uuid.uuid4()), "activity_type": "call", "summary": "SYNTHETIC employer call",
            "occurred_at": self.stamp.strftime("%Y-%m-%dT%H:%M"), "outcome": "SYNTHETIC conversation completed"}
        data.update(changes)
        return data

    def record(self):
        response = self.client.post(self.url(), self.data())
        self.assertEqual(response.status_code, 302)
        return Interaction.objects.get(audience="employer")

    def test_call_and_interview_are_owned_external_records_in_existing_candidate_portal(self):
        candidate_before = (self.candidate.stage_id, self.candidate.outcome, self.candidate.revision)
        grants_before = list(CandidateDisclosure.objects.values())
        counts = (Task.objects.count(), TaskNotification.objects.count(), EmployerCandidateFeedback.objects.count())
        with patch("django.core.mail.send_mail") as send:
            for kind in ["call", "interview"]:
                self.assertRedirects(self.client.post(self.url(), self.data(activity_type=kind, summary="SYNTHETIC " + kind)),
                    reverse("customer_portal:recruiting-candidate", args=[self.grant.pk]) + "#company-interactions")
            send.assert_not_called()
        response = self.client.get(reverse("customer_portal:recruiting-candidate", args=[self.grant.pk]))
        self.assertContains(response, "Company interactions")
        self.assertContains(response, "SYNTHETIC interview")
        self.assertContains(response, "Your company's responses")
        self.assertEqual(Interaction.objects.count(), 2)
        for row in Interaction.objects.all():
            self.assertEqual((row.audience, row.source, row.delivery_status), ("employer", "manual", ""))
            self.assertEqual(row.owner_organization, self.organization)
            self.assertEqual(row.participant, self.person)
            self.assertEqual(row.created_by, self.customer)
        self.candidate.refresh_from_db()
        self.assertEqual(candidate_before, (self.candidate.stage_id, self.candidate.outcome, self.candidate.revision))
        self.assertEqual(grants_before, list(CandidateDisclosure.objects.values()))
        self.assertEqual(counts, (Task.objects.count(), TaskNotification.objects.count(), EmployerCandidateFeedback.objects.count()))

    def test_validation_recovery_idempotency_and_untrusted_fields(self):
        payload = self.data(occurred_at="bad", summary="SYNTHETIC preserved answer")
        response = self.client.post(self.url(), payload)
        self.assertContains(response, "SYNTHETIC preserved answer")
        self.assertFalse(Interaction.objects.exists())
        for key in ["participant_id", "organization_id", "audience", "source", "responsible_staff", "disclosure_id"]:
            response = self.client.post(self.url(), self.data(**{key: "forged"}))
            self.assertContains(response, "Only the displayed interaction details may be submitted")
        self.assertFalse(Interaction.objects.exists())
        payload = self.data()
        self.assertEqual(self.client.post(self.url(), payload).status_code, 302)
        self.assertEqual(self.client.post(self.url(), payload).status_code, 302)
        self.assertEqual(Interaction.objects.count(), 1)

    def test_correction_preserves_author_and_revision_history_and_rejects_stale_edits(self):
        row = self.record()
        author, created = row.created_by_id, row.created_at
        response = self.client.post(self.url("employer-correct", row), self.data(summary="SYNTHETIC corrected", revision=1, reason="SYNTHETIC clarify outcome"))
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertEqual((row.created_by_id, row.created_at, row.revision), (author, created, 2))
        detail = self.client.get(self.url("employer-detail", row))
        self.assertContains(detail, "SYNTHETIC corrected")
        self.assertContains(detail, "SYNTHETIC employer call")
        self.assertContains(detail, "Version 2")
        stale = self.client.post(self.url("employer-correct", row), self.data(summary="SYNTHETIC stale", revision=1, reason="SYNTHETIC stale browser tab"))
        self.assertContains(stale, "This interaction changed")
        row.refresh_from_db(); self.assertEqual(row.summary, "SYNTHETIC corrected")
        self.assertEqual(InteractionRevision.objects.filter(interaction=row).count(), 2)

    def test_archive_restore_retains_history(self):
        row = self.record()
        self.assertEqual(self.client.post(self.url("employer-archive", row, action="archive"), {"revision": 1, "reason": "SYNTHETIC duplicate observation"}).status_code, 302)
        row.refresh_from_db(); self.assertIsNotNone(row.archived_at)
        self.assertEqual(services.employer_history(self.customer, self.organization.pk, self.grant.pk), [])
        self.assertEqual(len(services.employer_history(self.customer, self.organization.pk, self.grant.pk, include_archived=True)), 1)
        self.assertEqual(self.client.post(self.url("employer-archive", row, action="restore"), {"revision": 2, "reason": "SYNTHETIC restore reviewed entry"}).status_code, 302)
        row.refresh_from_db(); self.assertIsNone(row.archived_at)
        self.assertEqual(row.history.count(), 3)

    def test_member_readonly_and_author_scoped_correction(self):
        row = self.record()
        member = self.membership(self.organization, role="member"); user = self.activate(member)
        self.client.force_login(user, backend=BACKEND)
        self.assertEqual(self.client.get(self.url("employer-detail", row)).status_code, 200)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        permission = employer.member_permission(actor=self.admin, disclosure_id=self.grant.pk, membership_id=member.pk,
            can_respond=True, expected_revision=0, why="SYNTHETIC explicit response permission")
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.client.get(self.url("employer-correct", row)).status_code, 403)
        self.assertEqual(self.client.post(self.url(), self.data(summary="SYNTHETIC member entry")).status_code, 302)
        own = Interaction.objects.get(created_by=user)
        self.assertEqual(self.client.get(self.url("employer-correct", own)).status_code, 200)
        employer.member_permission(actor=self.admin, disclosure_id=self.grant.pk, membership_id=member.pk,
            can_respond=False, expected_revision=permission.revision, why="SYNTHETIC withdraw response permission")
        self.assertEqual(self.client.post(self.url("employer-correct", own), self.data(revision=1, reason="SYNTHETIC denied stale form")).status_code, 403)

    def test_foreign_company_cannot_read_or_write_known_urls(self):
        row = self.record()
        other, member, user = self.lead()
        self.client.force_login(user, backend=BACKEND)
        self.client.session.flush()
        self.client.force_login(user, backend=BACKEND)
        for name in ["employer-detail", "employer-correct"]:
            self.assertEqual(self.client.get(self.url(name, row)).status_code, 403)
        self.assertEqual(self.client.post(self.url(), self.data()).status_code, 403)
        self.assertEqual(Interaction.objects.count(), 1)

    def test_revocation_blocks_all_customer_reads_writes_but_btw_keeps_separate_history(self):
        row = self.record()
        employer.revoke(actor=self.admin, disclosure_id=self.grant.pk, expected_revision=self.grant.revision, why="SYNTHETIC disclosure withdrawn")
        for url in [self.url(), self.url("employer-detail", row), self.url("employer-correct", row), reverse("customer_portal:recruiting-candidate", args=[self.grant.pk])]:
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(self.url(), self.data()).status_code, 403)
        self.assertEqual([item.pk for item in services.staff_history(self.admin, audience="employer", participant_id=self.person.pk)], [row.pk])
        self.assertEqual(services.staff_history(self.admin, audience="btw", participant_id=self.person.pk), [])

    def test_current_identity_membership_projection_and_consent_are_enforced(self):
        row = self.record()
        self.career.skills_interests = "SYNTHETIC changed since disclosure"; self.career.save()
        self.assertEqual(self.client.get(self.url("employer-detail", row)).status_code, 403)
        self.assertEqual(self.client.post(self.url(), self.data()).status_code, 403)

    def test_internal_records_and_snapshot_identifiers_never_reach_customer_context(self):
        internal = services.create_internal(actor=self.admin, participant_id=self.person.pk, operation_id=uuid.uuid4(),
            data={"activity_type": "note", "summary": "SYNTHETIC private BTW note", "occurred_at": self.stamp})
        row = self.record()
        for url in [reverse("customer_portal:recruiting-candidate", args=[self.grant.pk]), self.url("employer-detail", row)]:
            response = self.client.get(url)
            self.assertNotContains(response, internal.summary)
            self.assertNotContains(response, "payload_fingerprint")
            self.assertNotContains(response, "participant_id")
            self.assertNotContains(response, "responsible_staff")
        self.assertEqual(self.client.get(self.url("employer-detail", internal)).status_code, 403)
        self.assertEqual(self.client.get(reverse("crm:detail", args=[row.pk])).status_code, 403)

    def test_no_csrf_bypass_and_wrong_realms_denied(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.customer, backend=BACKEND)
        self.assertEqual(client.post(self.url(), self.data()).status_code, 403)
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.client.force_login(self.portal, backend="apps.participants.auth.ParticipantAuthenticationBackend")
        self.assertNotEqual(self.client.get(self.url()).status_code, 200)

    def test_customer_preview_remains_view_only_even_with_response_authority(self):
        row = self.record()
        @contextmanager
        def preview_request(request):
            request.customer_preview = True
            yield
        with patch("apps.customers.portal_preview.review_context", preview_request):
            page = self.client.get(reverse("customer_portal:recruiting-candidate", args=[self.grant.pk]))
            self.assertEqual(page.status_code, 200)
            self.assertNotContains(page, 'href="' + self.url() + '"')
            detail = self.client.get(self.url("employer-detail", row))
            self.assertNotContains(detail, "Correct entry")
            self.assertEqual(self.client.post(self.url(), self.data()).status_code, 403)
            self.assertEqual(self.client.post(self.url("employer-correct", row), self.data(revision=1, reason="SYNTHETIC denied preview write")).status_code, 403)
            self.assertEqual(self.client.post(self.url("employer-archive", row, action="archive"), {"revision":1,"reason":"SYNTHETIC denied preview archive"}).status_code, 403)
        self.assertEqual(Interaction.objects.count(), 1)
        self.assertEqual(row.history.count(), 1)
