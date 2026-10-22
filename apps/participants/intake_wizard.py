"""Five participant pages over the existing pinned intake contract."""
import hashlib
import secrets
import uuid
from datetime import date
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from .intake_contracts import validate_answers, validate_personal_answers, visible
from .intake_lifecycle import verify_published
from .intake_submission import _legal, _event_extras, _staff_channel
from .models import IntakeSession

STEP_NAMES = {1: "Personal information", 2: "Education and employment",
              3: "Trade interests and experience", 4: "Career preferences"}


UI_STEP_NAMES = {1: "Personal information", 2: "Consent", 3: "Education and employment",
                 4: "Trade interests and experience", 5: "Career preferences"}


def current_ui_step(session):
    # Existing accepted sessions retain their pinned answers and document.
    if session.current_step == 1:
        return 2 if session.context.get("personal_information_saved") else 1
    return session.current_step + 1


def contract_step_for(ui_step):
    return max(1, ui_step - 1)


def browser_key(request):
    value = request.session.get("intake_browser_nonce")
    if not value:
        value = secrets.token_urlsafe(32)
        request.session["intake_browser_nonce"] = value
    return hashlib.sha256(value.encode()).hexdigest()


def session_revision(session):
    return hashlib.sha256((str(session.pk) + session.updated_at.isoformat()).encode()).hexdigest()


def session_for(request, session_id):
    session = IntakeSession.objects.select_related(
        "form_version__form", "consent_document", "event", "trailer").get(pk=session_id)
    if not secrets.compare_digest(session.browser_key, browser_key(request)):
        raise PermissionDenied
    _staff_channel(request.user, session.channel)
    return session


def _field(q):
    common = {"label": q["label"], "required": False, "help_text": q.get("help_text", "")}
    if q["type"] in {"single_choice", "multiple_choice"}:
        choices = [(v["value"], v["label"]) for v in q["options"]]
        if q["type"] == "multiple_choice":
            return forms.MultipleChoiceField(choices=choices, widget=forms.CheckboxSelectMultiple, **common)
        return forms.ChoiceField(choices=[("", "Choose an option")] + choices, **common)
    if q["type"] == "consent":
        return forms.BooleanField(**common)
    if q["type"] == "date":
        return forms.CharField(widget=forms.DateInput(attrs={"type": "date"}), **common)
    if q["type"] == "email":
        return forms.CharField(widget=forms.EmailInput, **common)
    if q["type"] == "phone":
        return forms.CharField(widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}), **common)
    if q["type"] == "long_text":
        return forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), **common)
    return forms.CharField(max_length=q.get("max_length", 500), **common)


