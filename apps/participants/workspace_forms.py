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
    audit_reason = forms.CharField(max_length=500, label="Reason for this change")
    class Meta:
        model = Participant
        fields = ("first_name", "last_name", "preferred_name", "contact_email", "contact_phone", "city", "state", "postal_code")
    def clean_contact_email(self):
        return normalize_email(self.cleaned_data["contact_email"])
    def clean_contact_phone(self):
        return normalize_phone(self.cleaned_data["contact_phone"])


class CareerProfileForm(forms.ModelForm):
    class Meta:
        model = ParticipantCareerProfile
        fields = ("employment_status", "employment_interest", "career_interests", "skills_interests",
                  "years_of_experience", "education_training", "work_availability", "willing_to_travel",
                  "preferred_locations", "preferred_work_type")
        labels = {"years_of_experience": "Reported overall experience (years)", "career_interests": "Trade and career interests",
                  "skills_interests": "Reported skills and interests"}
        widgets = {key: forms.Textarea(attrs={"rows": 3}) for key in ("career_interests", "skills_interests", "education_training")}


class StaffParticipantCreateForm(forms.ModelForm):
    confirm_distinct = forms.BooleanField(required=False, label="I reviewed the possible matches and this is a different person")
    duplicate_revision = forms.CharField(required=False, widget=forms.HiddenInput)
    audit_reason = forms.CharField(max_length=500, label="Source / reason for adding this person")
    class Meta:
        model = Participant
        fields = ("first_name", "last_name", "preferred_name", "contact_email", "contact_phone", "city", "state", "postal_code")
    def clean_contact_email(self):
        return normalize_email(self.cleaned_data["contact_email"])
    def clean_contact_phone(self):
        return normalize_phone(self.cleaned_data["contact_phone"])


class WorkExperienceForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.source == "staff":
            self.fields["employer"].required = True
            self.fields["role"].required = True
    revision = forms.CharField(required=False, widget=forms.HiddenInput)
    audit_reason = forms.CharField(max_length=500, label="Reason / source for this change")
    class Meta:
        model = ParticipantWorkExperience
        fields = ("employer", "role", "location", "started_on", "ended_on", "is_current", "description", "source_reference")
        widgets = {"started_on": DATE, "ended_on": DATE, "description": forms.Textarea(attrs={"rows": 4})}


