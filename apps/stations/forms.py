from django import forms

from apps.events.models import Event
from .models import EventStation, Station


class StationForm(forms.ModelForm):
    class Meta:
        model = Station
        fields = ("code", "name", "station_type", "description", "location", "is_active")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and not self.instance._state.adding:
            self.fields["code"].disabled = True


class EventStationForm(forms.ModelForm):
    event = forms.ModelChoiceField(queryset=Event.objects.order_by("-start_at"), label="Event")

    class Meta:
        model = EventStation
        fields = ("event", "enabled", "display_order")


class StationForEventForm(forms.Form):
    station = forms.ModelChoiceField(queryset=Station.objects.filter(is_active=True).order_by("name"))
    enabled = forms.BooleanField(required=False, initial=True)
    display_order = forms.IntegerField(min_value=0, max_value=999, initial=0)


class ExperienceModeForm(forms.Form):
    mode = forms.ChoiceField(choices=(("staff_test", "Staff test"), ("demo", "Demo")))


class SkipActivityForm(forms.Form):
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows": 2, "required": True}))
