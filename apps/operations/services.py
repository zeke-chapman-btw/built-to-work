"""Permission checks, atomic writes and audit history for internal operations."""
import hashlib,json,uuid
from django.conf import settings
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from apps.events.models import Event
from apps.events.services import audit_action
from apps.staffing.models import StaffProfile,StaffAssignment
from apps.staffing.services import require_staff,revision
from .models import Task,EventNote,EventDocument
from .forms import TaskForm,StatusForm,NoteForm,DocumentForm,DemoForm,RevisionForm
from . import storage

def manager(user,kind):
    return bool(user.is_authenticated and user.is_active and user.is_staff and user.has_perm("operations.manage_"+kind) and user.has_perm("events.view_event"))
def require_manager(user,kind):
    require_staff(user)
    if not manager(user,kind):raise PermissionDenied

def person(user):
    require_staff(user)
    return StaffProfile.objects.filter(user=user,is_active=True).first()
def assigned_event(user,event):
    p=person(user)
    return bool(p and event.status not in ("draft","archived","cancelled") and StaffAssignment.objects.filter(event=event,staff=p,status="confirmed").exists())
def event_access(user,event,kind):
    require_staff(user)
    if not (manager(user,kind) or assigned_event(user,event)):raise PermissionDenied

def mutable(event):
    if event and event.status=="archived":raise ValidationError("Archived event operations are read-only. History is retained.")
def tasks_for(user):
    require_staff(user)
    qs=Task.objects.select_related("event","assigned_staff","created_by","completed_by")
    if manager(user,"tasks"):return qs
    p=person(user)
    return qs.filter(assigned_staff=p).exclude(event__status="draft") if p else qs.none()
def snap(obj):
    return json.loads(json.dumps({f.attname:getattr(obj,f.attname) for f in obj._meta.fields},cls=DjangoJSONEncoder))
def audit(user,action,obj,before=None,reason=""):
    audit_action(actor=user,action="operations."+action,instance=obj,old_data=before,new_data=snap(obj),reason=reason)

