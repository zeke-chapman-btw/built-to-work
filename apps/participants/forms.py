from django import forms
from django.contrib.auth.password_validation import validate_password
from .models import Participant, ParticipantAccountRequest, ParticipantEmailChange
from .normalization import normalize_email, normalize_phone

class AccountRequestForm(forms.ModelForm):
    class Meta:
        model = ParticipantAccountRequest
        fields = ("first_name", "last_name", "preferred_name", "contact_email", "contact_phone", "login_email")
    def clean(self):
        data = super().clean()
        for field in ("contact_email", "login_email"):
            if data.get(field): data[field] = normalize_email(data[field])
        data["contact_phone"] = normalize_phone(data.get("contact_phone"))
        return data

class ParticipantProfileForm(forms.ModelForm):
    class Meta:
        model = Participant
        fields = ("first_name", "last_name", "preferred_name", "contact_email", "contact_phone")
    def clean_contact_email(self): return normalize_email(self.cleaned_data["contact_email"])
    def clean_contact_phone(self): return normalize_phone(self.cleaned_data["contact_phone"])

class ParticipantLoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)
    def clean_email(self): return normalize_email(self.cleaned_data["email"])

class SetPasswordForm(forms.Form):
    password1 = forms.CharField(widget=forms.PasswordInput)
    password2 = forms.CharField(widget=forms.PasswordInput)
    def clean(self):
        data = super().clean()
        if data.get("password1") and data.get("password2") and data["password1"] != data["password2"]:
            self.add_error("password2", "The passwords do not match.")
        if data.get("password1"):
            validate_password(data["password1"])
        return data

class EmailChangeForm(forms.ModelForm):
    class Meta:
        model = ParticipantEmailChange
        fields = ("new_email",)
    def clean_new_email(self): return normalize_email(self.cleaned_data["new_email"])

class StaffRequestResolutionForm(forms.Form):
    participant = forms.ModelChoiceField(queryset=Participant.objects.none())
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["participant"].queryset = Participant.objects.order_by("last_name", "first_name")
from datetime import date
from django import forms
from django.core.exceptions import ValidationError
from .models import RegistrationQuestion

LIFE_STAGE_CHOICES = [
    ("middle_school", "Middle school student"), ("high_school", "High school student"),
    ("trade_school", "Trade school student"), ("college", "College student"),
    ("working", "Currently working / in the workforce"), ("seeking_work", "Looking for work"),
    ("retired", "Retired"), ("other", "Other / none of these"),
]
TRAVEL_DISTANCE_CHOICES = [("25", "Up to 25 miles"), ("50", "Up to 50 miles"), ("100", "Up to 100 miles"), ("statewide", "Statewide"), ("nationwide", "Nationwide")]

class RegistrationIntakeForm(forms.Form):
    first_name = forms.CharField(max_length=100, label="First name")
    last_name = forms.CharField(max_length=100, label="Last name")
    preferred_name = forms.CharField(max_length=100, required=False, label="Preferred name")
    contact_email = forms.EmailField(required=False, label="Email")
    contact_phone = forms.CharField(max_length=32, label="Phone")
    date_of_birth = forms.DateField(input_formats=["%Y-%m-%d", "%m/%d/%Y"], widget=forms.DateInput(attrs={"type":"date"}), label="Date of birth")
    address_line_1 = forms.CharField(max_length=200, required=False, label="Mailing address")
    address_line_2 = forms.CharField(max_length=200, required=False, label="Address line 2")
    city = forms.CharField(max_length=100, required=False)
    state = forms.CharField(max_length=100, required=False)
    postal_code = forms.CharField(max_length=24, required=False, label="ZIP / postal code")
    life_stage = forms.ChoiceField(choices=LIFE_STAGE_CHOICES, label="Which best describes you currently?")
    employment_status = forms.CharField(max_length=100, required=False, label="Employment status")
    current_industry = forms.CharField(max_length=160, required=False, label="Current or previous industry")
    industry_other = forms.CharField(max_length=160, required=False, label="Tell us about your industry")
    career_interests = forms.CharField(widget=forms.Textarea, required=False, label="Career and industry interests")
    construction_experience = forms.ChoiceField(choices=[("yes","Yes"),("no","No")], required=False, label="Do you have construction experience?")
    years_experience = forms.IntegerField(required=False, min_value=0, max_value=99, label="Years of construction experience")
    certifications = forms.CharField(required=False, label="Certifications")
    willing_to_travel = forms.ChoiceField(choices=[("yes","Yes"),("no","No")], required=False, label="Are you willing to travel for work?")
    travel_distance = forms.ChoiceField(choices=TRAVEL_DISTANCE_CHOICES, required=False, label="How far would you travel?")
    consent = forms.BooleanField(required=False, label="I acknowledge the privacy and consent information shown by BTW.")
    def clean_date_of_birth(self):
        dob = self.cleaned_data["date_of_birth"]
        if dob > date.today(): raise ValidationError("Enter a valid date of birth.")
        return dob
    def clean(self):
        data = super().clean()
        dob = data.get("date_of_birth")
        if dob:
            today = date.today(); age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if age < 14: self.add_error("date_of_birth", "You must be at least 14 to continue.")
        if data.get("construction_experience") != "yes": data["years_experience"] = None
        if data.get("certifications") and not data.get("certifications").strip(): data["certifications"] = ""
        if data.get("willing_to_travel") != "yes": data["travel_distance"] = ""
        if data.get("current_industry") != "other": data["industry_other"] = ""
        return data

