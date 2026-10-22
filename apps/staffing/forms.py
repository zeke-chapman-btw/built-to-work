from datetime import datetime
from zoneinfo import ZoneInfo
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from .models import StaffProfile, StaffAssignment
from .calendar import event_days, day_window, local_instant


class ProfileForm(forms.ModelForm):
    revision = forms.CharField(required=False, widget=forms.HiddenInput)
    class Meta:
        model = StaffProfile
        fields = ("first_name", "last_name", "email", "phone", "street_address", "city", "state", "zip_code", "shirt_size", "is_active", "internal_notes")
        widgets = {"internal_notes": forms.Textarea(attrs={"rows": 3})}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance._state.adding:
            self.initial["revision"] = self.instance.updated_at.isoformat()


class AccountLinkForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    user = forms.ModelChoiceField(queryset=get_user_model().objects.none(), required=False, label="Existing staff account", help_text="Leave blank to unlink. This does not create an account or change its permissions.")
    confirm = forms.BooleanField(label="I confirm this account belongs to this staff member and authorize this link change.")
    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = get_user_model().objects.filter(is_active=True, is_staff=True).filter(Q(staff_profile__isnull=True) | Q(pk=profile.user_id)).order_by("username")
        self.initial.update(user=profile.user_id, revision=profile.updated_at.isoformat())


def legacy_coverage_role(value):
    """Only exact known responsibilities can classify an older submission."""
    return {"staff": "staff", "event staff": "staff", "staff lead": "staff_lead",
            "lead": "staff_lead", "driver": "driver", "combined": "combined",
            "driver + staff": "combined"}.get(value.strip().casefold(), "")


def coverage_role_field():
    return forms.ChoiceField(required=False, choices=[("", "Needs role review"), *StaffAssignment.CoverageRole.choices],
        label="Coverage role", help_text="Staff Lead counts as Staff. Combined counts as Driver only. This does not set pay or mileage.")


class PlanForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    required_staff = forms.IntegerField(min_value=0, max_value=1000, label="Default staff needed per day")
    required_drivers = forms.IntegerField(min_value=0, max_value=1000, required=False, label="Default drivers needed per day")
    is_published = forms.BooleanField(required=False, label="Publish this staffing opportunity", help_text="Visible to active linked staff only while the event is Upcoming. Draft events remain hidden.")
    signup_deadline = forms.CharField(required=False, label="Staff sign-up deadline (event local time)", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}), help_text="Leave blank for no deadline. Managers may approve and assign after the deadline.")
    def __init__(self, *args, event, plan, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.initial.update(revision=plan.updated_at.isoformat() if plan.updated_at else "new", required_staff=plan.required_staff, required_drivers=plan.required_drivers, is_published=plan.is_published, signup_deadline=plan.signup_deadline.astimezone(ZoneInfo(event.timezone_name)).strftime("%Y-%m-%dT%H:%M") if plan.signup_deadline else "")
        needs={n.date:n for n in plan.daily_needs.all()} if not plan._state.adding else {}
        self.day_rows=[]
        for day in event_days(event):
            key="need_"+day.isoformat()
            self.fields[key]=forms.IntegerField(min_value=0,max_value=1000,label="Staff",initial=needs[day].required_staff if day in needs else plan.required_staff)
            driver_key="drivers_"+day.isoformat()
            self.fields[driver_key]=forms.IntegerField(min_value=0,max_value=1000,required=False,label="Driver",initial=needs[day].required_drivers if day in needs else plan.required_drivers)
            self.day_rows.append({"date":day,"staff":self[key],"drivers":self[driver_key]})
        if self.is_bound:
            # Older clients must not erase newly configured driver needs.
            self.data=self.data.copy()
            for key in ["required_drivers", *("drivers_"+d.isoformat() for d in event_days(event))]:
                if key not in self.data:
                    self.data[key]=self.initial.get(key,self.fields[key].initial) or 0
    @property
    def settings_fields(self):
        return [self[n] for n in ("required_staff","required_drivers","is_published","signup_deadline")]
    def clean(self):
        data=super().clean()
        for key in ["required_drivers", *("drivers_"+d.isoformat() for d in event_days(self.event))]:
            if key not in self.errors and data.get(key) is None:
                data[key]=0
        return data
    def clean_signup_deadline(self):
        raw=self.cleaned_data.get("signup_deadline")
        if not raw: return None
        try:
            value=datetime.fromisoformat(raw)
            if value.tzinfo: raise ValueError
            return local_instant(value.date(),value.time(),ZoneInfo(self.event.timezone_name))
        except (ValueError,TypeError):
            raise ValidationError("Enter a local date and time in the event timezone.")


class AvailabilityForm(forms.Form):
    dates = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple,label="Days I can work")
    note = forms.CharField(required=False,max_length=1000,widget=forms.Textarea(attrs={"rows":3}),label="Availability note (visible to managers)")
    def __init__(self,*args,event,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["dates"].choices=[(d.isoformat(),d.strftime("%a, %b %d, %Y")+(" · Setup" if d<event.local_start_at.date() else "")) for d in event_days(event)]


class AssignmentForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    staff = forms.ModelChoiceField(queryset=StaffProfile.objects.none(),label="Assigned employee")
    coverage_role = coverage_role_field()
    role = forms.CharField(required=False,max_length=80,label="Additional responsibility (optional)")
    reason = forms.CharField(max_length=500,widget=forms.Textarea(attrs={"rows":2}),label="Assignment change reason")
    def __init__(self,*args,event,assignment=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.event=event
        self.assignment=assignment
        self.initial["coverage_role"]=assignment.coverage_role if assignment else "staff"
        self.fields["staff"].queryset=StaffProfile.objects.filter(is_active=True)
        self.initial.update(revision=assignment.updated_at.isoformat() if assignment else "new",staff=assignment.staff_id if assignment else None,role=assignment.role if assignment else "")
        existing={d.date:d for d in assignment.days.all()} if assignment else {}
        self.day_rows=[]
        zone=ZoneInfo(event.timezone_name)
        for day in event_days(event):
            key=day.isoformat(); old=existing.get(day)
            self.fields["day_"+key]=forms.BooleanField(required=False,label=day.strftime("%a, %b %d, %Y"),initial=bool(old))
            for name in ("arrival","start","end"):
                initial=None
                if old:
                    v=getattr(old,{"arrival":"arrival_at","start":"starts_at","end":"ends_at"}[name])
                    # A whole-day default can end at next midnight. Blank preserves that default.
                    if v and v.astimezone(zone).date()==day and (name=="arrival" or old.ends_at.astimezone(zone).date()==day): initial=v.astimezone(zone).strftime("%H:%M")
                self.fields[name+"_"+key]=forms.TimeField(required=False,label=name.title()+" time",widget=forms.TimeInput(attrs={"type":"time"},format="%H:%M"),initial=initial)
            self.day_rows.append({"date":day,"selected":self["day_"+key],"arrival":self["arrival_"+key],"start":self["start_"+key],"end":self["end_"+key]})
    @property
    def main_fields(self):
        return [self[n] for n in ("staff","coverage_role","role","reason")]
    def clean(self):
        data=super().clean(); rows=[]
        if "coverage_role" not in self.data:
            data["coverage_role"]=self.assignment.coverage_role if self.assignment else legacy_coverage_role(data.get("role",""))
        for day in event_days(self.event):
            key=day.isoformat()
            if data.get("day_"+key):
                try: rows.append(day_window(self.event,day,data.get("start_"+key),data.get("end_"+key),data.get("arrival_"+key)))
                except ValidationError as exc: self.add_error("day_"+key,exc)
        if not rows: raise ValidationError("Select at least one working day.")
        data["days"]=rows
        return data


class DecisionForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    reason = forms.CharField(required=False,max_length=500,widget=forms.Textarea(attrs={"rows":2}))
    confirm = forms.BooleanField(label="I confirm this action.")

    def __init__(self,*args,approve=False,**kwargs):
        super().__init__(*args,**kwargs)
        if approve:
            self.fields["coverage_role"]=coverage_role_field()
            self.initial["coverage_role"]="staff"