class AcquisitionForm(forms.ModelForm):
    revision = forms.CharField(widget=forms.HiddenInput)
    audit_reason = forms.CharField(max_length=1000, label="Evidence and reason for this correction",
        help_text="Describe why this is the original source. Later participation must not replace it.")
    class Meta:
        model = ParticipantAcquisition
        fields = ("channel", "acquired_at", "event", "online_source", "source_submission", "provenance")
        labels = {"acquired_at": "Original acquisition time (UTC)", "source_submission": "Supporting intake submission"}
        widgets = {"acquired_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}
    def __init__(self, *args, participant, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.participant = participant
        self.fields["source_submission"].queryset = RegistrationSubmission.objects.filter(participant=participant).order_by("-submitted_at")
        self.fields["event"].queryset = Event.objects.order_by("-start_at") if user.has_perm("events.view_event") else Event.objects.none()
        self.fields["event"].help_text = "Event references require event-view permission. Unknown historical origins should stay unknown."
        self.fields["acquired_at"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]


class NoteForm(forms.ModelForm):
    revision = forms.CharField(required=False, widget=forms.HiddenInput)
    request_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    audit_reason = forms.CharField(max_length=500, label="Reason for this entry or edit")
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
    name = forms.CharField(max_length=100, label="View name")
    def clean_name(self):
        return self.cleaned_data["name"].strip()


class ParticipantEventForm(forms.Form):
    event = forms.ModelChoiceField(queryset=Event.objects.none())
    audit_reason = forms.CharField(max_length=500, label="Reason / source")
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.operations.task_access import events_for
        self.fields["event"].queryset = events_for(user).filter(status="upcoming").order_by("-start_at")


class DiscoveryForm(forms.Form):
    segments = forms.CharField(required=False, max_length=12000, widget=forms.HiddenInput)
    facet = forms.ChoiceField(required=False, choices=[("", "No chart segment")] + [(x, x.replace("_", " ").title()) for x in ("channel", "state", "employment", "experience", "interest", "qualification", "document", "verification", "participation", "assessment", "completeness", "acquisition_month")], widget=forms.HiddenInput)
    bucket = forms.CharField(required=False, max_length=240, widget=forms.HiddenInput)
    q = forms.CharField(required=False, max_length=150, label="Name, email, or phone")
    trade_interest = forms.CharField(required=False, max_length=120, label="Trade / career interest")
    skill = forms.CharField(required=False, max_length=120, label="Recorded skill / qualification text")
    city = forms.CharField(required=False, max_length=100)
    state = forms.CharField(required=False, max_length=100)
    employment = forms.ChoiceField(required=False, choices=[("", "Any employment interest")] + list(ParticipantCareerProfile._meta.get_field("employment_interest").choices[1:]))
    experience_min = forms.DecimalField(required=False, min_value=0, max_value=80, decimal_places=1, label="Minimum reported experience (years)")
    experience_data = forms.ChoiceField(required=False, choices=[("", "Any experience data"), ("known", "Reported overall experience"), ("unknown", "Overall experience unknown")])
    availability = forms.CharField(required=False, max_length=120, label="Availability text")
    preferred_location = forms.CharField(required=False, max_length=120, label="Preferred work geography")
    work_type = forms.ChoiceField(required=False, choices=[("", "Any work arrangement")] + list(ParticipantCareerProfile._meta.get_field("preferred_work_type").choices[1:]))
    travel = forms.ChoiceField(required=False, choices=[("", "Any travel preference"), ("yes", "Willing to travel"), ("no", "Not willing to travel"), ("maybe", "May be willing to travel"), ("unknown", "Travel preference unknown")])
    channel = forms.ChoiceField(required=False, choices=[("", "Any original acquisition")] + list(ParticipantAcquisition.Channel.choices))
    acquired_from = forms.DateField(required=False, widget=DATE, label="Acquired from")
    acquired_to = forms.DateField(required=False, widget=DATE, label="Acquired through")
    event = forms.ModelChoiceField(required=False, queryset=Event.objects.none(), label="Registered event")
    participation = forms.ChoiceField(required=False, choices=[("", "Any registration history"), ("registered", "Has active event registration"), ("none", "No active registration recorded")])
    qualification = forms.ModelChoiceField(required=False, queryset=QualificationCatalogEntry.objects.none())
    qualification_kind = forms.ChoiceField(required=False, choices=[("", "Any qualification kind")] + list(QualificationCatalogEntry.Kind.choices))
    qualification_status = forms.ChoiceField(required=False, choices=[("", "Any credential review"), ("verified", "Has currently verified qualification"), ("pending", "Has pending qualification"), ("unverified", "Has unverified qualification"), ("expired", "Has expired qualification"), ("rejected", "Has rejected qualification"), ("none", "No active qualifications recorded")])
    document_status = forms.ChoiceField(required=False, choices=[("", "Any document status"), ("present", "Has active documents"), ("none", "No active documents recorded"), ("verified", "Has verified document"), ("pending", "Has pending document"), ("expired", "Has expired document"), ("rejected", "Has rejected document")])
    assessment = forms.ChoiceField(required=False, choices=[("", "Any assessment history"), ("quiz", "Accepted completed quiz"), ("simulator", "Accepted simulator result"), ("game", "Completed official game")])
    assessment_category = forms.ModelChoiceField(required=False, queryset=AssessmentCategory.objects.none(), label="Quiz category")
    score_min = forms.DecimalField(required=False, min_value=0, max_value=1000000, decimal_places=6, label="Minimum stored score")
    score_max = forms.DecimalField(required=False, min_value=0, max_value=1000000, decimal_places=6, label="Maximum stored score")
    completeness = forms.ChoiceField(required=False, choices=[("", "Any profile coverage"), ("complete", "All six profile groups recorded"), ("partial", "Some profile groups unknown")])
    archived = forms.ChoiceField(required=False, choices=[("", "Active participants"), ("all", "Active and archived"), ("only", "Archived participants")])
    sort = forms.ChoiceField(required=False, choices=[("", "Name A–Z"), ("name_desc", "Name Z–A"), ("newest", "Newest records"), ("oldest", "Oldest records"), ("acquired", "Most recent known acquisition"), ("experience", "Reported experience (highest first)")])

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if user.has_perm("events.view_eventregistration"):
            self.fields["event"].queryset = Event.objects.order_by("-start_at")
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
    audit_reason = forms.CharField(max_length=500, label="Reason")
    confirm = forms.BooleanField(label="I confirm this change; the record and its history will be retained.")
