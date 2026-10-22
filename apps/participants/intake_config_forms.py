"""Plain-language Admin controls for the allowlisted intake contract."""
from copy import deepcopy
from django import forms
from django.core.exceptions import ValidationError
from apps.events.models import Event, TrailerInstance
from .models import QualificationCatalogEntry, IntakeFormAssignment
from .intake_contracts import DESTINATIONS, KINDS, PROTECTED, validate_contract


def option_lines(options):
    return "\n".join(item["value"] + " | " + item["label"] for item in options)


def parse_options(text):
    options = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if "|" not in line:
            raise ValidationError("Use one stable value | label per line.")
        value, label = (part.strip() for part in line.split("|", 1))
        options.append({"value": value, "label": label})
    return options


class QuestionConfigForm(forms.Form):
    label = forms.CharField(max_length=240)
    help_text = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), max_length=2000)
    type = forms.ChoiceField(choices=[(kind, kind.replace("_", " ").title()) for kind in sorted(KINDS - {"repeat_group"})])
    step = forms.TypedChoiceField(coerce=int, choices=[(n, "Step " + str(n)) for n in range(1, 5)])
    required = forms.BooleanField(required=False)
    destination = forms.ChoiceField(label="Profile field mapping")
    options = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 6}),
        help_text="One stable value | participant-facing label per line. Changing a value changes the answer contract.")
    condition_mode = forms.ChoiceField(choices=[
        ("keep", "Keep current condition"), ("always", "Always show"),
        ("equals", "Show when an earlier answer equals"), ("in", "Show for any selected earlier choice"),
        ("contains", "Show when an earlier multi-choice answer includes"),
        ("adult_non_student", "Adults 18+ who are not high school students")])
    condition_field = forms.ChoiceField(required=False, label="Earlier question")
    condition_values = forms.MultipleChoiceField(required=False, label="Triggering choices")
    max_selections = forms.IntegerField(required=False, min_value=1)
    min_selections = forms.IntegerField(required=False, min_value=0)
    exclusive = forms.CharField(required=False, help_text="Comma-separated stable values that must be selected alone.")

    def __init__(self, *args, contract, key=None, child=False, **kwargs):
        self.contract, self.key, self.child = deepcopy(contract), key, child
        questions = (next(q for q in contract["questions"] if q["key"] == "experiences")["children"]
                     if child else contract["questions"])
        self.question = next((q for q in questions if q["key"] == key), None)
        super().__init__(*args, **kwargs)
        self.fields["destination"].choices = [("submission_only", "Submission history only")] + [
            (key, key.replace("_", " ").title()) for key in DESTINATIONS]
        prior = []
        for q in questions:
            if q["key"] == key:
                break
            if q["type"] in {"single_choice", "multiple_choice"}:
                prior.append(q)
        self.fields["condition_field"].choices = [("", "Choose earlier question")] + [
            (q["key"], q["label"]) for q in prior]
        choices = {}
        for q in prior:
            for item in q["options"]:
                choices[item["value"]] = item["label"]
        self.fields["condition_values"].choices = list(choices.items())
        if self.question:
            self.initial.update({k: self.question.get(k) for k in ("label", "help_text", "type", "step",
                                "required", "destination", "max_selections", "min_selections")})
            self.initial.update(options=option_lines(self.question.get("options", [])), condition_mode="keep",
                                exclusive=",".join(self.question.get("exclusive", [])))
            if self.question["type"] == "repeat_group":
                self.fields["type"].choices += [("repeat_group", "Common experience group")]
        else:
            self.initial.update(step=4, type="short_text", destination="submission_only", condition_mode="always")

    def updated_contract(self, new_key=None):
        data = self.cleaned_data
        key = self.key or new_key
        question = deepcopy(self.question) if self.question else {"key": key}
        question.update({k: data[k] for k in ("label", "type", "step", "required", "destination")})
        question["help_text"] = data["help_text"]
        question["options"] = parse_options(data["options"])
        for name in ("max_selections", "min_selections"):
            if data[name] is not None:
                question[name] = data[name]
            else:
                question.pop(name, None)
        question["exclusive"] = [v.strip() for v in data["exclusive"].split(",") if v.strip()]
        mode = data["condition_mode"]
        if mode == "always":
            question.pop("rule", None)
        elif mode == "adult_non_student":
            question["rule"] = {"op": mode}
        elif mode != "keep":
            values = data["condition_values"]
            if not data["condition_field"] or not values or (mode != "in" and len(values) != 1):
                raise ValidationError("Choose the earlier question and valid triggering choices.")
            question["rule"] = {"field": data["condition_field"], "op": mode,
                                "value": values if mode == "in" else values[0]}
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
            choices = [("", "Keep as intake answer only")] + [(str(e.stable_id), e.name)
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
