from django import forms
from apps.events.models import OfflineEventPreparation
from apps.stations.models import Station
from .models import Device

class ContextForm(forms.Form):
    preparation=forms.ModelChoiceField(queryset=OfflineEventPreparation.objects.none(),label="Local event preparation")
    revision=forms.IntegerField(widget=forms.HiddenInput)
    reason=forms.CharField(max_length=300)
    def __init__(self,*args,trailer,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["preparation"].queryset=OfflineEventPreparation.objects.filter(trailer=trailer).exclude(event__status__in=("draft","cancelled","archived")).select_related("event")
        self.fields["preparation"].label_from_instance=lambda prep:f"{prep.event.name} — {prep.get_status_display()}"

class DeviceForm(forms.Form):
    name=forms.CharField(max_length=100)
    kind=forms.ChoiceField(choices=Device.Kind.choices,label="Device type")
    connection=forms.ChoiceField(choices=Device._meta.get_field("connection").choices,label="Connection type (declared)")
    station=forms.ModelChoiceField(queryset=Station.objects.none(),required=False)
    def __init__(self,*args,preparation=None,**kwargs):
        super().__init__(*args,**kwargs)
        if preparation:self.fields["station"].queryset=Station.objects.filter(event_assignments__event=preparation.event,event_assignments__enabled=True,is_active=True).distinct()

class SyncForm(forms.Form):
    direction=forms.ChoiceField(choices=[("outbound","Send queued registration/intake data"),("inbound","Download selected event updates")])
    request_key=forms.UUIDField(widget=forms.HiddenInput)
    reason=forms.CharField(max_length=300)

class ReasonForm(forms.Form):
    reason=forms.CharField(max_length=300)
