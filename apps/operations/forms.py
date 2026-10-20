from django import forms
from django.db.models import Q
from apps.events.models import Event
from apps.staffing.models import StaffProfile
from .models import Task,EventNote,EventDocument
class RevisionForm(forms.Form):
    revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={"rows":2}))
class TaskForm(forms.ModelForm):
    revision = forms.CharField(widget=forms.HiddenInput, required=False)
    reason = forms.CharField(label="Change note (optional)", max_length=500, required=False,
                             widget=forms.Textarea(attrs={"rows": 2}))
    class Meta:
        model = Task
        fields = ("title", "description", "assigned_staff", "event", "due_date", "priority")
        labels = {"assigned_staff": "Responsible staff member", "event": "Related event", "due_date": "Due date (optional)"}
        widgets = {"due_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
                   "description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, actor=None, event=None, **kwargs):
        from .task_access import eligible_assignees, events_for
        super().__init__(*args, **kwargs)
        self.original_assignee_id = self.instance.assigned_staff_id
        self.original_event_id = self.instance.event_id
        self.fields["assigned_staff"].queryset = StaffProfile.objects.filter(
            Q(pk__in=eligible_assignees().values("pk")) | Q(pk=self.original_assignee_id)).select_related("user")
        self.fields["assigned_staff"].help_text = "Choose an active staff login. An existing legacy assignment may be retained."
        allowed = events_for(actor) if actor else Event.objects.none()
        self.visible_event_ids = set(allowed.values_list("pk", flat=True))
        self.fields["event"].queryset = Event.objects.filter(
            (Q(pk__in=self.visible_event_ids) & ~Q(status="archived")) | Q(pk=self.original_event_id))
        self.fields["event"].label_from_instance = lambda obj: obj.name if obj.pk in self.visible_event_ids else "Associated event (restricted)"
        self.fields["event"].help_text = "Optional. Event access remains separate from task access."
        self.initial["revision"] = self.instance.updated_at.isoformat() if self.instance.updated_at else "new"
        if event:
            self.initial["event"] = event.pk
            self.fields["event"].disabled = True
        if not self.instance.pk or self.instance._state.adding:
            own = eligible_assignees().filter(user=actor).first() if actor else None
            if own:
                self.initial["assigned_staff"] = own.pk

    def clean_assigned_staff(self):
        from .task_access import eligible_assignees
        profile = self.cleaned_data["assigned_staff"]
        unchanged = not self.instance._state.adding and profile.pk == self.original_assignee_id
        if not unchanged and not eligible_assignees().filter(pk=profile.pk).exists():
            raise forms.ValidationError("Choose an active staff profile linked to an active staff login.")
        return profile

class StatusForm(RevisionForm):
    status = forms.ChoiceField(choices=Task.Status.choices)
    def __init__(self, *args, manager=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].required = False
        self.fields["reason"].label = "Change note (optional)"

class TaskRevisionForm(RevisionForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].required = False
        self.fields["reason"].label = "Change note (optional)"

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
