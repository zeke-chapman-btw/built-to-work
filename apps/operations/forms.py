from django import forms
from django.db.models import Q
from apps.events.models import Event
from apps.staffing.models import StaffProfile
from .models import Task,EventNote,EventDocument
class RevisionForm(forms.Form):
    revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={"rows":2}))
class TaskForm(forms.ModelForm):
    revision=forms.CharField(widget=forms.HiddenInput,required=False)
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={"rows":2}))
    class Meta:
        model=Task
        fields=("title","description","assigned_staff","event","due_date","priority")
        widgets={"due_date":forms.DateInput(attrs={"type":"date"}),"description":forms.Textarea(attrs={"rows":4})}
    def __init__(self,*args,event=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["assigned_staff"].queryset=StaffProfile.objects.filter(Q(is_active=True)|Q(pk=self.instance.assigned_staff_id))
        self.fields["event"].queryset=Event.objects.exclude(status="archived")
        self.initial["revision"]=self.instance.updated_at.isoformat() if self.instance.updated_at else "new"
        if event:
            self.initial["event"]=event.pk;self.fields["event"].disabled=True
    def clean_assigned_staff(self):
        p=self.cleaned_data["assigned_staff"]
        if not p.is_active and (self.instance._state.adding or p.pk!=self.instance.assigned_staff_id):raise forms.ValidationError("Choose active staff.")
        return p
class StatusForm(RevisionForm):
    status=forms.ChoiceField(choices=Task.Status.choices)
    def __init__(self,*args,manager=False,**kwargs):
        super().__init__(*args,**kwargs)
        if not manager:self.fields["status"].choices=[(v,l) for v,l in Task.Status.choices if v in ("in_progress","completed")]
class NoteForm(forms.ModelForm):
    revision=forms.CharField(widget=forms.HiddenInput,required=False)
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={"rows":2}))
    class Meta:
        model=EventNote
        fields=("content","visibility")
        widgets={"content":forms.Textarea(attrs={"rows":4})}
    def __init__(self,*args,manager=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.initial["revision"]=self.instance.updated_at.isoformat() if self.instance.updated_at else "new"
        if not manager:self.fields["visibility"].choices=[("staff","Assigned event staff")];self.initial["visibility"]="staff"
class DocumentForm(forms.ModelForm):
    file=forms.FileField()
    reason=forms.CharField(max_length=500)
    class Meta:
        model=EventDocument
        fields=("title","description","visibility")
class DemoForm(forms.Form):
    visibility=forms.ChoiceField(choices=EventNote._meta.get_field("visibility").choices,initial="managers")
    confirm=forms.BooleanField(label="Create a clearly labeled synthetic preview PDF, not a real upload.")
    reason=forms.CharField(max_length=500)
