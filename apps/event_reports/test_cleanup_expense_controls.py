"""Expense filter and drawer controls must retain distinct accessible labels."""
from django.test import SimpleTestCase
from apps.event_reports.forms import ExpenseFilter, ExpenseForm


class ExpenseControlIdentityTests(SimpleTestCase):
    def test_filter_and_drawer_category_ids_do_not_collide(self):
        filters = ExpenseFilter({'category': ''})
        editor = ExpenseForm()
        self.assertNotEqual(filters['category'].id_for_label, editor['category'].id_for_label)
        self.assertEqual(filters['category'].html_name, 'category')
        self.assertEqual(editor['category'].html_name, 'category')
