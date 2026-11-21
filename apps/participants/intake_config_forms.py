"""Plain-language Admin controls for the allowlisted intake contract."""
from copy import deepcopy
from django import forms
from django.core.exceptions import ValidationError
from apps.events.models import Event, TrailerInstance
from .models import QualificationCatalogEntry, IntakeFormAssignment
from .intake_labels import destination_choices
from .intake_contracts import DESTINATIONS, KINDS, PROTECTED, validate_contract


import re

CHOICE_TYPES = {"single_choice", "multiple_choice"}
TYPE_LABELS = {"short_text": "Short answer", "long_text": "Long answer", "single_choice": "Choose one answer",
               "multiple_choice": "Choose several answers", "date": "Date", "email": "Email", "phone": "Phone",
               "consent": "Consent", "repeat_group": "Repeating list"}


def option_lines(options):
    return "\n".join(item["value"] + " | " + item["label"] for item in options)


def parse_options(text):
    options = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if "|" not in line:
            raise ValidationError("Use one choice code | displayed answer per line.")
        value, label = (part.strip() for part in line.split("|", 1))
        options.append({"value": value, "label": label})
    return options


def _new_value(label, taken):
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "choice"
    value, n = base, 2
    while value in taken:
        value, n = f"{base}_{n}", n + 1
    return value


def decode_rule(rule):
    """(mode, question key, [answer values]) for the plain "Show this question" controls."""
    if not rule:
        return "always", "", []
    if rule == {"op": "adult_non_student"}:
        return "adult", "", []
    if set(rule) == {"field", "op", "value"}:
        values = rule["value"] if isinstance(rule["value"], list) else [rule["value"]]
        return "answer", rule["field"], values
    parts = rule.get("any")
    if isinstance(parts, list) and parts and all(isinstance(p, dict) and set(p) == {"field", "op", "value"}
                                                 and p["op"] == "contains" for p in parts):
        fields = {p["field"] for p in parts}
        if len(fields) == 1:
            return "answer", fields.pop(), [p["value"] for p in parts]
    return "custom", "", []


