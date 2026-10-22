"""Event presentation regressions: form contracts, context and navigation boundaries."""
from datetime import timedelta
from django import forms
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from apps.events.models import Event
from apps.staffing.models import StaffProfile, StaffAssignment, AssignmentDay
from apps.operations import workspace


class EventFormPatternTests(SimpleTestCase):
    def test_bound_field_keeps_post_name_value_and_explicit_error_relationship(self):
        class Sample(forms.Form):
            reason = forms.CharField(min_length=5, help_text="Explain the correction.")
        form = Sample({"reason": "x"})
        self.assertFalse(form.is_valid())
        html = Template('{% load ui_patterns %}{% ui_field form.reason "reissue_reason" %}').render(Context({"form": form}))
        self.assertIn('name="reason"', html)
        self.assertIn('value="x"', html)
        self.assertIn('for="reissue_reason"', html)
        self.assertIn('id="reissue_reason"', html)
        self.assertIn('aria-invalid="true"', html)
        self.assertIn('aria-describedby="reissue_reason_help reissue_reason_errors"', html)
        self.assertIn('id="reissue_reason_errors"', html)

    def test_two_ticket_reason_fields_have_distinct_labels_without_changing_post_names(self):
        class Reason(forms.Form):
            reason = forms.CharField(label="Reason")
            revision = forms.CharField(widget=forms.HiddenInput, initial="retained-revision")
        form = Reason()
        html = Template('{% load ui_patterns %}{% ui_field form.reason "reissue_reason" %}{% ui_field form.reason "void_reason" %}{{ form.revision }}').render(Context({"form": form}))
        self.assertEqual(html.count('name="reason"'), 2)
        self.assertEqual(html.count('id="reissue_reason"'), 1)
        self.assertEqual(html.count('id="void_reason"'), 1)
        self.assertIn('value="retained-revision"', html)

    def test_field_labels_and_values_are_escaped(self):
        class Sample(forms.Form):
            text = forms.CharField(label="<script>label</script>")
        html = Template('{% load ui_patterns %}{% ui_field form.text %}').render(Context({"form": Sample(initial={"text": '<script>value</script>'})}))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_checkbox_groups_keep_unique_child_ids_and_group_label(self):
        class Sample(forms.Form):
            dates = forms.MultipleChoiceField(choices=[("a", "Day A"), ("b", "Day B")], widget=forms.CheckboxSelectMultiple)
        html = Template('{% load ui_patterns %}{% ui_field form.dates %}').render(Context({"form": Sample()}))
        self.assertIn('aria-labelledby="id_dates_label"', html)
        self.assertIn('id="id_dates_0"', html)
        self.assertIn('for="id_dates_0"', html)
        self.assertIn('name="dates"', html)

    def test_event_return_accepts_only_the_exact_local_tasks_route(self):
        import uuid
        target = reverse("operations:event", args=[uuid.uuid4(), "tasks"])
        self.assertEqual(workspace.return_path(target + "?ignored=value#fragment"), target)
        for raw in ("https://example.test" + target, "//example.test" + target, target.replace("/tasks/", "/notes/"), "/admin/", target + "\\evil", target + "\r\nLocation:evil"):
            with self.subTest(raw=raw):
                self.assertEqual(workspace.return_path(raw), reverse("operations:tasks"))


class EventWorkspaceContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("synthetic-event-ui", password=None)
        cls.staff = get_user_model().objects.create_user("synthetic-event-ui-staff", is_staff=True)
        cls.person = StaffProfile.objects.create(user=cls.staff, first_name="SYNTHETIC", last_name="Reviewer")
        start = timezone.now() + timedelta(days=7)
        cls.event = Event.objects.create(name="SYNTHETIC Event UI", status="upcoming", start_at=start, end_at=start + timedelta(hours=4), timezone_name="America/New_York")
        cls.assignment = StaffAssignment.objects.create(event=cls.event, staff=cls.person)
        AssignmentDay.objects.create(assignment=cls.assignment, date=cls.event.local_start_at.date(), starts_at=start, ends_at=cls.event.end_at)

    def login(self, user=None):
        self.client.force_login(user or self.admin, backend="django.contrib.auth.backends.ModelBackend")

    def test_staff_coverage_groups_current_page_without_losing_assignment_records(self):
        self.login()
        response = self.client.get(reverse("staffing:event", args=[self.event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([a.pk for a in response.context["confirmed_assignments"]], [self.assignment.pk])
        self.assertEqual(len(response.context["assignment_history"]), 0)
        self.assertEqual(response.context["assignments"].paginator.count, 1)
        self.assertContains(response, "event-day-tile")
        self.assertContains(response, "Edit assignment")
        self.assertNotContains(response, ">Reassign Staff</a>")

    def test_event_routes_render_shared_shell_and_preserve_event_context(self):
        self.login()
        for route, args in (
            ("staffing:event", [self.event.pk]), ("workdays:event", [self.event.pk]),
            ("event_reports:detail", [self.event.pk]),
            ("operations:event", [self.event.pk, "notes"]),
            ("operations:event", [self.event.pk, "documents"]),
            ("events:public-settings", [self.event.pk]),
            ("assessments:event_configuration", [self.event.pk]),
        ):
            with self.subTest(route=route, args=args):
                response = self.client.get(reverse(route, args=args))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "event_workspace.css")
                self.assertContains(response, "event-record-header")

    def test_internal_event_styles_do_not_load_on_home_or_kiosk_shell(self):
        from django.template.loader import get_template
        self.login()
        self.assertNotContains(self.client.get(reverse("internal:home")), "event_workspace.css")
        source = get_template("assessments/kiosk_base.html").template.source
        self.assertNotIn("internal/base.html", source)
        self.assertNotIn("event_workspace.css", source)
        self.assertIn("kiosk/css/kiosk.css", source)

    def test_limited_staff_still_cannot_read_financials_or_change_staffing(self):
        self.login(self.staff)
        for route in ("event_reports:detail", "staffing:event"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(route, args=[self.event.pk])).status_code, 403)

    def test_read_only_pages_leave_assignment_and_event_revision_unchanged(self):
        self.login()
        before = (self.event.updated_at, self.assignment.updated_at, StaffAssignment.objects.count())
        for section in ("notes", "documents", "tasks"):
            self.client.get(reverse("operations:event", args=[self.event.pk, section]))
        self.event.refresh_from_db()
        self.assignment.refresh_from_db()
        self.assertEqual((self.event.updated_at, self.assignment.updated_at, StaffAssignment.objects.count()), before)


    def test_financial_history_keeps_authorized_event_context(self):
        from decimal import Decimal
        from uuid import uuid4
        from apps.event_reports.models import ExpenseCategory, ManualExpense
        category = ExpenseCategory.objects.create(name="SYNTHETIC review category")
        expense = ManualExpense.objects.create(event=self.event, category=category, category_name=category.name,
            description="SYNTHETIC expense", expense_date=self.event.local_start_at.date(), amount=Decimal("12.50"),
            request_key=uuid4(), reason="SYNTHETIC fixture", created_by=self.admin, edited_by=self.admin)
        self.login()
        response = self.client.get(reverse("event_reports:action", args=["expense", expense.pk, "history"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["event"], self.event)
        self.assertContains(response, "event-record-header")
        self.login(self.staff)
        self.assertEqual(self.client.get(reverse("event_reports:action", args=["expense", expense.pk, "history"])).status_code, 403)

    def test_note_editor_keeps_event_and_active_section_without_granting_private_note_access(self):
        from apps.operations.models import EventNote
        note = EventNote.objects.create(event=self.event, author=self.admin, content="SYNTHETIC manager note", visibility="managers")
        self.login()
        response = self.client.get(reverse("operations:note-edit", args=[note.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["event"], self.event)
        self.assertEqual(response.context["section"], "notes")
        self.login(self.staff)
        self.assertEqual(self.client.get(reverse("operations:note-edit", args=[note.pk])).status_code, 403)

    def test_task_navigation_keeps_the_originating_event_through_edit_and_status(self):
        from apps.operations.models import Task
        task = Task.objects.create(event=self.event, title="SYNTHETIC event task", assigned_staff=self.person, created_by=self.admin)
        origin = reverse("operations:event", args=[self.event.pk, "tasks"])
        self.login()
        response = self.client.get(origin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["tasks"][0].detail_url, workspace.detail_url(task, origin))
        detail = self.client.get(reverse("operations:task", args=[task.pk]), {"return_to": origin})
        self.assertEqual(detail.context["task_back_url"], origin)
        for route in ("operations:task-edit", "operations:task-status"):
            response = self.client.get(reverse(route, args=[task.pk]), {"return_to": origin})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["task_return_to"], origin)

    def test_return_path_does_not_grant_access_to_an_unassigned_event(self):
        from apps.operations.models import Task
        private = Event.objects.create(name="SYNTHETIC private draft", start_at=self.event.start_at,
            end_at=self.event.end_at, timezone_name="America/New_York")
        task = Task.objects.create(title="SYNTHETIC independent task", assigned_staff=self.person, created_by=self.admin)
        origin = reverse("operations:event", args=[private.pk, "tasks"])
        self.login(self.staff)
        response = self.client.get(reverse("operations:task", args=[task.pk]), {"return_to": origin})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, private.name)
        self.assertEqual(self.client.get(origin).status_code, 403)

    def test_operations_template_library_uses_an_explicit_internal_resource_scope(self):
        self.login()
        response = self.client.get(reverse("operations:templates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "has-event-resource-context")
        self.assertContains(response, "event_workspace.css")


    def test_registration_without_ticket_renders_only_the_available_void_action(self):
        from apps.events.models import EventRegistration
        from apps.participants.models import Participant
        participant = Participant.objects.create(first_name="SYNTHETIC", last_name="Registration")
        registration = EventRegistration.objects.create(event=self.event, participant=participant)
        self.login()
        response = self.client.get(reverse("events:registration_detail", args=[self.event.pk, registration.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "event-record-header")
        self.assertContains(response, 'id="registration_void_reason"')
        self.assertContains(response, 'name="reason"')
        self.assertNotContains(response, 'id="ticket_reissue_reason"')

    def test_inactive_registration_without_ticket_has_an_explicit_unavailable_state(self):
        from apps.events.models import EventRegistration
        from apps.participants.models import Participant
        participant = Participant.objects.create(first_name="SYNTHETIC", last_name="Inactive registration")
        inactive = next(value for value, label in EventRegistration._meta.get_field("status").choices if value != EventRegistration.Status.ACTIVE)
        registration = EventRegistration.objects.create(event=self.event, participant=participant, status=inactive)
        self.login()
        response = self.client.get(reverse("events:registration_detail", args=[self.event.pk, registration.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No ticket actions are available")
        self.assertNotContains(response, 'id="registration_void_reason"')
