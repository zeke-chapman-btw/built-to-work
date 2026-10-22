"""Synthetic, database-free tests of Build 11B intake contract boundaries."""
from datetime import date
import uuid
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from .intake_contracts import validate_contract, validate_answers, contract_hash
from .intake_defaults import default_contract


class IntakeContract11BTests(SimpleTestCase):
    today = date(2026, 10, 22)

    def answers(self, **changes):
        data = {"first_name": "SYNTHETIC", "last_name": "Example", "dob": "2004-01-01",
                "email": "SYNTHETIC@example.test", "phone": "(555) 010-1234",
                "city": "Example City", "state": "GA", "postal_code": "00123",
                "consent": True, "current_situation": "unemployed",
                "career_timeline": "exploring", "opportunity_types": ["exploration"],
                "travel_willingness": "no"}
        data.update(changes)
        return data

    def entry(self, **changes):
        data = {"entry_id": str(uuid.uuid4()), "area": "welding", "source": "school",
                "paid": "unpaid", "duration": "6_12_months", "skills": ["mig"]}
        data.update(changes)
        return data

    def clean(self, answers=None):
        return validate_answers(default_contract(), answers or self.answers(), occurred_on=self.today)

    def test_default_contract_hash_and_copy(self):
        c = default_contract()
        self.assertEqual(validate_contract(c), c)
        self.assertEqual(len(contract_hash(c)), 64)
        self.assertEqual(contract_hash(dict(reversed(list(c.items())))), contract_hash(c))
        c["questions"][0]["label"] = "Different wording"
        self.assertNotEqual(contract_hash(c), contract_hash(default_contract()))

    def test_mandatory_fields_at_service_boundary(self):
        for key in ["first_name", "last_name", "dob", "email", "phone", "city", "state",
                    "postal_code", "consent", "current_situation", "career_timeline",
                    "opportunity_types", "travel_willingness"]:
            data = self.answers()
            del data[key]
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self.clean(data)

    def test_typed_limits_normalization_postal(self):
        clean = self.clean()["answers"]
        self.assertEqual(clean["email"], "synthetic@example.test")
        self.assertEqual(clean["phone"], "5550101234")
        self.assertEqual(clean["postal_code"], "00123")
        for data in [self.answers(email="invalid"), self.answers(phone="x"),
                     self.answers(phone="invalid5550101234"),
                     self.answers(first_name="x"*101), self.answers(city="x"*101),
                     self.answers(postal_code="1"*25)]:
            with self.assertRaises(ValidationError):
                self.clean(data)

    def test_age_and_pay_applicability(self):
        self.clean(self.answers(dob="2012-10-22"))
        for data in [self.answers(dob="2012-10-23"), self.answers(dob="2027-01-01"),
                     self.answers(dob="invalid"), self.answers(dob="2009-01-01", desired_pay="open"),
                     self.answers(current_situation="high_school", desired_pay="open")]:
            with self.assertRaises(ValidationError):
                self.clean(data)
        self.assertEqual(self.clean(self.answers(desired_pay="open"))["answers"]["desired_pay"], "open")

    def test_travel_maybe_no_unanswered_states(self):
        clean = self.clean(self.answers(travel_willingness="maybe", travel_range="regional",
                                       updates_preference="no"))
        self.assertEqual(clean["answers"]["travel_willingness"], "maybe")
        self.assertEqual(clean["answers"]["updates_preference"], "no")
        empty = self.clean()
        self.assertEqual(empty["states"]["travel_range"], "not_applicable")
        self.assertEqual(empty["states"]["updates_preference"], "unanswered")
        with self.assertRaises(ValidationError):
            self.clean(self.answers(travel_willingness="maybe"))
        with self.assertRaises(ValidationError):
            self.clean(self.answers(travel_range="regional"))

    def test_interest_maxima_exclusivity_and_no_inferred_experience(self):
        for value in [["not_sure", "welding"], ["welding", "welding"],
                      ["welding", "hvac", "diesel", "electrical"]]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.clean(self.answers(industry_interests=value))
        self.assertIsNone(self.clean(self.answers(industry_interests=["electrical"]))["answers"]["experiences"])

    def test_repeated_experience_has_no_arbitrary_product_cap(self):
        with self.assertRaises(ValidationError):
            self.clean(self.answers(hands_on="yes"))
        rows = [self.entry() for _ in range(40)]
        self.assertEqual(len(self.clean(self.answers(hands_on="yes", experiences=rows))["answers"]["experiences"]), 40)
        for rows in [[rows[0], rows[0]], [self.entry(paid="unknown")],
                     [self.entry(area="supervision", skills=["mig"])]]:
            with self.assertRaises(ValidationError):
                self.clean(self.answers(hands_on="yes", experiences=rows))

    def test_all_three_persona_paths(self):
        alex = self.answers(dob="2009-01-01", current_situation="high_school",
                            school_name="SYNTHETIC School", hands_on="yes",
                            industry_interests=["welding"], experiences=[self.entry()])
        jordan = self.answers(current_situation="employed_other", current_industry="retail",
                              job_title="SYNTHETIC Associate", industry_interests=["electrical"],
                              hands_on="yes", experiences=[self.entry(area="electrical", source="personal", skills=[])],
                              travel_willingness="maybe", travel_range="local", updates_preference="no")
        chris = self.answers(dob="1983-01-01", current_situation="employed_trade",
                             current_industry="construction", job_title="SYNTHETIC Supervisor",
                             hands_on="yes", experiences=[self.entry(source="employment", paid="paid", duration="over_10_years"),
                             self.entry(area="supervision", skills=["crew", "safety"])],
                             certifications="yes", certification_choices=["osha30"],
                             travel_willingness="yes", travel_range="nationwide", desired_pay="40_plus")
        for name, data in [("Alex", alex), ("Jordan", jordan), ("Chris", chris)]:
            with self.subTest(persona=name):
                self.assertEqual(self.clean(data)["states"]["consent"], "answered")

    def test_protected_required_structure(self):
        for key in ["dob", "email", "phone", "consent", "career_timeline"]:
            contract = default_contract()
            next(q for q in contract["questions"] if q["key"] == key)["required"] = False
            with self.subTest(key=key), self.assertRaises(ValidationError):
                validate_contract(contract)
        for key in ["experiences", "industry_interests"]:
            contract = default_contract()
            contract["questions"] = [q for q in contract["questions"] if q["key"] != key]
            with self.assertRaises(ValidationError):
                validate_contract(contract)

    def test_invalid_rules_columns_and_unapproved_mappings(self):
        contract = default_contract()
        contract["questions"][0]["rule"] = {"field": "travel_willingness", "op": "equals", "value": "no"}
        with self.assertRaises(ValidationError):
            validate_contract(contract)
        contract = default_contract()
        contract["questions"][0]["destination"] = "user.is_superuser"
        with self.assertRaises(ValidationError):
            validate_contract(contract)
        contract = default_contract()
        contract["qualification_mappings"] = [{"question": "industry_interests", "value": "welding",
                                               "catalog_uuid": str(uuid.uuid4())}]
        with self.assertRaises(ValidationError):
            validate_contract(contract)
        contract = default_contract()
        contract["questions"][0]["max_length"] = 500
        with self.assertRaises(ValidationError):
            validate_contract(contract)

    def test_invalid_skills_and_malformed_repeat_structure(self):
        contract = default_contract()
        contract["area_skills"]["welding"] = ["not-published"]
        with self.assertRaises(ValidationError):
            validate_contract(contract)
        contract = default_contract()
        next(q for q in contract["questions"] if q["key"] == "experiences")["children"].append("malformed")
        with self.assertRaises(ValidationError):
            validate_contract(contract)

    def test_forged_answer_keys_and_nonaffirmative_consent(self):
        with self.assertRaises(ValidationError):
            self.clean(self.answers(is_superuser=True))
        with self.assertRaises(ValidationError):
            self.clean(self.answers(consent="yes"))
