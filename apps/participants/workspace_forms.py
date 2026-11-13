from apps.core.optional_reason import OptionalReasonMixin
import uuid
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.events.models import Event
from apps.assessments.models import AssessmentCategory
from .models import Participant, ParticipantCareerProfile, RegistrationSubmission
from .qualification_models import QualificationCatalogEntry
from .workspace_models import ParticipantAcquisition, ParticipantWorkExperience, ParticipantNote
from .normalization import normalize_email, normalize_phone
from .workspace_access import FILTER_PERMISSIONS, ASSESSMENT_PERMISSIONS

DATE = forms.DateInput(attrs={"type": "date"})


class StaffProfileForm(forms.ModelForm):
    revision = forms.CharField(widget=forms.HiddenInput)
    audit_reason = forms.CharField(required=False, max_length=500, label="Change note", widget=forms.HiddenInput)
    class Meta:
        model = Participant
        fields = ("first_name", "last_name", "preferred_name", "contact_email", "contact_phone", "city", "state", "postal_code")
    def clean_contact_email(self):
        return normalize_email(self.cleaned_data["contact_email"])
    def clean_contact_phone(self):
        return normalize_phone(self.cleaned_data["contact_phone"])


class CareerProfileForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ['employment_status', 'work_availability']:
            self.fields[name].disabled = True
            self.fields[name].label = 'Previously recorded ' + name.replace('_', ' ')
            self.fields[name].help_text = 'Retained evidence. Edit current classifications in Recruiting preferences.'

    class Meta:
        model = ParticipantCareerProfile
        fields = ("employment_status", "employment_interest", "career_interests", "skills_interests",
                  "years_of_experience", "education_training", "work_availability", "willing_to_travel",
                  "preferred_work_type")
        labels = {"years_of_experience": "Years of work experience (total)", "career_interests": "Trades they want to work in",
                  "skills_interests": "Skills they have", "education_training": "School and training",
                  "employment_interest": "Looking for work", "preferred_work_type": "Work desired", "willing_to_travel": "Willing to travel"}
        widgets = {key: forms.Textarea(attrs={"rows": 3}) for key in ("career_interests", "skills_interests", "education_training")}


class StaffParticipantCreateForm(forms.ModelForm):
    lead_source = forms.CharField(required=False, max_length=240, label="Lead source", help_text="Staff-created people remain leads until required intake is verified.")
    confirm_distinct = forms.BooleanField(required=False, label="I reviewed the possible matches and this is a different person")
    duplicate_revision = forms.CharField(required=False, widget=forms.HiddenInput)
    audit_reason = forms.CharField(max_length=500, label="Source / reason for adding this person", required=False, widget=forms.HiddenInput)
    class Meta:
        model = Participant
        fields = ("first_name", "last_name", "preferred_name", "contact_email", "contact_phone", "city", "state", "postal_code")
    def clean_contact_email(self):
        return normalize_email(self.cleaned_data["contact_email"])
    def clean_contact_phone(self):
        return normalize_phone(self.cleaned_data["contact_phone"])


