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
from . import storage, task_access
from .forms import TaskRevisionForm

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
    return task_access.tasks_for(user)
def snap(obj):
    return json.loads(json.dumps({f.attname:getattr(obj,f.attname) for f in obj._meta.fields},cls=DjangoJSONEncoder))
def audit(user,action,obj,before=None,reason=""):
    audit_action(actor=user,action="operations."+action,instance=obj,old_data=before,new_data=snap(obj),reason=reason)

@transaction.atomic
def save_task(*, actor, data, task_id=None, event_id=None):
    task_access.require_task_staff(actor)
    initial = tasks_for(actor).filter(pk=task_id).first() if task_id else None
    if task_id and initial is None:
        raise PermissionDenied
    old_event = initial.event_id if initial else None
    ids = []
    for value in (old_event, data.get("event") or event_id):
        try:
            ids.append(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            pass
    list(Event.objects.select_for_update().filter(pk__in=sorted(set(ids))).order_by("pk"))
    obj = Task.objects.select_for_update().get(pk=task_id) if task_id else Task()
    if task_id:
        task_access.require_task_access(actor, obj)
    revision(obj, data.get("revision"))
    before = snap(obj) if task_id else None
    event = task_access.events_for(actor).filter(pk=event_id).first() if event_id else None
    if event_id and event is None:
        raise PermissionDenied
    form = TaskForm(data, instance=obj, event=event, actor=actor)
    if not form.is_valid():
        return None, form
    profile = StaffProfile.objects.select_for_update().get(pk=form.cleaned_data["assigned_staff"].pk)
    if not before or before["assigned_staff_id"] != str(profile.pk):
        from django.contrib.auth import get_user_model
        account = get_user_model().objects.select_for_update().filter(pk=profile.user_id).first()
        if not profile.is_active or not account or not account.is_active or not account.is_staff:
            form.add_error("assigned_staff", "Choose an active staff login.")
            return None, form
    obj = form.save(commit=False)
    if not task_id:
        obj.created_by = actor
    obj.full_clean()
    meaningful = ("title", "description", "assigned_staff_id", "event_id", "due_date", "priority")
    if before and all(before[key] == snap(obj)[key] for key in meaningful):
        return obj, form
    obj.save()
    action = "task.created" if not before else ("task.reassigned" if before["assigned_staff_id"] != str(obj.assigned_staff_id) else "task.updated")
    audit(actor, action, obj, before, form.cleaned_data["reason"])
    return obj, form

@transaction.atomic
def task_status(*, actor, task_id, data):
    task_access.require_task_staff(actor)
    obj = Task.objects.select_for_update().get(pk=task_id)
    task_access.require_task_access(actor, obj)
    form = StatusForm(data)
    if not form.is_valid():
        return None, form
    revision(obj, form.cleaned_data["revision"])
    status = form.cleaned_data["status"]
    if status == obj.status:
        form.add_error("status", "The task already has this status.")
        return None, form
    before = snap(obj)
    obj.status = status
    obj.completed_at = timezone.now() if status == "completed" else None
    obj.completed_by = actor if status == "completed" else None
    obj.full_clean()
    obj.save()
    action = "task.completed" if status == "completed" else ("task.reopened" if before["status"] == "completed" and status in ("todo", "in_progress") else "task.status")
    audit(actor, action, obj, before, form.cleaned_data["reason"])
    return obj, form

@transaction.atomic
def task_archive(*, actor, task_id, data, restore=False):
    task_access.require_task_staff(actor)
    obj = Task.objects.select_for_update().get(pk=task_id)
    task_access.require_task_access(actor, obj)
    form = TaskRevisionForm(data)
    if not form.is_valid():
        return None, form
    revision(obj, form.cleaned_data["revision"])
    if bool(obj.archived_at) != restore:
        form.add_error(None, "This task is already active." if restore else "This task is already archived.")
        return None, form
    before = snap(obj)
    obj.archived_at = None if restore else timezone.now()
    obj.archived_by = None if restore else actor
    obj.full_clean()
    obj.save()
    audit(actor, "task.restored" if restore else "task.archived", obj, before, form.cleaned_data["reason"])
    return obj, form

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