class QuestionConfigForm(forms.Form):
    label = forms.CharField(max_length=240, label="Question")
    help_text = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), max_length=2000,
                                label="Extra help under the question", help_text="Optional. Shown in smaller text.")
    type = forms.ChoiceField(label="Answer type", choices=[(k, TYPE_LABELS[k]) for k in
        ("short_text", "long_text", "single_choice", "multiple_choice", "date", "email", "phone")])
    step = forms.TypedChoiceField(label="Step", coerce=int, choices=[(1, "1 · Personal information"), (2, "3 · Education and employment"),
        (3, "4 · Trade interests and experience"), (4, "5 · Career preferences")])
    required = forms.BooleanField(required=False, label="People must answer this")
    show_when = forms.ChoiceField(label="Show this question", widget=forms.RadioSelect, choices=[
        ("always", "Always"), ("answer", "Only when an earlier answer is picked"),
        ("adult", "Only for adults (18+) who aren't in high school")])
    show_field = forms.ChoiceField(required=False, label="Earlier question")
    show_values = forms.MultipleChoiceField(required=False, label="Answers that show it", widget=forms.CheckboxSelectMultiple)
    max_selections = forms.IntegerField(required=False, min_value=1, label="People can pick up to",
                                        help_text="Leave blank for no limit.")
    min_selections = forms.IntegerField(required=False, min_value=0, label="People must pick at least")
    destination = forms.ChoiceField(required=False, label="Where the answer is saved",
                                    help_text="Which part of the participant's profile this answer fills in.")

    def __init__(self, *args, contract, key=None, child=False, **kwargs):
        self.contract, self.key, self.child = deepcopy(contract), key, child
        questions = (next(q for q in contract["questions"] if q["key"] == "experiences")["children"]
                     if child else contract["questions"])
        self.question = next((q for q in questions if q["key"] == key), None)
        super().__init__(*args, **kwargs)
        q = self.question or {}
        self.is_core = bool(q) and (key in PROTECTED or child)
        self.fields["destination"].choices = destination_choices(DESTINATIONS, child=child, current=q.get("destination"))
        prior = []
        for other in questions:
            if other["key"] == key:
                break
            if other["type"] in CHOICE_TYPES:
                prior.append(other)
        self.prior = {other["key"]: other for other in prior}
        self.fields["show_field"].choices = [("", "Choose a question")] + [(o["key"], o["label"]) for o in prior]
        self.fields["show_values"].choices = [(f"{o['key']}::{item['value']}", item["label"]) for o in prior for item in o.get("options", [])]
        mode, field, values = decode_rule(q.get("rule"))
        if mode == "custom":
            self.fields["show_when"].choices = [("custom", "Keep the current rule")] + self.fields["show_when"].choices
        self.rule_mode = mode
        if q.get("type") == "consent":
            self.fields["step"].choices = [(1, "2 · Consent")]
        if q:
            self.initial.update({k: q.get(k) for k in ("label", "help_text", "type", "step", "required", "destination",
                                                       "max_selections", "min_selections")})
            self.initial.update(show_when=mode, show_field=field, show_values=[f"{field}::{v}" for v in values])
            if q["type"] not in dict(self.fields["type"].choices):
                self.fields["type"].choices += [(q["type"], TYPE_LABELS.get(q["type"], q["type"]))]
        else:
            self.initial.update(step=4, type="short_text", destination="submission_only", show_when="always")
        # Core questions keep their answer type and profile field; everything else stays editable.
        if self.is_core or q.get("type") in {"repeat_group", "consent"}:
            for name in ("type", "destination"):
                self.fields[name].disabled = True
        if child:
            self.fields["step"].disabled = True
        self.type_label = TYPE_LABELS.get(q.get("type") or "short_text", "")
        self.option_rows = self._option_rows(q)

    def _option_rows(self, q):
        if self.is_bound:
            tokens, labels = self.data.getlist("opt_token"), self.data.getlist("opt_label")
            alone = set(self.data.getlist("opt_alone"))
            return [{"token": t, "label": l, "alone": t in alone} for t, l in zip(tokens, labels)]
        exclusive = set(q.get("exclusive", []))
        return [{"token": o["value"], "label": o["label"], "alone": o["value"] in exclusive} for o in q.get("options", [])]

    def clean(self):
        data = super().clean()
        kind = data.get("type") or (self.question or {}).get("type")
        rows = [r for r in self.option_rows if r["label"].strip()]
        if kind in CHOICE_TYPES and not rows:
            raise ValidationError("Add at least one answer people can pick.")
        if data.get("show_when") == "answer":
            field = data.get("show_field")
            picked = [v.split("::", 1)[1] for v in data.get("show_values", []) if v.split("::", 1)[0] == field]
            if not field or not picked:
                self.add_error("show_values", "Choose the earlier question and at least one of its answers.")
            data["picked"] = picked
        return data

    def _rule(self, data):
        mode = data["show_when"]
        current = (self.question or {}).get("rule")
        if mode == "custom":
            return current
        if mode == "always":
            return None
        if mode == "adult":
            return {"op": "adult_non_student"}
        field, picked = data["show_field"], data["picked"]
        parent = self.prior[field]
        order = [o["value"] for o in parent["options"]]
        picked = sorted(set(picked), key=order.index)
        old_mode, old_field, old_values = decode_rule(current)
        if old_mode == "answer" and old_field == field and set(old_values) == set(picked):
            return current  # unchanged: keep it exactly as it was
        if parent["type"] == "multiple_choice":
            parts = [{"field": field, "op": "contains", "value": v} for v in picked]
            return parts[0] if len(parts) == 1 else {"any": parts}
        if len(picked) == 1:
            return {"field": field, "op": "equals", "value": picked[0]}
        return {"field": field, "op": "in", "value": picked}

    def updated_contract(self, new_key=None):
        data = self.cleaned_data
        key = self.key or new_key
        question = deepcopy(self.question) if self.question else {"key": key}
        question.update({k: data[k] for k in ("label", "type", "step", "required")})
        question["destination"] = data["destination"] or "submission_only"
        question["help_text"] = data["help_text"]
        if question["type"] in CHOICE_TYPES:
            taken, options, exclusive = set(), [], []
            known = {o["value"] for o in (self.question or {}).get("options", [])}
            for row in (r for r in self.option_rows if r["label"].strip()):
                value = row["token"] if row["token"] in known and row["token"] not in taken else _new_value(row["label"], taken | known)
                taken.add(value)
                options.append({"value": value, "label": row["label"].strip()})
                if row["alone"]:
                    exclusive.append(value)
            question["options"], question["exclusive"] = options, exclusive
        else:
            question["options"], question["exclusive"] = [], []
        for name in ("max_selections", "min_selections"):
            if question["type"] == "multiple_choice" and data[name] is not None:
                question[name] = data[name]
            else:
                question.pop(name, None)
        rule = self._rule(data)
        if rule:
            question["rule"] = rule
        else:
            question.pop("rule", None)
        questions = (next(q for q in self.contract["questions"] if q["key"] == "experiences")["children"]
                     if self.child else self.contract["questions"])
        if self.question:
            questions[questions.index(self.question)] = question
        else:
            questions.append(question)
        self.contract["questions"].sort(key=lambda q: q["step"])
        return validate_contract(self.contract)


