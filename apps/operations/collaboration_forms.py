import uuid
from django import forms
from django.db.models import Q
from apps.events.models import Event
from apps.participants.models import Participant
from .forms import TaskRevisionForm
from .task_access import events_for
from .task_links import participants_for


class TaskLinksForm(TaskRevisionForm):
    events = forms.ModelMultipleChoiceField(queryset=Event.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple, label="Events (up to 20 search results)")
    participants = forms.ModelMultipleChoiceField(queryset=Participant.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple, label="Participants (up to 20 search results)")

    def __init__(self, *args, actor, event_query="", participant_query="", include_selected=False, **kwargs):
        super().__init__(*args, **kwargs)
        events = events_for(actor).exclude(status="archived")
        if event_query:
            events = events.filter(name__icontains=event_query)
        ids = list(events.order_by("name", "pk").values_list("pk", flat=True)[:20])
        self.fields["events"].queryset = events.filter(pk__in=ids).order_by("name", "pk")
        people = participants_for(actor).filter(kind="person", archived_at__isnull=True)
        for word in participant_query.split():
            people = people.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word) | Q(preferred_name__icontains=word))
        ids = list(people.order_by("last_name", "first_name", "pk").values_list("pk", flat=True)[:20])
        self.fields["participants"].queryset = people.filter(pk__in=ids).order_by("last_name", "first_name", "pk")
        self.fields["participants"].label_from_instance = lambda person: str(person)
        if include_selected and self.is_bound:
            def submitted(name):
                raw=self.data.getlist(name) if hasattr(self.data,'getlist') else self.data.get(name,[])
                return ([raw] if isinstance(raw,str) else list(raw))[:50]
            event_ids=[]
            for value in submitted('events'):
                try:event_ids.append(uuid.UUID(str(value)))
                except (ValueError,TypeError):pass
            person_ids=[int(value) for value in submitted('participants') if str(value).isdigit() and len(str(value))<19]
            current=list(self.fields['events'].queryset.values_list('pk',flat=True))
            self.fields['events'].queryset=events_for(actor).exclude(status='archived').filter(pk__in=current+event_ids).order_by('name','pk')
            current=list(self.fields['participants'].queryset.values_list('pk',flat=True))
            self.fields['participants'].queryset=participants_for(actor).filter(kind='person',archived_at__isnull=True,pk__in=current+person_ids).order_by('last_name','first_name','pk')

    def clean(self):
        data = super().clean()
        if not data.get("events") and not data.get("participants"):
            raise forms.ValidationError("Select at least one eligible Event or Participant. Search by name to find another record.")
        return data


class TaskCommentForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    content = forms.CharField(max_length=4000, label="Comment", widget=forms.Textarea(attrs={"rows":4}),
        help_text="Plain text only. Do not include information that task collaborators should not see.")
