from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.customers.position_view import pay_text, pay_range, PAY_TYPE
from apps.customers.presentation import derive_pay


class DerivePayTests(SimpleTestCase):
    def test_hourly_with_range_is_provided_and_defaults_currency(self):
        data = derive_pay({"pay_basis": "hour", "pay_minimum": Decimal("20"), "pay_maximum": Decimal("26"), "pay_frequency": "weekly"})
        self.assertEqual(data["pay_response"], "provided")
        self.assertEqual(data["pay_currency"], "USD")
        self.assertEqual(data["pay_frequency"], "weekly")

    def test_other_without_amount_clears_range(self):
        data = derive_pay({"pay_basis": "other", "pay_minimum": None, "pay_maximum": None, "pay_details": "Set after interview"})
        self.assertEqual(data["pay_response"], "not_applicable")
        self.assertIsNone(data["pay_minimum"])

    def test_no_pay_type_leaves_pay_unanswered(self):
        data = derive_pay({"pay_basis": "", "pay_minimum": None, "pay_maximum": None, "pay_frequency": "weekly"})
        self.assertEqual(data["pay_response"], "")
        self.assertEqual(data["pay_frequency"], "")


class PayDisplayTests(SimpleTestCase):
    def rev(self, **kw):
        base = dict(pay_basis="hour", pay_minimum=Decimal("20"), pay_maximum=Decimal("26"), pay_response="provided", pay_details="")
        base.update(kw)
        return SimpleNamespace(**base)

    def test_hourly_and_salary_text(self):
        self.assertEqual(pay_text(self.rev()), "$20–$26 / hr")
        self.assertEqual(pay_text(self.rev(pay_basis="year", pay_minimum=Decimal("50000"), pay_maximum=Decimal("60000"))), "Salary · $50,000–$60,000 / year")
        self.assertEqual(PAY_TYPE["hour"], "Hourly")
        self.assertEqual(PAY_TYPE["year"], "Salary")

    def test_notes_used_when_no_range(self):
        self.assertEqual(pay_text(self.rev(pay_basis="other", pay_minimum=None, pay_maximum=None, pay_details="Depends on experience")), "Depends on experience")
        self.assertEqual(pay_range(self.rev(pay_minimum=None, pay_maximum=None)), "")
