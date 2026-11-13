"""Small, explicit manual-entry forms; no ownership or provider fields."""
from apps.core.optional_reason import OptionalReasonMixin
import uuid

from django import forms
from django.utils import timezone

from apps.customers.models import Campaign, CustomerContact
from apps.recruiting.models import WorkforceRequest
from apps.operations.forms import TaskForm
from apps.operations.task_access import eligible_assignees
from apps.recruiting.selection import opportunities_for

from .models import ActivityType, ContactRelationship, Interaction


class ActivityForm(OptionalReasonMixin, forms.Form):
    return_to = forms.CharField(required=False, max_length=16384, widget=forms.HiddenInput)
    operation_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    revision = forms.IntegerField(widget=forms.HiddenInput, required=False, min_value=1)
    activity_type = forms.ChoiceField(label="Interaction", choices=ActivityType.choices)
    occurred_at = forms.DateTimeField(label="When", input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    summary = forms.CharField(max_length=240)
    outcome = forms.CharField(max_length=240, required=False)
    direction = forms.ChoiceField(choices=Interaction._meta.get_field("direction").choices, required=False)
    notes = forms.CharField(label="Details (optional)", max_length=6000, required=False,
        widget=forms.Textarea(attrs={"rows": 3}))
    next_step = forms.CharField(max_length=500, required=False)
    follow_up_on = forms.DateField(label="Follow-up date (optional)", required=False,
        widget=forms.DateInput(attrs={"type": "date"}))
    responsible_staff = forms.ModelChoiceField(queryset=eligible_assignees(), required=False)
    engagement = forms.ModelChoiceField(queryset=None, label="Recruiting engagement (optional)", required=False)
    contact = forms.ModelChoiceField(queryset=CustomerContact.objects.none(), required=False)
    campaign = forms.ModelChoiceField(queryset=Campaign.objects.none(), required=False)
    workforce_request = forms.ModelChoiceField(queryset=WorkforceRequest.objects.none(), required=False)
    reason = forms.CharField(label="Reason for correction", max_length=500, required=False,
        widget=forms.Textarea(attrs={"rows": 2}))

    # The handful of types staff actually pick; older types still show when editing an entry that used one.
    QUICK_TYPES = ("call", "voicemail", "sms", "email", "meeting", "video", "event", "other")

    def __init__(self, *args, actor=None, organization=None, row=None, employer=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("occurred_at", timezone.now().replace(second=0, microsecond=0))
        short = {"call": "Call", "voicemail": "Voicemail", "sms": "Text", "email": "Email", "meeting": "In person",
                 "video": "Video call", "event": "At an event", "other": "Other"}
        self.fields["activity_type"].label = "Type"
        if not employer:
            # Staff see the short list as chips; every existing type is still accepted (older entries, other screens).
            keep = set(self.QUICK_TYPES) | ({row.activity_type} if row else set())
            shown = [(value, short.get(value, label)) for value, label in ActivityType.choices if value in keep]
            self.fields["activity_type"].initial = "call"
            self.fields["activity_type"].widget = forms.RadioSelect(attrs={"class": "btw-chip-choices"}, choices=shown)
        self.fields["summary"].label = "What happened"
        self.fields["summary"].widget.attrs["placeholder"] = "Talked about the welding opening, sending resume Friday"
        self.fields["notes"].label = "More details"
        self.fields["notes"].widget.attrs["rows"] = 2
        self.fields["outcome"].label = "Result"
        self.fields["outcome"].widget.attrs["placeholder"] = "Interested, not now, left message..."
        direction = [("outbound", "We reached out"), ("inbound", "They reached out")]
        self.fields["direction"].label = "Who reached out"
        self.fields["direction"].choices = [("", "Not set")] + direction
        self.fields["direction"].widget = forms.RadioSelect(attrs={"class": "btw-chip-choices"}, choices=direction)
        self.fields["next_step"].label = "Next step"
        self.fields["follow_up_on"].label = "Follow up on"
        self.fields["responsible_staff"].label = "Who follows up"
        for key, label in (("engagement", "Job opening"), ("contact", "Customer contact"), ("campaign", "Campaign"), ("workforce_request", "Request")):
            if key in self.fields:
                self.fields[key].label = label
        if row:
            for key in ("activity_type", "occurred_at", "summary", "outcome", "direction", "notes", "next_step", "follow_up_on", "responsible_staff"):
                self.initial[key] = getattr(row, key)
            self.initial["revision"] = row.revision
            self.fields["reason"].required = True
        else:
            self.fields.pop("revision")
            self.fields.pop("reason")
        if employer or row:
            self.fields.pop("engagement")
            self.fields.pop("contact")
            self.fields.pop("campaign")
            self.fields.pop("workforce_request")
        else:
            try:
                query = opportunities_for(actor).filter(archived_at__isnull=True)
                if organization:
                    query = query.filter(need__organization=organization)
                self.fields["engagement"].queryset = query
                self.fields["engagement"].label_from_instance = lambda obj: str(obj.need)
            except Exception as exc:
                from django.core.exceptions import PermissionDenied
                if not isinstance(exc, PermissionDenied):
                    raise
                self.fields.pop("engagement")
            if organization:
                self.fields["contact"].queryset = organization.contacts.all()
                self.fields["contact"].label_from_instance = lambda obj: str(obj) + (" (inactive)" if not obj.is_active else "")
                self.fields["campaign"].queryset = organization.campaigns.all()
                self.fields["workforce_request"].queryset = WorkforceRequest.objects.filter(organization=organization, archived_at__isnull=True)
                self.fields["workforce_request"].label_from_instance = lambda obj: obj.name
            else:
                self.fields.pop("contact")
                self.fields.pop("campaign")
                self.fields.pop("workforce_request")
        if employer:
            self.fields.pop("responsible_staff")
        elif row and row.responsible_staff_id:
            from apps.staffing.models import StaffProfile
            from django.db.models import Q
            self.fields["responsible_staff"].queryset = StaffProfile.objects.filter(
                Q(pk__in=eligible_assignees().values("pk")) | Q(pk=row.responsible_staff_id))

    def clean_occurred_at(self):
        value = self.cleaned_data["occurred_at"]
        if value > timezone.now():
            raise forms.ValidationError("Use the actual time of an interaction that has already occurred.")
        return value

    def interaction_data(self):
        from .services import EDITABLE
        return {key: self.cleaned_data[key] for key in EDITABLE if key in self.cleaned_data}

    @property
    def core_fields(self):
        return [self[key] for key in ("activity_type", "summary", "occurred_at", "direction", "reason") if key in self.fields]

    @property
    def detail_fields(self):
        return [self[key] for key in ("outcome", "notes") if key in self.fields]

    @property
    def link_fields(self):
        return [self[key] for key in ("contact", "engagement", "campaign", "workforce_request") if key in self.fields]

    @property
    def optional_fields(self):
        return [field for field in self.visible_fields() if field.name not in {"activity_type", "occurred_at", "summary", "outcome", "reason"}]

    @property
    def next_fields(self):
        return [self[key] for key in ("next_step", "follow_up_on", "responsible_staff") if key in self.fields]

    @property
    def supporting_fields(self):
        return [field for field in self.optional_fields if field.name not in {"next_step", "follow_up_on", "responsible_staff"}]


class ArchiveForm(OptionalReasonMixin, forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=1)
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows": 2}))


class FollowupForm(TaskForm):
    operation_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)


class LinkTaskForm(forms.Form):
    task = forms.ModelChoiceField(queryset=None, label="Existing task")

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.operations.task_access import tasks_for
        self.fields["task"].queryset = tasks_for(actor).filter(archived_at__isnull=True).order_by("title")


class ContactForm(OptionalReasonMixin, forms.Form):
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=0)
    role = forms.CharField(label="Relationship role", max_length=160, required=False)
    is_primary = forms.BooleanField(label="Primary business contact", required=False)
    email_preference = forms.ChoiceField(choices=ContactRelationship.PREFERENCES)
    sms_preference = forms.ChoiceField(choices=ContactRelationship.PREFERENCES)
    phone_preference = forms.ChoiceField(choices=ContactRelationship.PREFERENCES)
    preference_notes = forms.CharField(label="Preference source / notes", max_length=1000, required=False,
        widget=forms.Textarea(attrs={"rows": 3}))
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows": 2}))
