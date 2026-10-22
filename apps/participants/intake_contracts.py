"""Build 11B typed intake contracts. No identity writes or database access."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import re
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .normalization import normalize_email, normalize_phone

SCHEMA = "btw-intake-11b-v1"
KINDS = {"short_text", "long_text", "email", "phone", "date", "single_choice",
         "multiple_choice", "consent", "repeat_group"}
SITUATIONS = ["high_school", "college", "trade_school", "employed_trade",
              "employed_other", "unemployed", "retired", "other"]
EMPLOYED = ["employed_trade", "employed_other"]
INDUSTRIES = ["retail", "hospitality", "logistics", "manufacturing",
              "construction", "office", "agriculture", "other"]
TRADES = ["electrical", "welding", "hvac", "plumbing", "carpentry", "equipment",
          "diesel", "maintenance", "management", "other", "not_sure"]
DURATIONS = ["under_6_months", "6_12_months", "1_2_years", "3_5_years",
             "6_10_years", "over_10_years"]
OPPORTUNITIES = ["full_time", "part_time", "apprenticeship", "trade_school",
                 "internship", "exploration", "other"]
PRIORITIES = ["pay", "advancement", "learning", "security", "balance",
              "enjoyment", "difference", "other"]
# Destination identifiers are an allowlist, not ORM paths supplied by an administrator.
DESTINATIONS = {}
def _dest(names, kind, limit=None):
    for name in names.split():
        DESTINATIONS[name] = {"kind": kind, "max_length": limit}
_dest("first_name last_name preferred_name city state", "short_text", 100)
_dest("street_address", "short_text", 200)
_dest("postal_code", "short_text", 24)
_dest("email", "email", 254)
_dest("phone", "phone", 32)
_dest("dob", "date")
_dest("consent", "consent")
_dest("current_situation education_level current_industry employment_duration "
      "hands_on license_status license_class certifications career_timeline "
      "travel_willingness travel_range desired_pay updates_preference referral_source",
      "single_choice")
_dest("industry_interests certification_choices opportunity_types career_priorities", "multiple_choice")
_dest("school_name graduation training school_activities job_title industry_other "
      "interest_other opportunity_other", "short_text", 200)
_dest("job_responsibilities prior_employment", "long_text", 3000)
_dest("experiences", "repeat_group")
# Child destinations describe the common entry; these cannot write arbitrary fields.
_dest("area source paid duration proficiency", "single_choice")
_dest("skills", "multiple_choice")
_dest("description", "long_text", 1000)
PROTECTED = {
    "first_name": (1, "short_text"), "last_name": (1, "short_text"),
    "dob": (1, "date"), "email": (1, "email"), "phone": (1, "phone"),
    "city": (1, "short_text"), "state": (1, "short_text"),
    "postal_code": (1, "short_text"), "consent": (1, "consent"),
    "current_situation": (2, "single_choice"),
    "career_timeline": (4, "single_choice"),
    "opportunity_types": (4, "multiple_choice"),
    "travel_willingness": (4, "single_choice"),
}
EXPERIENCE_CHILDREN = {"area", "source", "paid", "duration", "skills", "proficiency", "description"}
REQUIRED_CHILDREN = {"area", "source", "paid", "duration"}


def canonical_json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Configuration must contain ordinary JSON values.") from exc


def contract_hash(contract):
    return hashlib.sha256(canonical_json(contract).encode("utf-8")).hexdigest()


def _fail(message):
    raise ValidationError(message)


def _options(question):
    options = question.get("options", [])
    if not isinstance(options, list):
        _fail("Choices must be a list of stable value/label objects.")
    result = []
    for item in options:
        if (not isinstance(item, dict) or set(item) != {"value", "label"}
                or not isinstance(item["value"], str) or not item["value"]
                or len(item["value"]) > 100 or not isinstance(item["label"], str)
                or not item["label"].strip() or len(item["label"]) > 240):
            _fail("Each choice needs a stable value and a plain-text label.")
        result.append(item["value"])
    if len(result) != len(set(result)):
        _fail("Choice values must be unique.")
    if question["type"] in {"single_choice", "multiple_choice"} and not result:
        _fail("Choice questions need choices.")
    return result


def _rule(rule, prior, depth=0):
    if not rule:
        return
    if not isinstance(rule, dict) or depth > 4:
        _fail("Unsupported or excessively nested condition.")
    if set(rule) in ({"all"}, {"any"}):
        group = next(iter(rule.values()))
        if not isinstance(group, list) or not group:
            _fail("Conditional groups cannot be empty.")
        for child in group:
            _rule(child, prior, depth + 1)
        if "all" in rule:
            equalities = {}
            for child in group:
                if child.get("op") == "equals":
                    key = child["field"]
                    if key in equalities and equalities[key] != child["value"]:
                        _fail("Contradictory conditions make the question inaccessible.")
                    equalities[key] = child["value"]
        return
    if rule.get("op") == "adult_non_student":
        if rule != {"op": "adult_non_student"}:
            _fail("Unsupported protected age condition.")
        return
    if set(rule) != {"field", "op", "value"} or rule["field"] not in prior:
        _fail("Conditions must reference an earlier supported question.")
    parent = prior[rule["field"]]
    if parent["type"] not in {"single_choice", "multiple_choice"}:
        _fail("Conditions must reference predefined choices.")
    values = _options(parent)
    op, expected = rule["op"], rule["value"]
    if op == "in":
        if not isinstance(expected, list) or not expected or any(v not in values for v in expected):
            _fail("The condition references unsupported choices.")
    elif op in {"equals", "contains"}:
        if expected not in values:
            _fail("The condition references an unsupported choice.")
        if op == "contains" and parent["type"] != "multiple_choice":
            _fail("Contains requires a multiple-choice source.")
    else:
        _fail("Unsupported condition operator.")


def visible(rule, answers, *, occurred_on):
    if not rule:
        return True
    if "all" in rule:
        return all(visible(child, answers, occurred_on=occurred_on) for child in rule["all"])
    if "any" in rule:
        return any(visible(child, answers, occurred_on=occurred_on) for child in rule["any"])
    if rule.get("op") == "adult_non_student":
        dob = answers.get("dob")
        return bool(dob and age_on(dob, occurred_on) >= 18
                    and answers.get("current_situation") != "high_school")
    value = answers.get(rule["field"])
    if rule["op"] == "in":
        return value in rule["value"]
    if rule["op"] == "contains":
        return isinstance(value, list) and rule["value"] in value
    return value == rule["value"]


def age_on(dob, occurred_on):
    return occurred_on.year - dob.year - ((occurred_on.month, occurred_on.day) < (dob.month, dob.day))


def _question(question, prior, destinations, *, child=False):
    allowed = {"key", "label", "type", "step", "required", "destination", "options",
               "rule", "max_length", "max_selections", "min_selections", "exclusive",
               "children", "help_text"}
    if not isinstance(question, dict) or set(question) - allowed:
        _fail("Unsupported question configuration.")
    key = question.get("key")
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", key):
        _fail("Question keys must be stable lowercase identifiers.")
    if key in prior:
        _fail("Question keys must be unique.")
    if question.get("type") not in KINDS:
        _fail("Unsupported question type.")
    if question.get("step") not in {1, 2, 3, 4}:
        _fail("Questions must belong to one of the four approved steps.")
    if (not isinstance(question.get("label"), str) or not question["label"].strip()
            or len(question["label"]) > 240 or type(question.get("required")) is not bool):
        _fail("Question wording and requiredness are invalid.")
    if not isinstance(question.get("help_text", ""), str) or len(question.get("help_text", "")) > 2000:
        _fail("Help text is too long or invalid.")
    destination = question.get("destination")
    if destination != "submission_only":
        if destination not in DESTINATIONS or DESTINATIONS[destination]["kind"] != question["type"]:
            _fail("Question and approved destination types are incompatible.")
        if destination in destinations:
            _fail("Multiple questions cannot compete for the same destination.")
        destinations.add(destination)
    if not child and destination in EXPERIENCE_CHILDREN:
        _fail("Experience child fields belong inside the common entry, not the current profile.")
    if question["type"] == "consent" and (key != "consent" or destination != "consent"):
        _fail("Use the approved versioned consent; additional legal text is outside this contract.")
    values = _options(question)
    _rule(question.get("rule"), prior)
    if question["type"] in {"short_text", "long_text", "email", "phone"}:
        default_limit = 3000 if question["type"] == "long_text" else 500
        limit = question.get("max_length", default_limit)
        maximum = DESTINATIONS.get(destination, {}).get("max_length") or default_limit
        if type(limit) is not int or not 1 <= limit <= maximum:
            _fail("Text length exceeds the approved destination limit.")
    for flag in ("max_selections", "min_selections"):
        if flag in question and (type(question[flag]) is not int or question[flag] < 0):
            _fail("Selection bounds must be nonnegative integers.")
    if question.get("min_selections", 0) > question.get("max_selections", len(values)):
        _fail("Selection bounds conflict.")
    exclusive = question.get("exclusive", [])
    if not isinstance(exclusive, list) or any(v not in values for v in exclusive):
        _fail("Exclusive selections must be configured choices.")
    if question["type"] == "repeat_group":
        if child or key != "experiences" or destination != "experiences":
            _fail("Only the supported common experience repeat group is available.")
        children = question.get("children")
        if (not isinstance(children, list) or any(not isinstance(q, dict) for q in children)
                or {q.get("key") for q in children} != EXPERIENCE_CHILDREN):
            _fail("The experience group must retain its complete approved structure.")
        child_prior, child_destinations = {}, set()
        for q in children:
            _question(q, child_prior, child_destinations, child=True)
            if q["key"] != q["destination"] or q["step"] != 3 or q.get("rule"):
                _fail("Experience fields cannot change identity, step or scope.")
            if q["required"] != (q["key"] in REQUIRED_CHILDREN):
                _fail("The required common experience fields cannot be weakened.")
            child_prior[q["key"]] = q
        if set(_options(child_prior["paid"])) != {"paid", "unpaid"}:
            _fail("Paid classification has exactly Paid and Unpaid choices.")
    elif "children" in question:
        _fail("Only experience groups may contain children.")


def validate_contract(contract):
    if (not isinstance(contract, dict) or set(contract) != {"schema", "questions", "area_skills", "qualification_mappings"}
            or contract["schema"] != SCHEMA or not isinstance(contract["questions"], list)):
        _fail("Unsupported intake contract schema.")
    canonical_json(contract)
    prior, destinations, last_step = {}, set(), 1
    for q in contract["questions"]:
        _question(q, prior, destinations)
        if q["step"] < last_step:
            _fail("Questions must follow the four-step order.")
        last_step = q["step"]
        prior[q["key"]] = q
    for key, (step, kind) in PROTECTED.items():
        q = prior.get(key, {})
        if (q.get("step") != step or q.get("type") != kind or q.get("required") is not True
                or q.get("rule") or q.get("destination") != key):
            _fail("Protected required question cannot be removed or weakened: " + key)
    for key in ("industry_interests", "career_priorities", "experiences", "hands_on",
                "travel_range", "desired_pay", "license_class", "certification_choices"):
        if key not in prior:
            _fail("Required intake structure is missing: " + key)
    exact = {"current_situation": set(SITUATIONS), "travel_willingness": {"yes", "no", "maybe"}}
    for key, values in exact.items():
        if set(_options(prior[key])) != values:
            _fail("Protected choices changed: " + key)
    if prior["desired_pay"].get("required"):
        _fail("Desired pay must remain optional.")
    if prior["opportunity_types"].get("min_selections") != 1:
        _fail("At least one opportunity type is required.")
    for key in ("industry_interests", "career_priorities"):
        q = prior.get(key, {})
        if q.get("max_selections") != 3:
            _fail("The approved three-selection maximum is required: " + key)
    if prior["industry_interests"].get("exclusive") != ["not_sure"]:
        _fail("Not sure yet must be exclusive.")
    essential_rules = {
        "current_industry": {"field": "current_situation", "op": "in", "value": EMPLOYED},
        "experiences": {"field": "hands_on", "op": "equals", "value": "yes"},
        "travel_range": {"field": "travel_willingness", "op": "in", "value": ["yes", "maybe"]},
        "desired_pay": {"op": "adult_non_student"},
        "license_class": {"field": "license_status", "op": "equals", "value": "valid"},
        "certification_choices": {"field": "certifications", "op": "equals", "value": "yes"},
    }
    for key, rule in essential_rules.items():
        q = prior.get(key, {})
        if q.get("rule") != rule:
            _fail("Protected conditional behavior changed: " + key)
        if key in {"current_industry", "experiences", "travel_range"} and not q.get("required"):
            _fail("The applicable question must be required: " + key)
    children = {q["key"]: q for q in prior["experiences"]["children"]}
    areas = set(_options(children["area"]))
    skills = set(_options(children["skills"]))
    relations = contract["area_skills"]
    if (not isinstance(relations, dict) or any(area not in areas for area in relations)
            or any(not isinstance(items, list) or any(skill not in skills for skill in items) for items in relations.values())):
        _fail("Area-specific skills must reference published area/skill choices.")
    mappings = contract["qualification_mappings"]
    if not isinstance(mappings, list):
        _fail("Qualification mappings must be a list.")
    seen = set()
    for mapping in mappings:
        if (not isinstance(mapping, dict)
                or not {"question", "value", "catalog_uuid"} <= set(mapping)
                or set(mapping) - {"question", "value", "catalog_uuid", "catalog_snapshot", "mapping_version"}
                or mapping["question"] not in {"certification_choices", "license_class", "skills"}):
            _fail("Only explicit credential or practiced-skill mappings are supported.")
        q = children["skills"] if mapping["question"] == "skills" else prior[mapping["question"]]
        if mapping["value"] not in _options(q):
            _fail("Qualification mapping references an unknown answer.")
        try:
            uuid.UUID(mapping["catalog_uuid"])
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError("Qualification mapping requires a stable catalog UUID.") from exc
        pair = (mapping["question"], mapping["value"])
        if pair in seen:
            _fail("Duplicate conflicting qualification mapping.")
        seen.add(pair)
    return deepcopy(contract)


def _answer(q, value, *, occurred_on):
    kind = q["type"]
    empty = value is None or value == "" or value == []
    if empty:
        if q["required"]:
            _fail("This answer is required.")
        return None
    if kind == "repeat_group":
        if not isinstance(value, list) or not value:
            _fail("Complete at least one experience entry.")
        rows, ids = [], set()
        for item in value:
            if not isinstance(item, dict) or set(item) - EXPERIENCE_CHILDREN - {"entry_id"}:
                _fail("Invalid experience entry.")
            try:
                entry_id = str(uuid.UUID(str(item.get("entry_id"))))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValidationError("Each experience needs a stable entry identifier.") from exc
            if entry_id in ids:
                _fail("Experience identifiers cannot be duplicated.")
            ids.add(entry_id)
            row = {"entry_id": entry_id}
            for child in q["children"]:
                row[child["key"]] = _answer(child, item.get(child["key"]), occurred_on=occurred_on)
            rows.append(row)
        return rows
    if kind == "multiple_choice":
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            _fail("Choose one or more supported options.")
        if len(value) != len(set(value)) or any(v not in _options(q) for v in value):
            _fail("Choose valid distinct options.")
        if not q.get("min_selections", int(q["required"])) <= len(value) <= q.get("max_selections", len(_options(q))):
            _fail("The selection count is outside the supported range.")
        if len(value) > 1 and any(v in value for v in q.get("exclusive", [])):
            _fail("Not sure yet cannot be combined with other interests.")
        return value
    if kind == "consent":
        if value is not True:
            _fail("The approved consent must be affirmatively accepted.")
        return True
    if not isinstance(value, str):
        _fail("Enter an answer in the supported format.")
    if kind == "single_choice":
        if value not in _options(q):
            _fail("Choose a supported option.")
        return value
    if kind == "date":
        try:
            dob = date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError("Enter a valid date of birth.") from exc
        if q["destination"] == "dob" and (dob > occurred_on or age_on(dob, occurred_on) < 14):
            _fail("Participants must be at least 14 at the time of intake.")
        return dob
    value = value.strip()
    if kind == "email":
        value = normalize_email(value)
        validate_email(value)
    elif kind == "phone":
        if not re.fullmatch(r"\+?[0-9\s().-]+", value):
            _fail("Enter a phone number using digits and standard phone punctuation.")
        value = normalize_phone(value)
        if not 7 <= len(value) <= 15:
            _fail("Enter a valid phone number, including area/country code where applicable.")
    if not value and q["required"]:
        _fail("This answer is required.")
    if len(value) > q.get("max_length", DESTINATIONS.get(q["destination"], {}).get("max_length") or 3000):
        _fail("This answer exceeds the supported field length.")
    return value or None


def validate_answers(contract, answers, *, occurred_on, through_step=4):
    return _validate_answers(contract, answers, occurred_on=occurred_on, through_step=through_step)


def validate_personal_answers(contract, answers, *, occurred_on):
    """Validate only personal information; never manufacture consent acceptance."""
    return _validate_answers(contract, answers, occurred_on=occurred_on, through_step=1, defer_consent=True)


def _validate_answers(contract, answers, *, occurred_on, through_step, defer_consent=False):
    validate_contract(contract)
    if not isinstance(answers, dict):
        _fail("Answers must be an object.")
    if type(through_step) is not int or through_step not in {1, 2, 3, 4}:
        _fail("Choose a supported intake step.")
    known = {q["key"] for q in contract["questions"]}
    if set(answers) - known:
        _fail("Unsupported answer keys.")
    clean, states, errors = {}, {}, {}
    for q in contract["questions"]:
        if q["step"] > through_step or (defer_consent and q["type"] == "consent"):
            continue
        key = q["key"]
        if not visible(q.get("rule"), clean, occurred_on=occurred_on):
            states[key] = "not_applicable"
            clean[key] = None
            if answers.get(key) not in (None, "", []):
                errors[key] = ["This answer does not apply to the selected path."]
            continue
        try:
            clean[key] = _answer(q, answers.get(key), occurred_on=occurred_on)
            states[key] = "answered" if clean[key] is not None else "unanswered"
        except ValidationError as exc:
            errors[key] = exc.messages
            clean[key] = None
    for row in clean.get("experiences") or []:
        allowed = contract["area_skills"].get(row["area"], [])
        if any(skill not in allowed for skill in row.get("skills") or []):
            errors["experiences"] = ["Select practiced skills applicable to the chosen area."]
    if errors:
        raise ValidationError(errors)
    return {"answers": {k: v.isoformat() if isinstance(v, date) else v for k, v in clean.items()},
            "states": states}


def canonical_answers(contract, answers):
    """Apply only the published, allowlisted canonical destination map."""
    return {q["destination"]: answers.get(q["key"]) for q in contract["questions"]
            if q["destination"] != "submission_only"}
