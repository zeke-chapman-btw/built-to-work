"""Atomic quick creation reuses TaskForm and the established collaboration policy."""
import uuid
from copy import deepcopy
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction, IntegrityError
from django.shortcuts import get_object_or_404
from .forms import TaskForm
from .collaboration_forms import TaskLinksForm
from . import services, collaboration, task_access, task_links
from .models import Task

TOKEN_SALT='operations.quick-create.9g2c'

def new_token(actor):
    return signing.dumps({'actor':str(actor.pk),'task':str(uuid.uuid4())},salt=TOKEN_SALT)

def context_for(actor,event_id=None,participant_id=None):
    task_access.require_task_staff(actor)
    event=get_object_or_404(task_access.events_for(actor).exclude(status='archived'),pk=event_id) if event_id else None
    person=get_object_or_404(task_links.participants_for(actor).filter(kind='person',archived_at__isnull=True),pk=participant_id) if participant_id else None
    return event,person

class QuickTaskForm(TaskForm):
    creation_token=forms.CharField(widget=forms.HiddenInput)
    def __init__(self,*args,actor,event=None,participant=None,**kwargs):
        super().__init__(*args,actor=actor,event=event,**kwargs)
        self.actor=actor
        # The legacy primary FK is set only by the authorized Event context.
        self.fields['event'].disabled=True
        self.fields['event'].widget=forms.HiddenInput()
        self.fields['reason'].widget=forms.HiddenInput()
        choices=TaskLinksForm(self.data if self.is_bound else None,actor=actor,include_selected=True)
        for name in ('events','participants'):
            self.fields[name]=deepcopy(choices.fields[name])
            self.fields[name].widget=forms.SelectMultiple(attrs={'size':4,'data-record-select':name})
            self.fields[name].widget.choices=self.fields[name].choices
            self.fields[name].label='Linked Events (optional)' if name=='events' else 'Linked Participants (optional)'
            self.fields[name].help_text='Select multiple records with Ctrl/Command. Search by name to find more.'
        if not self.is_bound:self.initial['creation_token']=new_token(actor)
    def clean_creation_token(self):
        raw=self.cleaned_data['creation_token']
        try:
            payload=signing.loads(raw,salt=TOKEN_SALT,max_age=7200)
            if payload['actor']!=str(self.actor.pk):raise ValueError
            self.creation_id=uuid.UUID(payload['task'])
        except (signing.BadSignature,ValueError,KeyError,TypeError):
            raise forms.ValidationError('This creation form has expired. Reload the form and try again.')
        return raw
    def clean(self):
        data=super().clean()
        for key in ('events','participants'):
            if len(data.get(key,()))>20:self.add_error(key,'Select up to 20 records of each type for quick creation. More can be linked afterward.')
        return data

def prepare(actor,data=None,*,event_id=None,participant_id=None):
    event,person=context_for(actor,event_id,participant_id)
    if data is not None:
        data=data.copy()
        if person:
            selected=data.getlist('participants')
            data.setlist('participants',list(dict.fromkeys(selected+[str(person.pk)])))
    form=QuickTaskForm(data,actor=actor,event=event,participant=person)
    if data is None and person:
        # Keep a mandatory context record visible even when outside the first page.
        from apps.participants.models import Participant
        form.fields['participants'].queryset=Participant.objects.filter(pk__in=list(form.fields['participants'].queryset.values_list('pk',flat=True))+[person.pk])
        form.initial['participants']=[person.pk]
    return form,event,person

def create(actor,form,*,event=None):
    task_access.require_task_staff(actor)
    existing=Task.objects.filter(pk=form.creation_id).first()
    if existing:
        if existing.created_by_id!=actor.pk:raise PermissionDenied
        task_access.require_task_access(actor,existing)
        return existing,True
    data=form.cleaned_data
    payload={'title':data['title'],'description':data['description'],'assigned_staff':str(data['assigned_staff'].pk),
        'event':str(event.pk) if event else '', 'due_date':data['due_date'].isoformat() if data['due_date'] else '',
        'priority':data['priority'],'revision':'new','reason':''}
    try:
        with transaction.atomic():
            task,checked=services.save_task(actor=actor,data=payload,event_id=event.pk if event else None,creation_id=form.creation_id)
            if not task:raise ValidationError('Assignment or Event access changed. Review the form and try again.')
            if data['events'] or data['participants']:
                linked,checked=collaboration.add_links(actor=actor,task_id=task.pk,include_selected=True,data={
                    'revision':task.updated_at.isoformat(),'events':[str(e.pk) for e in data['events']],
                    'participants':[str(p.pk) for p in data['participants']]})
                if not linked:raise ValidationError('Some linked records are no longer eligible. Review your selection.')
                task=linked
            return task,False
    except IntegrityError:
        # The signed UUID and existing primary-key uniqueness handle retries across workers.
        existing=Task.objects.filter(pk=form.creation_id,created_by=actor).first()
        if existing:
            task_access.require_task_access(actor,existing)
            return existing,True
        raise
