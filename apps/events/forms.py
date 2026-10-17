from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.core.exceptions import ValidationError

from apps.participants.models import Participant
from .models import Event, EventGroup, EventRegistration

DATETIME_FORMAT = "%Y-%m-%dT%H:%M"


class EventForm(forms.ModelForm):
    start_at = forms.CharField(label="Event start (local time)", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    end_at = forms.CharField(label="Event end (local time)", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    registration_opens_at = forms.CharField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    registration_closes_at = forms.CharField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    registration_deadline_at = forms.CharField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    revision = forms.CharField(required=False, widget=forms.HiddenInput)

    FIELD_GROUPS = (
        ("Basic information", ("name", "code", "event_type", "event_type_other", "description")),
        ("Location", ("location_name", "address_line_1", "address_line_2", "city", "state", "postal_code", "country")),
        ("Schedule", ("timezone_name", "setup_date", "staff_arrival_time", "start_at", "end_at", "daily_start_time", "daily_end_time", "return_date")),
        ("Contact and logistics", ("contact_name", "contact_email", "contact_phone", "housing_information", "logistics_instructions")),
        ("Participant registration settings", ("registration_opens_at", "registration_closes_at", "registration_deadline_at", "registration_capacity", "group_mode", "group_label")),
    )

    class Meta:
        model = Event
        fields = tuple(name for title, names in (
            ("basic", ("name", "code", "event_type", "event_type_other", "description")),
            ("location", ("location_name", "address_line_1", "address_line_2", "city", "state", "postal_code", "country")),
            ("schedule", ("timezone_name", "setup_date", "staff_arrival_time", "start_at", "end_at", "daily_start_time", "daily_end_time", "return_date")),
            ("logistics", ("contact_name", "contact_email", "contact_phone", "housing_information", "logistics_instructions")),
            ("registration", ("registration_opens_at", "registration_closes_at", "registration_deadline_at", "registration_capacity", "group_mode", "group_label")),
        ) for name in names)
        widgets = {
            **{name: forms.DateInput(attrs={"type": "date"}) for name in ("setup_date", "return_date")},
            **{name: forms.TimeInput(attrs={"type": "time"}) for name in ("staff_arrival_time", "daily_start_time", "daily_end_time")},
            **{name: forms.Textarea(attrs={"rows": 3}) for name in ("description", "housing_information", "logistics_instructions")},
        }
        labels = {"location_name": "Venue", "return_date": "Arrival home date", "event_type_other": "Other event type", "timezone_name": "Event timezone"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timezone_name"].help_text = "Use an IANA timezone, for example America/New_York. All date/time entries below use this timezone."
        self.fields["start_at"].help_text = "The event start and end define the existing QR/kiosk operating window. Setup and travel dates do not extend it."
        self.fields["daily_start_time"].help_text = "Optional daily planning hours; enter both times. These do not change the QR/kiosk window."
        self.fields["event_type_other"].help_text = "Optional description when the type is Other."
        if not self.instance._state.adding:
            self.initial["revision"] = self.instance.updated_at.isoformat()
            zone = ZoneInfo(self.instance.timezone_name)
            for name in ("start_at", "end_at", "registration_opens_at", "registration_closes_at", "registration_deadline_at"):
                value = getattr(self.instance, name)
                self.initial[name] = value.astimezone(zone).strftime(DATETIME_FORMAT) if value else ""

    @property
    def grouped_fields(self):
        return [(title, [self[name] for name in names]) for title, names in self.FIELD_GROUPS]

    def clean(self):
        cleaned = super().clean()
        try:
            zone = ZoneInfo(cleaned.get("timezone_name") or "UTC")
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            self.add_error("timezone_name", "Enter a valid IANA timezone, such as America/New_York.")
            for name in ("start_at", "end_at", "registration_opens_at", "registration_closes_at", "registration_deadline_at"):
                cleaned.pop(name, None)
            return cleaned
        from datetime import UTC
        for name in ("start_at", "end_at", "registration_opens_at", "registration_closes_at", "registration_deadline_at"):
            raw = cleaned.get(name)
            if not raw:
                cleaned[name] = None
                continue
            try:
                value = datetime.fromisoformat(raw)
                if value.tzinfo is None:
                    candidates = [value.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
                    valid = [v for v in candidates if v.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value]
                    if not valid or len({v.utcoffset() for v in valid}) > 1:
                        raise ValueError
                    value = valid[0]
                cleaned[name] = value
            except (ValueError, TypeError):
                self.add_error(name, "Enter an unambiguous local date and time. This time may fall in a daylight-saving clock change.")
        for name in ("start_at", "end_at"):
            if isinstance(cleaned.get(name), str):
                cleaned.pop(name, None)
        start, end = cleaned.get("start_at"), cleaned.get("end_at")
        if start and end and end < start:
            self.add_error("end_at", "Event end must be at or after event start.")
        opening, closing = cleaned.get("registration_opens_at"), cleaned.get("registration_closes_at")
        if opening and closing and closing < opening:
            self.add_error("registration_closes_at", "Registration closing must be at or after opening.")
        return cleaned


class EventFilterForm(forms.Form):
    view = forms.ChoiceField(choices=(("upcoming", "Upcoming"), ("current", "Current"), ("completed", "Completed"), ("draft", "Draft"), ("cancelled", "Cancelled"), ("archived", "Archived"), ("all", "All events")))
    q = forms.CharField(required=False, max_length=200, label="Search events")
    event_type = forms.ChoiceField(required=False, choices=(("", "All event types"), *Event.EventType.choices))
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="From date (UTC)")
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Through date (UTC)")

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to") and data["date_to"] < data["date_from"]:
            self.add_error("date_to", "The end of the date range must follow its start.")
        return data


class EventActionForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    reason = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 2}))
    confirm = forms.BooleanField(label="I confirm this event action.")

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
    group = forms.ModelChoiceField(queryset=EventGroup.objects.none(), required=False)
    override_reason = forms.CharField(max_length=240, required=False, help_text="Required for a deadline or capacity exception.")

    def __init__(self, *args, **kwargs):
        event = kwargs.pop("event", None)
        super().__init__(*args, **kwargs)
        self.fields["participant"].queryset = Participant.objects.filter(archived_at__isnull=True, kind=Participant.Kind.PERSON).order_by("last_name", "first_name")
        if event is not None and event.group_mode != "disabled":
            self.fields["group"].queryset = event.groups.filter(is_active=True).order_by("name")
            self.fields["group"].label = event.group_label
        else:
            self.fields.pop("group")
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
