from datetime import datetime
from zoneinfo import ZoneInfo
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from .models import StaffProfile
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


class PlanForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    required_staff = forms.IntegerField(min_value=0, max_value=1000, label="Default staff needed per day")
    is_published = forms.BooleanField(required=False, label="Publish this staffing opportunity", help_text="Visible to active linked staff only while the event is Upcoming. Draft events remain hidden.")
    signup_deadline = forms.CharField(required=False, label="Staff sign-up deadline (event local time)", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}), help_text="Leave blank for no deadline. Managers may approve and assign after the deadline.")
    def __init__(self, *args, event, plan, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.initial.update(revision=plan.updated_at.isoformat() if plan.updated_at else "new", required_staff=plan.required_staff, is_published=plan.is_published, signup_deadline=plan.signup_deadline.astimezone(ZoneInfo(event.timezone_name)).strftime("%Y-%m-%dT%H:%M") if plan.signup_deadline else "")
        needs={n.date:n.required_staff for n in plan.daily_needs.all()} if not plan._state.adding else {}
        for day in event_days(event):
            key="need_"+day.isoformat()
            self.fields[key]=forms.IntegerField(min_value=0,max_value=1000,label=f"{day:%a, %b %d, %Y} — staff needed",initial=needs.get(day,plan.required_staff))
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
    role = forms.CharField(required=False,max_length=80,label="Role or responsibility")
    reason = forms.CharField(max_length=500,widget=forms.Textarea(attrs={"rows":2}),label="Assignment change reason")
    def __init__(self,*args,event,assignment=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.event=event
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
        return [self[n] for n in ("staff","role","reason")]
    def clean(self):
        data=super().clean(); rows=[]
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