class IntakeStepForm(forms.Form):
    def __init__(self, *args, contract, step, answers=None, event=None, context=None, consent_only=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.contract, self.step = contract, step
        self.questions, self.rows = [], []
        self.answers = answers or {}
        self.experience_question = None
        for q in contract["questions"]:
            if q["step"] != step:
                continue
            if consent_only is not None and (q["type"] == "consent") != consent_only:
                continue
            if q["type"] == "repeat_group":
                self.experience_question = q
                continue
            self.fields[q["key"]] = _field(q)
            self.initial[q["key"]] = self.answers.get(q["key"])
            self.questions.append(q)
        if self.experience_question:
            ids = self.data.getlist("experience_ids") if self.is_bound else [
                row["entry_id"] for row in self.answers.get("experiences") or []]
            if not ids:
                ids = [str(uuid.uuid4())]
            for entry_id in ids:
                try:
                    entry_id = str(uuid.UUID(entry_id))
                except (ValueError, TypeError) as exc:
                    raise ValidationError("Invalid experience entry identifier.") from exc
                initial = next((row for row in self.answers.get("experiences") or []
                                if row["entry_id"] == entry_id), {})
                children = []
                for q in self.experience_question["children"]:
                    name = "experience_" + entry_id + "_" + q["key"]
                    self.fields[name] = _field(q)
                    self.initial[name] = initial.get(q["key"])
                    children.append({"definition": q, "field_name": name})
                self.rows.append({"entry_id": entry_id, "children": children})
        self.event_fields = []
        if step == 4 and event is not None:
            groups = [(str(g.pk), g.name) for g in event.groups.filter(is_active=True).order_by("name")]
            if event.group_mode != "disabled":
                self.fields["event_group"] = forms.ChoiceField(
                    choices=[("", "Choose a group")] + groups, required=event.group_mode == "required",
                    label=event.group_label or "Event group")
            self.fields["ticket_email"] = forms.BooleanField(required=False, label="Email my event ticket")
            self.fields["ticket_sms"] = forms.BooleanField(required=False, label="Text my event ticket")
            self.event_fields = (["event_group"] if "event_group" in self.fields else []) + ["ticket_email", "ticket_sms"]
            for q in (context or {}).get("event_questions", []):
                name = "event_answer_" + q["key"]
                definition = dict(q, type=q["type"], required=q["required"])
                if q["type"] == "yes_no":
                    definition.update(type="single_choice", options=[
                        {"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}])
                definition["options"] = [v if isinstance(v, dict) else {"value": str(v), "label": str(v)}
                                         for v in definition.get("options", [])]
                self.fields[name] = _field(definition)
                self.fields[name].required = q["required"]
                self.event_fields.append(name)

    def payload(self):
        answer = {q["key"]: self.cleaned_data.get(q["key"]) for q in self.questions}
        if self.experience_question:
            rows = []
            for item in self.rows:
                row = {"entry_id": item["entry_id"]}
                row.update({c["definition"]["key"]: self.cleaned_data.get(c["field_name"])
                            for c in item["children"]})
                rows.append(row)
            answer["experiences"] = rows if answer.get("hands_on") == "yes" else None
        return answer

    def extras(self):
        return {"group_id": self.cleaned_data.get("event_group") or None,
                "ticket_email": self.cleaned_data.get("ticket_email", False),
                "ticket_sms": self.cleaned_data.get("ticket_sms", False),
                "answers": {name.removeprefix("event_answer_"): self.cleaned_data[name]
                            for name in self.event_fields if name.startswith("event_answer_")}}

    def display_questions(self):
        return [dict(q, bound=self[q["key"]]) for q in self.questions]

    def display_rows(self):
        return [{"entry_id": row["entry_id"],
                 "children": [dict(c["definition"], bound=self[c["field_name"]]) for c in row["children"]]}
                for row in self.rows]


@transaction.atomic
def save_step(*, session, step, values, expected_revision, extras=None):
    session = IntakeSession.objects.select_for_update(of=("self",)).select_related(
        "form_version__form", "consent_document", "event").get(pk=session.pk)
    if session.state != "open" or session_revision(session) != expected_revision:
        raise ValidationError("This intake changed. Reload before continuing.")
    if step not in STEP_NAMES or step > session.current_step:
        raise ValidationError("Complete the intake steps in order.")
    version = verify_published(session.form_version, allow_retired=True)
    if session.contract_hash != version.content_hash:
        raise ValidationError("The pinned intake contract does not match.")
    occurred_on = timezone.localtime(timezone.now(), ZoneInfo(session.context["timezone"])).date()
    earlier = {q["key"]: session.answers.get(q["key"]) for q in version.snapshot["questions"]
               if q["step"] < step}
    combined = dict(earlier, **values)
    result = validate_answers(version.snapshot, combined, occurred_on=occurred_on, through_step=step)
    if step == 1:
        _legal(session.consent_document, expected_hash=session.consent_hash,
               expected_text_hash=session.context["consent_text_hash"])
        session.consent_accepted_at = timezone.now()
    if step == 4 and session.event_id:
        session.context = dict(session.context, extras=extras or {})
        _event_extras(session)
    session.answers = result["answers"]
    session.current_step = min(step + 1, 4)
    session.context = dict(session.context, completed_through=step)
    session.save(update_fields=["answers", "current_step", "context", "consent_accepted_at", "updated_at"])
    return session


@transaction.atomic
def save_personal_information(*, session, values, expected_revision):
    """Save a private draft, reset later answers, and require the separate consent page."""
    session = IntakeSession.objects.select_for_update(of=("self",)).select_related(
        "form_version__form").get(pk=session.pk)
    if session.state != "open" or session_revision(session) != expected_revision:
        raise ValidationError("This intake changed. Reload before continuing.")
    version = verify_published(session.form_version, allow_retired=True)
    if session.contract_hash != version.content_hash:
        raise ValidationError("The pinned intake contract does not match.")
    allowed = {q["key"] for q in version.snapshot["questions"] if q["step"] == 1 and q["type"] != "consent"}
    if set(values) - allowed:
        raise ValidationError("Only personal information may be saved on this page.")
    occurred_on = timezone.localtime(timezone.now(), ZoneInfo(session.context["timezone"])).date()
    result = validate_personal_answers(version.snapshot, values, occurred_on=occurred_on)
    session.answers = result["answers"]
    session.current_step = 1
    session.consent_accepted_at = None
    session.context = dict(session.context, completed_through=0, personal_information_saved=True)
    session.context.pop("extras", None)
    session.save(update_fields=["answers", "current_step", "context", "consent_accepted_at", "updated_at"])
    return session
