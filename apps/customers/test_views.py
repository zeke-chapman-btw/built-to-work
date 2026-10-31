"""Route, form and read-only boundary tests using synthetic customer records."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, Client
from django.urls import reverse
from apps.core.models import AuditLog
from apps.core.audit import record_audit
from apps.customers import forms, services as svc
from apps.customers.models import CustomerOrganization as Org, CustomerIdentity, WorkforceNeedRevision, CustomerAuditScope
from apps.customers.test_domain import CustomerFixtures, WHY


class CustomerScreenTests(CustomerFixtures, TestCase):
    def setUp(self):
        self.org_a = self.org(name="SYNTHETIC Alpha company")
        self.org_b = self.org(name="SYNTHETIC Beta company")
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")

    def url(self, name, *args):
        return reverse("customers:" + name, args=args)

    def test_directory_and_all_tabs_render_without_writing(self):
        before = (AuditLog.objects.count(), Org.objects.count(), CustomerAuditScope.objects.count())
        for url in [self.url("directory"), self.url("campaign-directory")] + [self.url("workspace", self.org_a.pk, key) for key in ("overview", "company", "needs", "campaigns", "matches", "users", "access", "history")]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
        self.assertEqual(before, (AuditLog.objects.count(), Org.objects.count(), CustomerAuditScope.objects.count()))

    def test_legacy_global_audit_viewer_cannot_bypass_customer_history_boundary(self):
        self.unaffiliated.user_permissions.add(Permission.objects.get(content_type__app_label="core", codename="view_auditlog"))
        legacy = record_audit(actor=self.admin, action="synthetic.legacy_event", instance=self.admin, source="isolated_test", reason="SYNTHETIC unrelated audit remains visible")
        customer_log = self.org_a.audit_scopes.select_related("audit").first().audit
        self.client.force_login(self.unaffiliated, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.get(reverse("admin:core_auditlog_changelist"))
        self.assertEqual(response.status_code, 200)
        visible_ids = set(response.context["cl"].queryset.values_list("pk", flat=True))
        self.assertIn(legacy.pk, visible_ids)
        self.assertFalse(visible_ids.intersection(CustomerAuditScope.objects.values_list("audit_id", flat=True)))
        self.assertNotContains(response, str(self.org_a.pk))
        self.assertEqual(self.client.get(reverse("admin:core_auditlog_change", args=[customer_log.pk])).status_code, 302)
        # Admin's ordinary not-found redirect must not display the hidden record.
        self.assertEqual(self.client.get(reverse("admin:core_auditlog_change", args=[legacy.pk])).status_code, 200)
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("admin:core_auditlog_change", args=[customer_log.pk])).status_code, 200)

    def test_manager_and_combined_access_and_other_roles_denied(self):
        for actor in (self.manager, self.combined):
            self.client.force_login(actor, backend="django.contrib.auth.backends.ModelBackend")
            self.assertEqual(self.client.get(self.url("directory")).status_code, 200)
        for actor in (self.recruiter, self.worker, self.unaffiliated):
            self.client.force_login(actor, backend="django.contrib.auth.backends.ModelBackend")
            for url in (self.url("directory"), self.url("workspace", self.org_a.pk, "overview"), self.url("plan", self.org_a.pk), self.url("campaign-directory")):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_external_and_prepared_identities_cannot_enter(self):
        external = get_user_model().objects.create_user("synthetic-external")
        member = self.membership(self.org_a)
        for actor in (external, member.identity.user):
            self.client.force_login(actor, backend="django.contrib.auth.backends.ModelBackend")
            self.assertIn(self.client.get(self.url("directory")).status_code, (302, 403))
        self.client.logout()
        self.assertEqual(self.client.get(self.url("directory")).status_code, 302)

    def test_all_editors_render_without_writes(self):
        member = self.membership(self.org_a)
        contact = self.child(self.org_a, "contact", {"first_name": "SYNTHETIC Contact"})
        location = self.child(self.org_a, "location", {"name": "SYNTHETIC Location"})
        need = self.draft(self.org_a, {})
        campaign = self.campaign(self.org_a)
        count = AuditLog.objects.count()
        urls = [self.url("new"), self.url("edit", self.org_a.pk), self.url("plan", self.org_a.pk), self.url("member-new", self.org_a.pk), self.url("member-edit", self.org_a.pk, member.pk), self.url("identity-edit", self.org_a.pk, member.identity_id), self.url("primary", self.org_a.pk), self.url("relationship", self.org_a.pk), self.url("child-new", self.org_a.pk, "contact"), self.url("child-edit", self.org_a.pk, "contact", contact.pk), self.url("child-edit", self.org_a.pk, "location", location.pk), self.url("need-new", self.org_a.pk), self.url("need-edit", self.org_a.pk, need.pk), self.url("need", self.org_a.pk, need.pk), self.url("need-action", self.org_a.pk, need.pk, "submit"), self.url("campaign-new", self.org_a.pk), self.url("campaign-edit", self.org_a.pk, campaign.pk), self.url("campaign", self.org_a.pk, campaign.pk), self.url("campaign-action", self.org_a.pk, campaign.pk, "approve_activate"), self.url("organization-action", self.org_a.pk, "archive")]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(AuditLog.objects.count(), count)

    def test_forged_nested_ids_are_404(self):
        member = self.membership(self.org_b)
        contact = self.child(self.org_b, "contact", {"first_name": "SYNTHETIC other"})
        need, campaign = self.draft(self.org_b, {}), self.campaign(self.org_b)
        for name, args in [("member-edit", (member.pk,)), ("identity-edit", (member.identity_id,)), ("child-edit", ("contact", contact.pk)), ("need", (need.pk,)), ("need-edit", (need.pk,)), ("campaign", (campaign.pk,)), ("campaign-edit", (campaign.pk,))]:
            response = self.client.get(self.url(name, self.org_a.pk, *args))
            self.assertEqual(response.status_code, 404)

    def test_forged_posted_fields_rejected_and_input_preserved(self):
        count = Org.objects.count()
        response = self.client.post(self.url("new"), {"name": "SYNTHETIC entered company", "status": "prospect", "reason": WHY, "is_superuser": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unsupported submitted fields")
        self.assertContains(response, "SYNTHETIC entered company")
        self.assertEqual(Org.objects.count(), count)

    def test_post_creates_customer_with_unconfigured_plan(self):
        response = self.client.post(self.url("new"), {"name": "SYNTHETIC New via form", "status": "prospect", "reason": WHY})
        self.assertEqual(response.status_code, 302)
        obj = Org.objects.get(name="SYNTHETIC New via form")
        self.assertIsNone(obj.access_plan.lead_limit)
        self.assertFalse(obj.provisioned)

    def test_scoped_form_options_reject_other_company_contact(self):
        foreign = self.child(self.org_b, "contact", {"first_name": "SYNTHETIC foreign"})
        self.org_a.refresh_from_db()
        response = self.client.post(self.url("edit", self.org_a.pk), {"name": self.org_a.name, "status": "active", "org_revision": self.org_a.revision, "primary_contact": foreign.pk, "reason": WHY})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        self.org_a.refresh_from_db()
        self.assertIsNone(self.org_a.primary_contact_id)

    def test_stale_form_does_not_overwrite(self):
        old = self.org_a.revision
        self.plan(self.org_a, lead_limit=1)
        response = self.client.post(self.url("edit", self.org_a.pk), {"name": "SYNTHETIC stale", "status": "active", "org_revision": old, "reason": WHY})
        self.assertContains(response, "changed. Reload")
        self.org_a.refresh_from_db()
        self.assertNotEqual(self.org_a.name, "SYNTHETIC stale")

    def test_hiring_draft_form_keeps_missing_topics_as_draft(self):
        self.org_a.refresh_from_db()
        response = self.client.post(self.url("need-new", self.org_a.pk), {"org_revision": self.org_a.revision, "title": "SYNTHETIC draft", "reason": WHY})
        self.assertEqual(response.status_code, 302)
        need = self.org_a.workforce_needs.get()
        self.assertEqual(need.status, "draft")
        self.assertEqual(need.working_revision.title, "SYNTHETIC draft")
        self.org_a.refresh_from_db()
        response = self.client.post(self.url("need-action", self.org_a.pk, need.pk, "submit"), {"org_revision": self.org_a.revision, "record_revision": need.revision, "working_id": need.working_revision_id, "reason": WHY})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Answer this topic")
        need.refresh_from_db()
        self.assertEqual(need.working_revision.state, "draft")

    def test_no_posted_workflow_state_in_hiring_terms(self):
        form = forms.HiringForm({"org_revision": self.org_a.revision, "title": "SYNTHETIC", "state": "approved", "reason": WHY}, actor=self.admin, organization=self.org_a, instance=WorkforceNeedRevision(organization=self.org_a))
        self.assertFalse(form.is_valid())
        self.assertIn("Unsupported submitted fields", str(form.errors))
        self.assertIn("location_state", form.fields)

    def test_search_filters_pagination_and_return_context(self):
        for i in range(23):
            self.org(name=f"SYNTHETIC numbered {i:02}", configured=False)
        response = self.client.get(self.url("directory"), {"q": "numbered", "status": "prospect", "page": "2"})
        self.assertContains(response, "Page 2 of 2")
        self.assertEqual(len(response.context["rows"]), 3)
        self.assertContains(response, "return_to=")
        response = self.client.get(self.url("directory"), {"q": "missing-company"})
        self.assertContains(response, "No customers found")

    def test_history_does_not_leak_connected_private_records(self):
        self.child(self.org_b, "contact", {"first_name": "SYNTHETIC confidential"})
        response = self.client.get(self.url("workspace", self.org_a.pk, "history"), {"q": "contact.created"})
        self.assertEqual(response.context["rows"].paginator.count, 0)
        self.assertNotContains(response, "SYNTHETIC confidential")

    def test_return_link_does_not_accept_external_or_other_subsystems(self):
        for value in ("https://example.test/", "//example.test/", "/admin/", "/participants/"):
            response = self.client.get(self.url("workspace", self.org_a.pk, "overview"), {"return_to": value})
            self.assertEqual(response.context["back_url"], self.url("directory"))

    def test_csrf_is_required_on_mutation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(client.post(self.url("new"), {"name": "forged", "reason": WHY}).status_code, 403)

    def test_native_customer_admin_is_read_only_and_identity_admin_change_denied(self):
        member = self.membership(self.org_a)
        response = self.client.post(reverse("admin:customers_customerorganization_change", args=[self.org_a.pk]), {"name": "forged"})
        self.assertEqual(response.status_code, 403)
        # The Administrator may inspect the existing read-only identity form;
        # POST and password changes must never convert or activate the account.
        user_url = reverse("admin:accounts_user_change", args=[member.identity.user_id])
        self.assertEqual(self.client.get(user_url).status_code, 200)
        self.assertEqual(self.client.post(user_url, {"is_active": "on", "is_staff": "on"}).status_code, 403)
        member.identity.user.refresh_from_db()
        self.assertFalse(member.identity.user.is_active)
        self.assertFalse(member.identity.user.is_staff)

    def test_prepared_identity_shows_pending_access_without_password_setup(self):
        self.membership(self.org_a)
        response = self.client.get(self.url("workspace", self.org_a.pk, "users"))
        self.assertContains(response, "Awaiting invitation acceptance")
        self.assertContains(response, "Invitation: Not invited")
        self.assertNotContains(response, "Send invitation")
        self.assertNotContains(response, "Set password")

    def test_effective_and_pending_details_are_distinct_and_history_collapsed(self):
        need = self.approved_need(self.org_a)
        self.draft(self.org_a, {"worker_quantity": 8}, need=need)
        response = self.client.get(self.url("need", self.org_a.pk, need.pk))
        self.assertContains(response, "Effective approved hiring details")
        self.assertContains(response, "Pending changes")
        self.assertContains(response, "not effective until approved")
        self.assertContains(response, "<summary>Revision history</summary>", html=True)