class SkillRelationsForm(forms.Form):
    def __init__(self, *args, contract, **kwargs):
        self.contract = deepcopy(contract)
        super().__init__(*args, **kwargs)
        children = {q["key"]: q for q in next(q for q in contract["questions"] if q["key"] == "experiences")["children"]}
        for area in children["area"]["options"]:
            name = "area_" + area["value"]
            self.fields[name] = forms.MultipleChoiceField(required=False, label=area["label"],
                choices=[(item["value"], item["label"]) for item in children["skills"]["options"]],
                widget=forms.CheckboxSelectMultiple)
            self.initial[name] = contract["area_skills"].get(area["value"], [])

    def updated_contract(self):
        self.contract["area_skills"] = {name.removeprefix("area_"): self.cleaned_data[name] for name in self.fields}
        return validate_contract(self.contract)


class QualificationMappingsForm(forms.Form):
    def __init__(self, *args, contract, **kwargs):
        self.contract = deepcopy(contract)
        super().__init__(*args, **kwargs)
        questions = {q["key"]: q for q in contract["questions"]}
        questions["skills"] = next(q for q in questions["experiences"]["children"] if q["key"] == "skills")
        old = {(m["question"], m["value"]): m["catalog_uuid"] for m in contract["qualification_mappings"]}
        self.pairs = {}
        for key, kind in [("skills", "skill"), ("license_class", "license"), ("certification_choices", "certification")]:
            choices = [("", "Don't add to profile")] + [(str(e.stable_id), e.name)
                for e in QualificationCatalogEntry.objects.filter(is_active=True, category__is_active=True, kind=kind).order_by("name")]
            for index, option in enumerate(questions[key]["options"]):
                name = key + "_" + str(index)
                self.pairs[name] = (key, option["value"])
                self.fields[name] = forms.ChoiceField(required=False, choices=choices,
                    label=questions[key]["label"] + " — " + option["label"])
                self.initial[name] = old.get((key, option["value"]), "")

    def updated_contract(self):
        self.contract["qualification_mappings"] = [
            {"question": self.pairs[name][0], "value": self.pairs[name][1], "catalog_uuid": value}
            for name, value in self.cleaned_data.items() if value]
        return validate_contract(self.contract)


class AssignmentForm(forms.Form):
    channel = forms.ChoiceField(choices=IntakeFormAssignment.Channel.choices)
    event = forms.ModelChoiceField(queryset=Event.objects.order_by("-start_at"), required=False)
    trailer = forms.ModelChoiceField(queryset=TrailerInstance.objects.filter(is_active=True).order_by("identity"), required=False)
    confirmed = forms.BooleanField(label="Activate this published version only for the selected channel and event")
