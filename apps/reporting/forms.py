from datetime import timedelta
from zoneinfo import ZoneInfo
from django import forms
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from apps.events.models import Event
from apps.customers.models import CustomerOrganization,Campaign
from apps.recruiting.models import RecruitingOpportunity
from apps.staffing.models import StaffProfile
from apps.workdays.models import Workday

class ReportFilter(forms.Form):
    start = forms.DateField(required=False, label="From", widget=forms.DateInput(attrs={"type":"date"}))
    end = forms.DateField(required=False, label="Through", widget=forms.DateInput(attrs={"type":"date"}))
    event = forms.ModelChoiceField(queryset=Event.objects.none(), required=False)
    customer = forms.ModelChoiceField(queryset=CustomerOrganization.objects.none(), required=False)
    region = forms.CharField(required=False, max_length=100, label="State / region")
    identifier = forms.ChoiceField(required=False, choices=[("", "Any participant identifier"), ("student","Student"), ("employee","Employee"), ("teacher_instructor","Teacher / Instructor"), ("job_seeker","Job Seeker"), ("other","Other"), ("unclassified","Unclassified")])
    segments = forms.CharField(required=False,max_length=4000,widget=forms.HiddenInput)
    travel = forms.ChoiceField(required=False,choices=[("","Any"),("yes","Yes"),("maybe","Maybe"),("no","No"),("unknown","Unknown")],widget=forms.HiddenInput)
    assessment = forms.ChoiceField(required=False,choices=[("","Any"),("quiz","Quiz"),("simulator","Simulator"),("game","Game")],widget=forms.HiddenInput)
    score_min = forms.DecimalField(required=False,widget=forms.HiddenInput)
    score_max = forms.DecimalField(required=False,widget=forms.HiddenInput)
    campaign = forms.ModelChoiceField(queryset=Campaign.objects.none(),required=False)
    opportunity = forms.ModelChoiceField(queryset=RecruitingOpportunity.objects.none(),required=False)
    def __init__(self, *args, actor, tab="highlights", **kwargs):
        super().__init__(*args, **kwargs)
        from .access import allowed_tabs
        tabs = allowed_tabs(actor)
        if "opportunities" in tabs:
            from apps.recruiting.selection import opportunities_for
            authorized=opportunities_for(actor).select_related("need__organization","need__effective_revision").order_by("need__organization__name","pk")
            self.fields["opportunity"].queryset=authorized
            self.fields["opportunity"].label_from_instance=lambda row:row.need.organization.name+" · "+(row.need.effective_revision.title if row.need.effective_revision else "Position awaiting approved details")
            self.fields["campaign"].queryset=Campaign.objects.filter(pk__in=authorized.values("need__campaign_id")).order_by("name")
        if "events" in tabs: self.fields["event"].queryset = Event.objects.all().order_by("-start_at", "name")
        if "customers" in tabs:
            from apps.customers.access import organizations
            self.fields["customer"].queryset = organizations(actor).order_by("name")
        elif "opportunities" in tabs:
            from apps.recruiting.selection import opportunities_for
            self.fields["customer"].queryset = CustomerOrganization.objects.filter(workforce_needs__recruiting_opportunity__in=opportunities_for(actor)).distinct().order_by("name")
        if tab in ("events", "participants"): self.fields.pop("customer")
        if tab not in ("events", "participants", "highlights"): self.fields.pop("event")
        if tab != "participants":
            for key in ("identifier","segments","travel","assessment","score_min","score_max"):self.fields.pop(key)
        if tab == "payroll":self.fields.pop("region")
        if tab not in ("opportunities","hires","customers"): self.fields.pop("campaign"); self.fields.pop("opportunity")
    def clean(self):
        d = super().clean()
        for key in ("event","customer","identifier","campaign","opportunity","segments","travel","assessment","score_min","score_max"):
            if key not in self.fields and self.data.get(key):self.add_error(None,"This report does not support the selected "+key+" filter.")
        if d.get("start") and d.get("end") and d["start"] > d["end"]:
            self.add_error("end", "Through must be on or after From.")
        return d

class ReasonForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows":2}))

class PayrollStartForm(forms.Form):
    starts_on = forms.DateField(label="Week starting Monday", widget=forms.DateInput(attrs={"type":"date"}))
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows":2}))
    def clean_starts_on(self):
        value = self.cleaned_data["starts_on"]
        if value.weekday() != 0: raise forms.ValidationError("Choose a Monday; the period ends Sunday.")
        return value

class ManualFigureForm(ReasonForm):
    label = forms.CharField(max_length=120)
    value = forms.DecimalField(max_digits=14, decimal_places=2)
    unit = forms.CharField(max_length=40)
    source_note = forms.CharField(max_length=500)

class AdjustmentForm(forms.Form):
    event = forms.ModelChoiceField(queryset=Event.objects.all())
    staff = forms.ModelChoiceField(queryset=StaffProfile.objects.all())
    workday = forms.ModelChoiceField(queryset=Workday.objects.filter(is_active=True), required=False)
    occurred_on = forms.DateField(widget=forms.DateInput(attrs={"type":"date"}))
    kind = forms.ChoiceField(choices=[("incentive","Contractor incentive"),("adjustment","Compensation adjustment")])
    amount = forms.DecimalField(max_digits=14, decimal_places=2)
    reference = forms.CharField(max_length=200)
    reason = forms.CharField(max_length=500,widget=forms.Textarea(attrs={"rows":2}))
    operation_id = forms.UUIDField(widget=forms.HiddenInput)
