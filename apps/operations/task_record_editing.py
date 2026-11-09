"""Typed association replacement. Hidden/restricted legacy links are retained."""
from django.core.exceptions import PermissionDenied
from .models import TaskEventLink,TaskParticipantLink,TaskCustomerLink
from .task_access import events_for
from .task_links import participants_for,customers_for

def scopes(actor):
 return {'events':events_for(actor).exclude(status='archived'),
  'participants':participants_for(actor).filter(kind='person',archived_at__isnull=True),
  'customers':customers_for(actor).exclude(status='archived')}

def lock_records(data):
 # Match the collaboration service's parent-before-Task lock order.
 from apps.events.models import Event
 from apps.participants.models import Participant
 from apps.customers.models import CustomerOrganization
 for kind,model in [('events',Event),('participants',Participant),('customers',CustomerOrganization)]:
  values=data.getlist(kind) if hasattr(data,'getlist') else data.get(kind,[])
  if isinstance(values,str):values=[values]
  ids=[]
  for value in list(values)[:50]:
   try:ids.append(model._meta.pk.to_python(value))
   except Exception:pass
  list(model.objects.select_for_update().filter(pk__in=ids).order_by('pk'))

def plan(actor,task,cleaned):
 changes=[];primary=task.event_id
 allowed=scopes(actor)
 for kind,model,field,relation in [('events',TaskEventLink,'event','event_links'),
   ('participants',TaskParticipantLink,'participant','participant_links'),('customers',TaskCustomerLink,'organization','customer_links')]:
  selected={row.pk for row in cleaned[kind]};scope=allowed[kind]
  if scope.filter(pk__in=selected).count()!=len(selected):raise PermissionDenied
  current=set(getattr(task,relation).values_list(field+'_id',flat=True)) if not task._state.adding else set()
  removable=set(scope.filter(pk__in=current).values_list('pk',flat=True))-selected
  if kind=='events' and primary:
   if scope.filter(pk=primary).exists() and primary not in selected:primary=None
   if primary:selected.discard(primary)
  add=selected-current
  if removable or add:changes.append((model,field,removable,add))
 return primary,changes

def apply(task,changes):
 for model,field,remove,add in changes:
  model.objects.filter(task=task,**{field+'_id__in':remove}).delete()
  for pk in sorted(add,key=str):model.objects.get_or_create(task=task,**{field+'_id':pk})