class RegistrationQuestionForm(forms.ModelForm):
    class Meta:
        model = RegistrationQuestion
        fields = ("label", "help_text", "field_type", "options", "visibility_rule", "is_required", "is_active", "position")
        widgets = {"options": forms.Textarea(attrs={"rows":3}), "visibility_rule": forms.Textarea(attrs={"rows":3})}
from django import forms
from datetime import date
import json

class SnapshotRegistrationForm(forms.Form):
    """Participant intake form generated exclusively from an immutable published snapshot."""
    def __init__(self, *args, questions=None, consent_document=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshot_questions = sorted(list(questions or []), key=lambda q: (q.get("position", 0), q.get("key", "")))
        self.consent_document = consent_document
        for q in self.snapshot_questions:
            key = q.get("key")
            if not key or key in self.fields:
                continue
            kind = q.get("field_type", "short_text")
            opts = q.get("options") or []
            choices = []
            if isinstance(opts, dict):
                choices = list(opts.items())
            elif isinstance(opts, list):
                choices = [(str(x.get("value", x)), str(x.get("label", x.get("value", x)))) if isinstance(x, dict) else (str(x[0]), str(x[1])) if isinstance(x, (list, tuple)) and len(x) >= 2 else (str(x), str(x)) for x in opts]
            kwargs = {"label": q.get("label") or key.replace("_", " ").title(), "required": bool(q.get("required")), "help_text": q.get("help_text") or ""}
            if kind in ("single_choice", "dropdown", "yes_no"):
                kwargs["choices"] = choices or ([('yes', 'Yes'), ('no', 'No')] if kind == "yes_no" else [('', 'Select one')])
                field = forms.ChoiceField(**kwargs)
            elif kind == "multiple_choice":
                kwargs["choices"] = choices
                field = forms.MultipleChoiceField(**kwargs)
            elif kind == "date":
                kwargs["input_formats"] = ["%Y-%m-%d", "%m/%d/%Y"]
                field = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), **kwargs)
            elif kind == "number":
                field = forms.IntegerField(**kwargs)
            elif kind == "long_text":
                field = forms.CharField(widget=forms.Textarea, **kwargs)
            else:
                field = forms.CharField(max_length=500, **kwargs)
            self.fields[key] = field
        if consent_document:
            self.fields["consent"] = forms.BooleanField(required=True, label=f"I agree to {consent_document.title}")

    def _visible(self, question, cleaned):
        rule = question.get("visibility_rule") or {}
        if isinstance(rule, str):
            try: rule = json.loads(rule or "{}")
            except (TypeError, ValueError): return False
        if not rule: return True
        key = rule.get("field") or rule.get("question") or rule.get("key")
        expected = rule.get("value", rule.get("equals"))
        if not key: return True
        actual = cleaned.get(key)
        if rule.get("not_empty") is True: return bool(actual)
        if isinstance(actual, list): return expected in actual
        return str(actual or "") == str(expected)

    def clean(self):
        data = super().clean()
        for q in self.snapshot_questions:
            key = q.get("key")
            if key and not self._visible(q, data):
                data[key] = None
                self.errors.pop(key, None)
        dob = data.get("date_of_birth")
        if dob:
            if dob > date.today(): self.add_error("date_of_birth", "Enter a valid date of birth.")
            else:
                age = date.today().year - dob.year - ((date.today().month, date.today().day) < (dob.month, dob.day))
                if age < 14: self.add_error("date_of_birth", "Participants must be at least 14 years old.")
        if data.get("construction_experience") != "yes": data["years_experience"] = None
        if data.get("willing_to_travel") != "yes": data["travel_distance"] = None
        if data.get("current_industry") != "other": data["industry_other"] = ""
        return data
