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
