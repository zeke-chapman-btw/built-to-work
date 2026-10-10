from django.test import SimpleTestCase
from .offline_views import OfflineRegistrationForm
from django.urls import reverse


class OfflineRegistrationWorkflowTests(SimpleTestCase):
    def test_offline_pages_require_staff_authentication(self):
        response = self.client.get(reverse('events:offline-preparations'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_staff_workflow_routes_are_defined(self):
        self.assertIn('/offline/', reverse('events:offline-preparations'))
        self.assertIn('/activate/', reverse('events:offline-activate', args=['00000000-0000-0000-0000-000000000001']))
        self.assertIn('/register/', reverse('events:offline-register', args=['00000000-0000-0000-0000-000000000001']))
        self.assertIn('/pdf/', reverse('events:offline-ticket-pdf', args=['00000000-0000-0000-0000-000000000001']))

    def test_ticket_pdf_endpoint_is_staff_protected(self):
        response = self.client.get(reverse('events:offline-ticket-pdf', args=['00000000-0000-0000-0000-000000000001']))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)


class OfflineRegistrationQuestionTests(SimpleTestCase):
    def _preparation(self):
        question = type("Question", (), {"key": "grade", "label": "Grade", "field_type": "short_text", "options": [], "is_required": True, "help_text": ""})()
        groups = MockManager([])
        questions = MockManager([question])
        event = type("Event", (), {"group_mode": "disabled", "groups": groups, "registration_questions": questions})()
        return type("Preparation", (), {"event": event})()

    def test_required_event_question_is_rendered_and_validated(self):
        form = OfflineRegistrationForm(data={"first_name": "A", "last_name": "B", "consent_ack": "on"}, preparation=self._preparation())
        self.assertIn("grade", form.fields)
        self.assertFalse(form.is_valid())
        self.assertIn("grade", form.errors)

    def test_optional_event_question_allows_registration_without_answer(self):
        preparation = self._preparation()
        preparation.event.registration_questions = MockManager([type("Question", (), {"key": "note", "label": "Note", "field_type": "long_text", "options": [], "is_required": False, "help_text": ""})()])
        form = OfflineRegistrationForm(data={"first_name": "A", "last_name": "B", "consent_ack": "on"}, preparation=preparation)
        self.assertTrue(form.is_valid())


class MockManager:
    def __init__(self, values):
        self.values = values
    def filter(self, **kwargs):
        return self
    def order_by(self, *args):
        return self.values
