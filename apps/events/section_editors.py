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
 'event':('Event details',('name','event_type','event_type_other','description','simulator_qualification_threshold')),
 'location':('Location',('location_name','address_line_1','address_line_2','city','state','postal_code','country')),
 'schedule':('Schedule',('timezone_name','setup_date','staff_arrival_time','start_at','end_at','daily_start_time','daily_end_time','return_date')),
 'contact':('Event contact',('contact_name','contact_email','contact_phone')),
 'housing':('Housing notes',('housing_information',)),
 'logistics':('Travel & setup',('logistics_instructions',)),
 'purpose':('Purpose',('purpose','customer','campaign')),
 'signup':('Participant sign-up',('registration_opens_at','registration_closes_at','registration_deadline_at','registration_capacity','group_mode','group_label')),
}

# Plain labels and help for the pop-up editors. Validation is unchanged.
LABELS={
 'name':('Event name',''),'event_type':('Event type',''),'event_type_other':('Describe the event type',''),
 'simulator_qualification_threshold':('Simulator passing score (%)','Scores at or above this count as qualified. Usually 50.'),
 'location_name':('Venue',''),'address_line_1':('Street address',''),'address_line_2':('Address line 2',''),'postal_code':('ZIP code',''),
 'timezone_name':('Time zone','Every time on this event uses this time zone.'),
 'setup_date':('Setup day',''),'staff_arrival_time':('Staff arrive (setup day)',''),
 'start_at':('Event starts','Tickets and trailer stations work between the start and end.'),'end_at':('Event ends',''),
 'daily_start_time':('Staff start (each day)','Used when a staff assignment has no times of its own.'),'daily_end_time':('Staff end (each day)',''),
 'return_date':('Arrive home',''),
 'contact_name':('Contact name',''),'contact_email':('Email',''),'contact_phone':('Phone',''),
 'housing_information':('Housing notes','For anything not covered by a housing entry.'),
 'logistics_instructions':('Travel & setup notes','Directions, parking, load-in, what to bring.'),
 'registration_opens_at':('Sign-up opens','Leave blank to open right away.'),'registration_closes_at':('Sign-up closes','Leave blank to stay open until the event ends.'),
 'registration_deadline_at':('Last day to sign up','Optional. Use if sign-up should stop before it closes.'),
 'registration_capacity':('Sign-up limit','Leave blank for no limit.'),
 'group_mode':('Ask for a group','For example, which school or company they came with.'),'group_label':('Group name','What to call the group, such as School.'),
}

class SectionForm(forms.Form):
 revision=forms.CharField(widget=forms.HiddenInput)
 def __init__(self,*args,event,section,**kwargs):
  super().__init__(*args,**kwargs)
  full=EventForm(instance=event)
  self.initial['revision']=event.updated_at.isoformat()
  for name in SECTIONS[section][1]:
   self.fields[name]=deepcopy(full.fields[name]);self.initial[name]=full[name].value()
   label,help_text=LABELS.get(name,(None,None))
   if label:self.fields[name].label=label
   if help_text is not None:self.fields[name].help_text=help_text

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


QUICK_FIELDS=('name','event_type','event_type_other','timezone_name','start_at','end_at','location_name','city','state')


class QuickCreateForm(forms.Form):
 quick_create=forms.CharField(widget=forms.HiddenInput,initial='1')
 def __init__(self,*args,**kwargs):
  super().__init__(*args,**kwargs)
  full=EventForm()
  for name in QUICK_FIELDS:
   self.fields[name]=deepcopy(full.fields[name]);self.initial.setdefault(name,full[name].value())
   label,help_text=LABELS.get(name,(None,None))
   if label:self.fields[name].label=label
   if help_text is not None:self.fields[name].help_text=help_text
  self.fields['start_at'].help_text='You can add setup days, staff, housing and everything else after it is created.'
  for name in ('location_name','city','state'):self.fields[name].required=False


def quick_create(request):
 """New events start from a short pop-up; every other detail is edited from the event's cards."""
 from django.urls import reverse
 from .event_management import save_event
 actor=fresh_staff(request.user);require_event_permission(actor,'events.add_event')
 back=event_return(request)
 form=QuickCreateForm(request.POST if request.method=='POST' else None)
 if request.method=='POST' and form.is_valid():
  full=EventForm()
  payload={name:(full[name].value() if full[name].value() is not None else '') for name in full.fields}
  payload.update({name:request.POST.get(name,'') for name in QUICK_FIELDS})
  payload.update(action='save_draft',revision='')
  event,checked=save_event(data=payload,actor=actor)
  if event:return editor_success(request,reverse('events:event_detail',args=[event.pk]))
  for key,errors in checked.errors.items():form.add_error(key if key in form.fields else None,errors)
 return editor_response(request,template='events/section_form.html',panel='internal/ui/record_form_panel.html',context={
  'form':form,'page_title':'New event','editor_context':'Saved as a draft. Add the rest from the event page.','back':back,
  'save_label':'Create event','editor_return_label':'events','fields_template':'internal/ui/section_fields.html',
  'full_page_editor':request.headers.get('X-BTW-Editor')!='1'})