@transaction.atomic
def save_task(*,actor,data,task_id=None,event_id=None):
    require_manager(actor,"tasks")
    # Lock associated Events in stable order before Task rows, shared with event management.
    old_event=Task.objects.get(pk=task_id).event_id if task_id else None
    target=data.get("event") or event_id
    ids=sorted(set(str(v) for v in (old_event,target) if v))
    if ids:list(Event.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    obj=Task.objects.select_for_update().get(pk=task_id) if task_id else Task()
    mutable(obj.event if obj.event_id else None)
    revision(obj,data.get("revision"))
    before=snap(obj) if task_id else None
    event=Event.objects.get(pk=event_id) if event_id else None
    form=TaskForm(data,instance=obj,event=event)
    if not form.is_valid():return None,form
    mutable(form.cleaned_data["event"])
    staff=StaffProfile.objects.select_for_update().get(pk=form.cleaned_data["assigned_staff"].pk)
    if not staff.is_active and (not task_id or str(staff.pk)!=before["assigned_staff_id"]):raise ValidationError("Choose active staff.")
    obj=form.save(commit=False)
    if not task_id:obj.created_by=actor
    obj.full_clean();obj.save()
    audit(actor,"task.updated" if task_id else "task.created",obj,before,form.cleaned_data["reason"])
    if before and before["assigned_staff_id"]!=str(obj.assigned_staff_id):audit(actor,"task.reassigned",obj,before,form.cleaned_data["reason"])
    return obj,form
@transaction.atomic
def task_status(*,actor,task_id,data):
    require_staff(actor)
    initial=Task.objects.get(pk=task_id)
    if initial.event_id:Event.objects.select_for_update().get(pk=initial.event_id)
    obj=Task.objects.select_for_update().get(pk=task_id)
    is_manager=manager(actor,"tasks")
    if not is_manager and not tasks_for(actor).filter(pk=obj.pk).exists():raise PermissionDenied
    mutable(obj.event if obj.event_id else None)
    form=StatusForm(data,manager=is_manager)
    if not form.is_valid():return None,form
    revision(obj,form.cleaned_data["revision"])
    status=form.cleaned_data["status"]
    if not is_manager and (obj.status not in ("todo","in_progress") or status not in ("in_progress","completed")):raise ValidationError("Only managers can reopen or cancel tasks.")
    if status==obj.status:raise ValidationError("The task already has this status.")
    before=snap(obj);obj.status=status
    obj.completed_at=timezone.now() if status=="completed" else None
    obj.completed_by=actor if status=="completed" else None
    obj.full_clean();obj.save();audit(actor,"task.completed" if status=="completed" else "task.status",obj,before,form.cleaned_data["reason"])
    return obj,form
@transaction.atomic
def save_note(*,actor,event_id,data,note_id=None):
    require_staff(actor)
    event=Event.objects.select_for_update().get(pk=event_id);event_access(actor,event,"event_notes");mutable(event)
    can_manage=manager(actor,"event_notes")
    obj=EventNote.objects.select_for_update().get(pk=note_id,event=event) if note_id else EventNote(event=event)
    if obj.archived_at:raise ValidationError("Archived notes are read-only.")
    if note_id and not can_manage and (obj.author_id!=actor.pk or obj.visibility!="staff"):raise PermissionDenied
    revision(obj,data.get("revision"));before=snap(obj) if note_id else None
    form=NoteForm(data,instance=obj,manager=can_manage)
    if not form.is_valid():return None,form
    obj=form.save(commit=False)
    if not note_id:obj.author=actor
    else:obj.edited_by=actor
    obj.full_clean();obj.save();audit(actor,"note.edited" if note_id else "note.created",obj,before,form.cleaned_data["reason"])
    return obj,form
@transaction.atomic
def create_document(*,actor,event_id,data,upload=None,demo=False):
    require_manager(actor,"event_documents")
    event=Event.objects.select_for_update().get(pk=event_id);mutable(event)
    form=DemoForm(data) if demo else DocumentForm(data,{"file":upload} if upload else {})
    if not form.is_valid():return None,form
    if demo:
        if not (getattr(settings,"DEBUG",False) or getattr(settings,"BTW_PREVIEW_GATE_ENABLED",False)):raise ValidationError("Synthetic document demonstrations are limited to development and gated preview.")
        payload=storage.demo_pdf();filename="SYNTHETIC-BTW-9D-preview.pdf";content_type="application/pdf"
        obj=EventDocument(event=event,title="SYNTHETIC preview document — not real instructions",description="Fixed synthetic test PDF. No uploaded file or permanent storage is implied.",visibility=form.cleaned_data["visibility"],is_demo=True)
    else:
        backend=storage.private_storage()
        filename,content_type,payload=storage.validate_upload(form.cleaned_data["file"])
        obj=form.save(commit=False);obj.event=event
    obj.original_filename=filename;obj.content_type=content_type;obj.file_size=len(payload);obj.sha256=hashlib.sha256(payload).hexdigest();obj.uploaded_by=actor
    key=None
    try:
        if not demo:
            key=backend.save("operational-events/"+str(event.pk)+"/"+uuid.uuid4().hex+{"application/pdf":".pdf","image/png":".png","image/jpeg":".jpg"}[content_type],ContentFile(payload));obj.storage_key=key
        obj.full_clean();obj.save();audit(actor,"document.demo_created" if demo else "document.uploaded",obj,reason=form.cleaned_data["reason"])
    except Exception:
        if key:backend.delete(key)
        raise
    return obj,form
@transaction.atomic
def archive(*,actor,obj_id,kind,data):
    cls=EventNote if kind=="event_notes" else EventDocument
    require_manager(actor,kind)
    event_id=cls.objects.get(pk=obj_id).event_id
    Event.objects.select_for_update().get(pk=event_id)
    obj=cls.objects.select_for_update().get(pk=obj_id);mutable(obj.event)
    form=RevisionForm(data)
    if not form.is_valid():return None,form
    revision(obj,form.cleaned_data["revision"])
    if obj.archived_at:raise ValidationError("This record is already archived.")
    before=snap(obj);obj.archived_at=timezone.now();obj.save();audit(actor,"note.archived" if cls==EventNote else "document.archived",obj,before,form.cleaned_data["reason"])
    return obj,form
