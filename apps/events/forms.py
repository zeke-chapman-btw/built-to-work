from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.core.exceptions import ValidationError

from apps.participants.models import Participant
from .models import Event, EventRegistration

DATETIME_FORMAT = "%Y-%m-%dT%H:%M"


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = (
            "name", "code", "description", "start_at", "end_at", "timezone_name",
            "location_name", "address_line_1", "address_line_2", "city", "state",
            "postal_code", "country", "status", "allow_station_auto_check_in",
        )
        widgets = {
            "start_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_FORMAT),
            "end_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_FORMAT),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            zone = ZoneInfo(self.instance.timezone_name)
            self.initial["start_at"] = self.instance.local_start_at.strftime(DATETIME_FORMAT)
            self.initial["end_at"] = self.instance.local_end_at.strftime(DATETIME_FORMAT)

    def clean(self):
        cleaned = super().clean()
        zone_name = cleaned.get("timezone_name") or "UTC"
        try:
            zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, TypeError):
            self.add_error("timezone_name", "Enter a valid IANA timezone, such as America/Chicago.")
            return cleaned
        if self.is_bound:
            for field_name in ("start_at", "end_at"):
                raw = self.data.get(self.add_prefix(field_name))
                if raw:
                    try:
                        local_value = datetime.strptime(raw, DATETIME_FORMAT)
                        cleaned[field_name] = local_value.replace(tzinfo=zone)
                    except ValueError:
                        pass
        start, end = cleaned.get("start_at"), cleaned.get("end_at")
        if start and end and end < start:
            self.add_error("end_at", "Event end must be at or after event start.")
        return cleaned

    def save(self, commit=True):
        event = super().save(commit=False)
        event.full_clean()
        if commit:
            event.save()
            self.save_m2m()
        return event


class RegistrationForm(forms.Form):
    participant = forms.ModelChoiceField(
        queryset=Participant.objects.none(), required=False,
        help_text="Choose an existing participant, or leave blank to find/create by the details below.",
    )
    first_name = forms.CharField(max_length=100, required=False)
    last_name = forms.CharField(max_length=100, required=False)
    contact_email = forms.EmailField(required=False)
    contact_phone = forms.CharField(max_length=40, required=False)
    source = forms.ChoiceField(choices=(
        (EventRegistration.Source.STAFF, "Staff registration"),
        (EventRegistration.Source.WALK_IN, "Walk-in"),
    ), initial=EventRegistration.Source.STAFF)
    check_in_now = forms.BooleanField(required=False, label="Check in now")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["participant"].queryset = Participant.objects.filter(archived_at__isnull=True, kind=Participant.Kind.PERSON).order_by("last_name", "first_name")
        self.fields["participant"].label_from_instance = lambda person: f"{person.last_name}, {person.first_name} — {person.contact_email or person.contact_phone or 'no contact'}"

    def clean(self):
        cleaned = super().clean()
        participant = cleaned.get("participant")
        if not participant and not (cleaned.get("first_name") and cleaned.get("last_name")):
            raise ValidationError("Select an existing participant or enter a first and last name.")
        return cleaned


class ReissueTicketForm(forms.Form):
    expected_ticket_id = forms.UUIDField(widget=forms.HiddenInput)
    reason = forms.CharField(max_length=240, required=False, widget=forms.TextInput(attrs={"placeholder": "Reason for replacement (optional)"}))


class VoidRegistrationForm(forms.Form):
    reason = forms.CharField(max_length=240, widget=forms.TextInput(attrs={"required": True, "placeholder": "Reason for voiding"}))
