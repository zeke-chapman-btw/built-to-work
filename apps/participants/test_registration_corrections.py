from datetime import date, timedelta
from django.test import SimpleTestCase
from django.urls import reverse
from .forms import SnapshotRegistrationForm

class SnapshotRegistrationFormTests(SimpleTestCase):
    def test_snapshot_controls_labels_order_and_choices(self):
        questions = [
            {"key": "life_stage", "label": "Stage", "field_type": "single_choice", "options": [["working", "Working"]], "required": True, "position": 2},
            {"key": "first_name", "label": "Preferred given name", "field_type": "short_text", "required": True, "position": 1},
        ]
        form = SnapshotRegistrationForm(questions=questions)
        self.assertEqual(list(form.fields), ["first_name", "life_stage"])
        self.assertEqual(form.fields["first_name"].label, "Preferred given name")
        self.assertEqual(form.fields["life_stage"].choices[0], ("working", "Working"))

    def test_hidden_required_question_is_not_required(self):
        questions = [{"key": "life_stage", "field_type": "single_choice", "options": [["working", "Working"]], "required": True, "position": 1}, {"key": "school_name", "field_type": "short_text", "required": True, "visibility_rule": {"field": "life_stage", "value": "student"}, "position": 2}]
        form = SnapshotRegistrationForm({"life_stage": "working"}, questions=questions)
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["school_name"])

    def test_future_and_underage_dates_are_rejected(self):
        questions = [{"key": "date_of_birth", "field_type": "date", "required": True, "position": 1}]
        future = SnapshotRegistrationForm({"date_of_birth": (date.today() + timedelta(days=1)).isoformat()}, questions=questions)
        self.assertFalse(future.is_valid())
        young = SnapshotRegistrationForm({"date_of_birth": date(date.today().year - 13, date.today().month, date.today().day).isoformat()}, questions=questions)
        self.assertFalse(young.is_valid())
