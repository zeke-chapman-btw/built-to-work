from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import OnboardingQuestion

MAX_W9 = 10 * 1024 * 1024


class CompanyForm(forms.Form):
    name = forms.CharField(label="Company name", max_length=200)
    legal_name = forms.CharField(label="Legal business name", max_length=240, required=False,
                                 help_text="If different from the name above (as it appears on your W-9).")
    industry = forms.CharField(max_length=160, required=False)
    website = forms.URLField(required=False, assume_scheme="https")
    phone = forms.CharField(label="Main phone", max_length=60, required=False)
    business_email = forms.EmailField(label="Main company email", required=False)
    address_line1 = forms.CharField(label="Street address", max_length=240, required=False)
    address_line2 = forms.CharField(label="Suite / unit", max_length=240, required=False)
    city = forms.CharField(max_length=120, required=False)
    state = forms.CharField(max_length=120, required=False)
    postal_code = forms.CharField(label="ZIP code", max_length=30, required=False)
    employees = forms.IntegerField(label="About how many employees do you have?", min_value=1, required=False)


PERSON = (("first_name", "First name", 100), ("last_name", "Last name", 100), ("title", "Job title", 160),
          ("email", "Email", 254), ("phone", "Phone", 60))
OTHERS = 3


class ContactsForm(forms.Form):
    """Main contact (required), billing contact, and up to three others."""
    billing_same = forms.BooleanField(label="The main contact also handles billing", required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for prefix, required in [("main", True), ("billing", False)] + [(f"other{i}", False) for i in range(1, OTHERS + 1)]:
            for key, label, size in PERSON:
                field_class = forms.EmailField if key == "email" else forms.CharField
                kwargs = {"label": label, "required": required and key in ("first_name", "last_name", "email")}
                if field_class is forms.CharField:
                    kwargs["max_length"] = size
                self.fields[f"{prefix}_{key}"] = field_class(**kwargs)
            if prefix.startswith("other"):
                self.fields[f"{prefix}_portal"] = forms.BooleanField(label="Give this person access to the customer portal", required=False)
        self.fields["main_portal"] = forms.BooleanField(label="Give this person access to the customer portal", required=False, initial=True)

    def groups(self):
        def people(prefix):
            return [self[f"{prefix}_{key}"] for key, _l, _s in PERSON] + ([self[f"{prefix}_portal"]] if f"{prefix}_portal" in self.fields else [])
        result = [("Main contact", people("main"), "Our main point of contact. Required.")]
        result.append(("Billing contact", [self["billing_same"]] + people("billing"), "Who should get bank setup and billing emails?"))
        for i in range(1, OTHERS + 1):
            result.append((f"Other contact {i}", people(f"other{i}"), "Optional."))
        return result

    def clean(self):
        data = super().clean()
        if not data.get("billing_same") and not data.get("billing_email"):
            self.add_error("billing_email", "Add the billing contact's email, or tick the box above.")
        for i in range(1, OTHERS + 1):
            p = f"other{i}"
            if any(data.get(f"{p}_{k}") for k, _l, _s in PERSON) and not (data.get(f"{p}_first_name") and data.get(f"{p}_email")):
                self.add_error(f"{p}_email", "Add at least a first name and email, or leave this person blank.")
        return data

    @staticmethod
    def to_data(data):
        def person(prefix):
            row = {k: (data.get(f"{prefix}_{k}") or "").strip() for k, _l, _s in PERSON}
            if f"{prefix}_portal" in data:
                row["portal"] = bool(data.get(f"{prefix}_portal"))
            return row
        result = {"main": person("main"), "billing_same": bool(data.get("billing_same")), "others": []}
        result["billing"] = {} if result["billing_same"] else person("billing")
        for i in range(1, OTHERS + 1):
            row = person(f"other{i}")
            if row.get("first_name") or row.get("email"):
                result["others"].append(row)
        return result

    @staticmethod
    def initial_from(saved):
        initial = {"billing_same": saved.get("billing_same", True)}
        for prefix, row in [("main", saved.get("main") or {}), ("billing", saved.get("billing") or {})] + \
                [(f"other{i + 1}", r) for i, r in enumerate(saved.get("others") or [])]:
            for key, value in row.items():
                initial[f"{prefix}_{key}"] = value
        if "main" not in saved:
            initial["main_portal"] = True
        return initial


class HiringForm(forms.Form):
    def __init__(self, *args, questions=None, **kwargs):
        super().__init__(*args, **kwargs)
        order = {key: i for i, (key, _label) in enumerate(OnboardingQuestion.Section.choices)}
        found = questions if questions is not None else OnboardingQuestion.objects.filter(is_active=True)
        self.questions = sorted(found, key=lambda q: (order.get(q.section, 99), q.position, q.pk))
        for q in self.questions:
            key = f"q{q.pk}"
            common = {"label": q.label, "required": q.required, "help_text": q.help_text}
            if q.kind == "short":
                field = forms.CharField(max_length=500, **common)
            elif q.kind == "long":
                field = forms.CharField(max_length=4000, widget=forms.Textarea(attrs={"rows": 3}), **common)
            elif q.kind == "choice":
                field = forms.ChoiceField(choices=[(o, o) for o in q.option_list()], widget=forms.RadioSelect, **common)
            elif q.kind == "multi":
                field = forms.MultipleChoiceField(choices=[(o, o) for o in q.option_list()], widget=forms.CheckboxSelectMultiple, **common)
            else:
                field = forms.ChoiceField(choices=[("Yes", "Yes"), ("No", "No")], widget=forms.RadioSelect, **common)
            self.fields[key] = field

    def sections(self):
        labels = dict(OnboardingQuestion.Section.choices)
        result = []
        for q in self.questions:
            if not result or result[-1][0] != labels.get(q.section, q.section):
                result.append((labels.get(q.section, q.section), []))
            result[-1][1].append(self[f"q{q.pk}"])
        return result

    def to_data(self):
        return {f"q{q.pk}": {"label": q.label, "section": q.section, "value": self.cleaned_data.get(f"q{q.pk}")}
                for q in self.questions}


class DocumentsForm(forms.Form):
    w9 = forms.FileField(label="W-9 (PDF or photo)", required=False,
                         help_text="PDF, JPG or PNG, up to 10 MB. You can skip this and send it later.")
    agree = forms.BooleanField(label="I have read the agreement above and agree to it on behalf of the company.", required=False)
    signed_name = forms.CharField(label="Your full name (this is your signature)", max_length=160, required=False)
    signed_title = forms.CharField(label="Your title", max_length=160, required=False)

    def __init__(self, *args, require_signature=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_signature = require_signature

    def clean_w9(self):
        upload = self.cleaned_data.get("w9")
        if upload:
            if upload.size > MAX_W9:
                raise forms.ValidationError("That file is over 10 MB.")
            if not upload.name.lower().endswith((".pdf", ".png", ".jpg", ".jpeg")):
                raise forms.ValidationError("Upload a PDF, JPG or PNG.")
        return upload

    def clean(self):
        data = super().clean()
        signing = data.get("agree") or data.get("signed_name")
        if self.require_signature or signing:
            if not data.get("agree"):
                self.add_error("agree", "Tick the box to agree.")
            if not (data.get("signed_name") or "").strip():
                self.add_error("signed_name", "Type your full name to sign.")
        return data


class SetupForm(forms.Form):
    """Staff only: access, account manager and the monthly agreement."""
    lead_limit = forms.IntegerField(label="Portal lead seats", min_value=0, initial=1, help_text="People who manage the customer's portal.")
    member_limit = forms.IntegerField(label="Portal member seats", min_value=0, initial=3)
    workforce_need_creation = forms.BooleanField(label="Customer can post positions in the portal", required=False, initial=True)
    account_manager = forms.ChoiceField(label="BTW account manager", required=False)
    monthly_amount = forms.DecimalField(label="Monthly fee ($)", min_value=0, max_digits=9, decimal_places=2, required=False,
                                        help_text="Leave blank for no monthly agreement yet.")
    starts_on = forms.DateField(label="Monthly fee starts", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    ends_on = forms.DateField(label="Agreement ends", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    send_bank_link = forms.BooleanField(label="Email the bank setup link when the customer is created", required=False, initial=True)
    notes = forms.CharField(label="Internal notes", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        users = get_user_model().objects.filter(is_active=True, is_staff=True).order_by("first_name", "last_name")
        self.fields["account_manager"].choices = [("", "—")] + [(str(u.pk), u.get_full_name() or u.get_username()) for u in users]

    def clean(self):
        data = super().clean()
        if data.get("monthly_amount"):
            if not (data.get("starts_on") and data.get("ends_on")):
                self.add_error("ends_on", "A monthly fee needs a start and end date.")
            elif data["ends_on"] <= data["starts_on"]:
                self.add_error("ends_on", "The end date has to be after the start date.")
            elif data["ends_on"] < timezone.localdate():
                self.add_error("ends_on", "The end date is already past.")
        return data

    @staticmethod
    def to_data(data):
        result = {}
        for key, value in data.items():
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            elif value is not None and not isinstance(value, (bool, int, str)):
                value = str(value)
            result[key] = value
        return result