class WorkExperienceForm(OptionalReasonMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.source == "staff":
            self.fields["employer"].required = True
            self.fields["role"].required = True
    revision = forms.CharField(required=False, widget=forms.HiddenInput)
    audit_reason = forms.CharField(required=False, max_length=500, widget=forms.HiddenInput)
    class Meta:
        model = ParticipantWorkExperience
        fields = ("employer", "role", "location", "started_on", "ended_on", "is_current", "description", "source_reference")
        widgets = {"started_on": DATE, "ended_on": DATE, "description": forms.Textarea(attrs={"rows": 3})}
        labels = {"employer": "Employer", "role": "Job title", "location": "Where", "started_on": "Started",
                  "ended_on": "Ended", "is_current": "They still work here", "description": "What they did",
                  "source_reference": "Where this info came from"}


class AcquisitionForm(forms.ModelForm):
    """How we first met this person: at an event, online, or not known."""
    revision = forms.CharField(widget=forms.HiddenInput)
    audit_reason = forms.CharField(max_length=1000, required=False, widget=forms.HiddenInput)
    class Meta:
        model = ParticipantAcquisition
        fields = ("channel", "event", "online_source")
        labels = {"channel": "How they found us", "event": "Event", "online_source": "Where online"}
    def __init__(self, *args, participant, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.participant = participant
        # Participant type sits on the Details card, so it is edited here too (when allowed).
        if user.has_perm("participants.change_participant"):
            from .identifier_services import revision as type_revision
            type_choices = [("", "Not set")] + [(v, l) for v, l in Participant._meta.get_field("primary_identifier").choices if v]
            self.fields["participant_type"] = forms.ChoiceField(label="Participant type", required=False, choices=type_choices,
                widget=forms.RadioSelect(attrs={"class": "btw-chip-choices"}))
            self.fields["type_revision"] = forms.CharField(widget=forms.HiddenInput)
            self.initial.setdefault("participant_type", participant.primary_identifier)
            self.initial.setdefault("type_revision", type_revision(participant))
            self.order_fields(["participant_type", "channel", "event", "online_source"])
        choices = [("event", "Event"), ("online", "Online"), ("unknown", "Not known")]
        self.fields["channel"].choices = choices
        self.fields["channel"].widget = forms.RadioSelect(attrs={"class": "btw-chip-choices"}, choices=choices)
        self.fields["event"].queryset = Event.objects.order_by("-start_at") if user.has_perm("events.view_event") else Event.objects.none()
        self.fields["event"].empty_label = "Choose an event"
        self.fields["online_source"].widget.attrs["placeholder"] = "Website, Facebook, Indeed, a friend..."
        self.fields["event"].widget.attrs["data-show-when"] = "channel=event"
        self.fields["online_source"].widget.attrs["data-show-when"] = "channel=online"
    def clean(self):
        data = super().clean()
        channel = data.get("channel")
        if channel != "event":
            data["event"] = None
        elif not data.get("event"):
            self.add_error("event", "Choose the event.")
        if channel != "online":
            data["online_source"] = ""
        existing = self.instance.acquired_at if self.instance.pk else None
        if channel == "unknown":
            data["acquired_at"] = None
        else:
            data["acquired_at"] = existing
        data["source_submission"] = self.instance.source_submission if self.instance.pk else None
        data["provenance"] = self.instance.provenance if self.instance.pk else ""
        return data
    def _post_clean(self):
        pass


class NoteForm(forms.ModelForm):
    revision = forms.CharField(required=False, widget=forms.HiddenInput)
    request_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    audit_reason = forms.CharField(required=False, max_length=500, label="Change note", widget=forms.HiddenInput)
    class Meta:
        model = ParticipantNote
        fields = ("content",)
        widgets = {"content": forms.Textarea(attrs={"rows": 5})}
        labels = {"content": "Internal BTW note"}
    def clean_content(self):
        value = self.cleaned_data["content"].strip()
        if not value:
            raise ValidationError("Enter an internal note.")
        return value


class SavedViewForm(forms.Form):
    shared = forms.BooleanField(required=False, label="Share search criteria with authorized staff; current evidence permissions still apply")
    name = forms.CharField(max_length=100, label="View name")
    def clean_name(self):
        return self.cleaned_data["name"].strip()


class ParticipantEventForm(forms.Form):
    event = forms.ModelChoiceField(queryset=Event.objects.none())
    audit_reason = forms.CharField(max_length=500, label="Reason / source", required=False, widget=forms.HiddenInput)
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.operations.task_access import events_for
        self.fields["event"].queryset = events_for(user).filter(status="upcoming").order_by("-start_at")


class DiscoveryForm(forms.Form):
    from .identifier_definitions import PrimaryIdentifier
    identifier = forms.ChoiceField(required=False, label="Type", choices=[("", "Any type"), *PrimaryIdentifier.choices, ("unclassified", "Unclassified")])
    relocation = forms.ChoiceField(required=False, label="Will relocate", choices=[("", "Any"), ("yes", "Yes"), ("maybe", "Maybe"), ("no", "No"), ("unknown", "Not recorded")])
    employment_classification = forms.ChoiceField(required=False, label="Employment")
    availability_classification = forms.ChoiceField(required=False, label="Availability")
    segments = forms.CharField(required=False, max_length=12000, widget=forms.HiddenInput)
    facet = forms.ChoiceField(required=False, choices=[("", "No chart segment")] + [(x, x.replace("_", " ").title()) for x in ("channel", "state", "employment", "experience", "interest", "qualification", "document", "verification", "participation", "assessment", "completeness", "acquisition_month")], widget=forms.HiddenInput)
    bucket = forms.CharField(required=False, max_length=240, widget=forms.HiddenInput)
    q = forms.CharField(required=False, max_length=150, label="Name, ID, email, or phone", widget=forms.TextInput(attrs={"aria-describedby": "participant-search-help"}))
    trade_interest = forms.CharField(required=False, max_length=120, label="Trade / career interest")
    skill = forms.CharField(required=False, max_length=120, label="Skill or qualification")
    page_size = forms.ChoiceField(required=False, choices=[("25", "25"), ("10", "10"), ("50", "50"), ("100", "100")], initial="25", label="Per page")
    location = forms.CharField(required=False, max_length=200, label="Location", widget=forms.TextInput(attrs={"placeholder": "City, state, or both"}))
    city = forms.CharField(required=False, max_length=100)
    state = forms.CharField(required=False, max_length=100)
    employment = forms.ChoiceField(required=False, choices=[("", "Any"), ("seeking", "Yes"), ("open", "Maybe"), ("not_seeking", "No"), ("unknown", "Unknown")])
    experience_min = forms.DecimalField(required=False, min_value=0, max_value=80, decimal_places=1, label="Years of experience (at least)")
    experience_data = forms.ChoiceField(required=False, label="Experience recorded", choices=[("", "Any"), ("known", "Yes"), ("unknown", "No")])
    availability = forms.CharField(required=False, max_length=120, label="Availability notes")
    preferred_location = forms.CharField(required=False, max_length=120, label="Preferred work area")
    work_type = forms.ChoiceField(required=False, label="Work type", choices=[("", "Any")] + list(ParticipantCareerProfile._meta.get_field("preferred_work_type").choices[1:]))
    travel = forms.ChoiceField(required=False, choices=[("", "Any"), ("yes", "Yes"), ("maybe", "Maybe"), ("no", "No"), ("unknown", "Unknown")])
    channel = forms.ChoiceField(required=False, label="Found us", choices=[("", "Any"), ("event", "At an event"), ("online", "Online"), ("unknown", "Not known")])
    acquired_from = forms.DateField(required=False, widget=DATE, label="Found us from")
    acquired_to = forms.DateField(required=False, widget=DATE, label="Found us through")
    event = forms.ModelChoiceField(required=False, queryset=Event.objects.none(), label="Signed up for event", empty_label="Any event")
    participation = forms.ChoiceField(required=False, label="Event sign-ups", choices=[("", "Any"), ("registered", "Signed up for an event"), ("none", "No sign-ups")])
    qualification = forms.ModelChoiceField(required=False, queryset=QualificationCatalogEntry.objects.none(), empty_label="Any", label="Qualification")
    qualification_kind = forms.ChoiceField(required=False, label="Qualification kind", choices=[("", "Any")] + list(QualificationCatalogEntry.Kind.choices))
    qualification_status = forms.ChoiceField(required=False, label="Qualification status", choices=[("", "Any"), ("verified", "Has currently verified qualification"), ("pending", "Has pending qualification"), ("unverified", "Has unverified qualification"), ("expired", "Has expired qualification"), ("rejected", "Has rejected qualification"), ("none", "No active qualifications recorded")])
    document_status = forms.ChoiceField(required=False, label="Documents", choices=[("", "Any"), ("present", "Has active documents"), ("none", "No active documents recorded"), ("verified", "Has verified document"), ("pending", "Has pending document"), ("expired", "Has expired document"), ("rejected", "Has rejected document")])
    assessment = forms.ChoiceField(required=False, choices=[("", "Any assessment history"), ("quiz", "Accepted completed quiz"), ("simulator", "Accepted simulator result"), ("game", "Completed official game")])
    assessment_category = forms.ModelChoiceField(required=False, queryset=AssessmentCategory.objects.none(), label="Quiz category", empty_label="Any")
    score_min = forms.DecimalField(required=False, min_value=0, max_value=1000000, decimal_places=6, label="Score at least")
    score_max = forms.DecimalField(required=False, min_value=0, max_value=1000000, decimal_places=6, label="Score at most")
    completeness = forms.ChoiceField(required=False, label="Profile", choices=[("", "Any"), ("complete", "Complete"), ("partial", "Missing details")])
    archived = forms.ChoiceField(required=False, label="Show", choices=[("", "Active participants"), ("all", "Active and archived"), ("only", "Archived only")])
    sort = forms.ChoiceField(required=False, choices=[("", "Name A–Z"), ("name_desc", "Name Z–A"), ("newest", "Newest records"), ("oldest", "Oldest records"), ("acquired", "Most recent known acquisition"), ("experience", "Reported experience (highest first)")])

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if user.has_perm("events.view_eventregistration"):
            self.fields["event"].queryset = Event.objects.order_by("-start_at")
        from apps.recruiting.models import Classification
        for name, kind in (("employment_classification", "employment"), ("availability_classification", "availability")):
            if name in self.fields:
                self.fields[name].choices = [("", "Any")] + [(c.key, c.label) for c in Classification.objects.filter(kind=kind)]
        if user.has_perm("participants.view_participantqualification"):
            self.fields["qualification"].queryset = QualificationCatalogEntry.objects.order_by("name")
        if user.has_perm("assessments.view_quizattempt"):
            self.fields["assessment_category"].queryset = AssessmentCategory.objects.order_by("name")
        for key, perms in FILTER_PERMISSIONS.items():
            if perms and not user.has_perms(perms):
                self.fields.pop(key, None)
        allowed = [("", "Any assessment history")] + [(k, label) for k, label in [("quiz", "Accepted completed quiz"), ("simulator", "Accepted simulator result"), ("game", "Completed official game")] if user.has_perm(ASSESSMENT_PERMISSIONS[k])]
        self.fields["assessment"].choices = allowed
        if len(allowed) == 1:
            for key in ("assessment", "score_min", "score_max"):
                self.fields.pop(key, None)

    def clean(self):
        import json
        d = super().clean()
        try:
            segments = json.loads(d.get("segments") or "[]")
            allowed = {x[0] for x in self.fields["facet"].choices if x[0]}
            if not isinstance(segments, list) or len(segments) > 12:
                raise ValueError
            dimensions = set()
            for pair in segments:
                if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(x, str) for x in pair) or pair[0] not in allowed or not pair[1] or len(pair[1]) > 240 or pair[0] in dimensions:
                    raise ValueError
                dimensions.add(pair[0])
            d["segments"] = segments
        except (ValueError, TypeError):
            self.add_error("segments", "Chart selections are invalid. Clear the filters and try again.")
        if d.get("acquired_from") and d.get("acquired_to") and d["acquired_from"] > d["acquired_to"]:
            self.add_error("acquired_to", "The end of the acquisition range must follow its start.")
        if d.get("score_min") is not None or d.get("score_max") is not None:
            if not d.get("assessment"):
                self.add_error(None, "Choose one assessment type before entering a score range. Quiz = correct answers; simulator = percent; game = raw points.")
        if d.get("score_min") is not None and d.get("score_max") is not None and d["score_min"] > d["score_max"]:
            self.add_error("score_max", "Maximum score must be at least the minimum.")
        if d.get("channel") == "unknown" and (d.get("acquired_from") or d.get("acquired_to")):
            self.add_error("channel", "Unknown original acquisition has no asserted acquisition date.")
        return d

class RecordActionForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    audit_reason = forms.CharField(max_length=500, label="Reason", required=False, widget=forms.HiddenInput)
    confirm = forms.BooleanField(label="I confirm this change; the record and its history will be retained.")
