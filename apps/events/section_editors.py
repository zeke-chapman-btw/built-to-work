"""Focused Event edits reuse the locked, audited EventForm/save_event contract."""
from copy import deepcopy
from django import forms
from django.db import transaction
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods
from apps.crm.access import fresh_staff
from apps.internal_ui.record_editors import event_return, editor_response, editor_success
from .forms import EventForm
from .models import Event
from .event_management import save_event, require_event_permission
from .views import staff_permission_required

SECTIONS={
 'event':('Event & location',('name','event_type','event_type_other','description','location_name','address_line_1','address_line_2','city','state','postal_code','country')),
 'schedule':('Schedule',('timezone_name','start_at','end_at','daily_start_time','daily_end_time','setup_date','staff_arrival_time','return_date')),
 'contact':('Event contact',('contact_name','contact_email','contact_phone')),
 'housing':('Housing',('housing_information',)),
 'logistics':('Travel & setup',('logistics_instructions',)),
}

class SectionForm(forms.Form):
 revision=forms.CharField(widget=forms.HiddenInput)
 def __init__(self,*args,event,section,**kwargs):
  super().__init__(*args,**kwargs)
  full=EventForm(instance=event)
  self.initial['revision']=event.updated_at.isoformat()
  for name in SECTIONS[section][1]:
   self.fields[name]=deepcopy(full.fields[name]);self.initial[name]=full[name].value()

@transaction.atomic
def save_section(*,actor,event_id,section,data):
 actor=fresh_staff(actor);require_event_permission(actor,'events.change_event')
 if section not in SECTIONS:raise Http404
 event=Event.objects.select_for_update().get(pk=event_id)
 form=SectionForm(data,event=event,section=section)
 if not form.is_valid():return None,form
 if data.get('revision')!=event.updated_at.isoformat():
  form.add_error(None,'This event changed. Reload before saving.');return None,form
 if set(data)-set(form.fields)-{'csrfmiddlewaretoken','return_to'}:
  form.add_error(None,'Only fields in this section may be submitted.');return None,form
 # Build the existing full validator from the latest locked record. Nothing is
 # copied from a stale browser outside this section; operating-window and staff
 # schedule checks remain in the established service.
 full=EventForm(instance=event)
 payload={name:full[name].value() if full[name].value() is not None else '' for name in full.fields}
 payload.update({name:data.get(name,'') for name in SECTIONS[section][1]})
 payload.update(revision=data['revision'],action='save')
 saved,checked=save_event(actor=actor,event_id=event.pk,data=payload)
 if not saved:
  for key,errors in checked.errors.items():form.add_error(key if key in form.fields else None,errors)
 return saved,form

@staff_permission_required('events.view_event','events.change_event')
@require_http_methods(['GET','POST'])
def edit(request,event_id,section):
 if section not in SECTIONS:raise Http404
 event=get_object_or_404(Event,pk=event_id);back=event_return(request,event)
 form=SectionForm(request.POST if request.method=='POST' else None,event=event,section=section)
 if request.method=='POST':
  saved,form=save_section(actor=request.user,event_id=event.pk,section=section,data=request.POST)
  if saved:return editor_success(request,back)
 conflict=any('Reload' in str(error) for errors in form.errors.values() for error in errors)
 return editor_response(request,template='events/section_form.html',panel='internal/ui/record_form_panel.html',context={
  'event':event,'form':form,'page_title':'Edit '+SECTIONS[section][0],'editor_context':event.name,'back':back,
  'save_label':'Save changes','save_conflict':conflict,'cautious_save':True,'editor_return_label':'event',
  'fields_template':'internal/ui/section_fields.html','full_page_editor':request.headers.get('X-BTW-Editor')!='1'})
