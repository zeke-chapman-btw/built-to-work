from functools import wraps
import hashlib
from io import BytesIO
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse,Http404
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from apps.events.models import Event
from apps.staffing.models import StaffProfile
from .models import Task,EventNote,EventDocument
from .forms import TaskForm,StatusForm,NoteForm,DocumentForm,DemoForm,RevisionForm
from . import services as svc,storage

def staff_view(fn):
    @login_required(login_url="/admin/login/")
    @require_http_methods(["GET","POST"])
    @wraps(fn)
    def wrapped(request,*args,**kwargs):
        svc.require_staff(request.user)
        try:return fn(request,*args,**kwargs)
        except ValidationError as exc:return render(request,"operations/notice.html",{"page_title":"Action unavailable","notice":" ".join(exc.messages)},status=409)
    return wrapped

def form_page(request,form,title,back):
    return render(request,"operations/form.html",dict(form=form,page_title=title,back=back),status=400 if form.is_bound else 200)
@staff_view
def task_list(request,mine=False):
    if request.method!="GET":raise PermissionDenied
    managing=svc.manager(request.user,"tasks");qs=svc.tasks_for(request.user)
    view=request.GET.get("view","open")
    if mine:
        p=svc.person(request.user);qs=qs.filter(assigned_staff=p) if p else qs.none()
    if view not in ("all","mine","open","completed","overdue"):view="open"
    p=svc.person(request.user)
    if view=="mine":qs=qs.filter(assigned_staff=p) if p else qs.none()
    if view=="open":qs=qs.filter(status__in=("todo","in_progress"))
    if view=="completed":qs=qs.filter(status="completed")
    if view=="overdue":qs=qs.filter(status__in=("todo","in_progress"),due_date__lt=timezone.localdate())
    filters={k:request.GET.get(k,"").strip()[:200] for k in ("q","status","priority","assignee","event")}
    if filters["q"]:qs=qs.filter(Q(title__icontains=filters["q"])|Q(description__icontains=filters["q"]))
    for field,choices in (("status",Task.Status.values),("priority",Task.Priority.values)):
        if filters[field] in choices:qs=qs.filter(**{field:filters[field]})
    if managing:
        for field,key in (("assignee","assigned_staff_id"),("event","event_id")):
            if filters[field]:
                try:qs=qs.filter(**{key:filters[field]})
                except (ValidationError,ValueError):qs=qs.none()
    query=request.GET.copy();query.pop("page",None)
    return render(request,"operations/tasks.html",dict(page_title="Tasks" if managing and not mine else "My Tasks",managing=managing,tasks=Paginator(qs.order_by("due_date","-created_at"),20).get_page(request.GET.get("page")),view=view,filters=filters,statuses=Task.Status.choices,priorities=Task.Priority.choices,people=StaffProfile.objects.all() if managing else [],events=Event.objects.all() if managing else [],query=query.urlencode(),today=timezone.localdate()))
@staff_view
def task_detail(request,task_id):
    if request.method!="GET":raise PermissionDenied
    obj=get_object_or_404(svc.tasks_for(request.user),pk=task_id)
    managing=svc.manager(request.user,"tasks")
    return render(request,"operations/task.html",dict(page_title=obj.title,task=obj,managing=managing,can_change=managing or obj.status in ("todo","in_progress")))
@staff_view
def task_edit(request,task_id=None,event_id=None):
    svc.require_manager(request.user,"tasks")
    obj=get_object_or_404(Task,pk=task_id) if task_id else Task()
    event=get_object_or_404(Event,pk=event_id) if event_id else None
    svc.mutable(event or (obj.event if obj.event_id else None));form=TaskForm(instance=obj,event=event)
    if request.method=="POST":
        saved,form=svc.save_task(actor=request.user,data=request.POST,task_id=task_id,event_id=event_id)
        if saved:messages.success(request,"Task saved.");return redirect("operations:task",task_id=saved.pk)
    return form_page(request,form,"Edit Task" if task_id else "Create Task",reverse("operations:tasks"))
@staff_view
def task_status(request,task_id):
    obj=get_object_or_404(svc.tasks_for(request.user),pk=task_id)
    form=StatusForm(manager=svc.manager(request.user,"tasks"),initial={"revision":obj.updated_at.isoformat()})
    if request.method=="POST":
        saved,form=svc.task_status(actor=request.user,task_id=obj.pk,data=request.POST)
        if saved:messages.success(request,"Task status updated.");return redirect("operations:task",task_id=obj.pk)
    return form_page(request,form,"Update task status",reverse("operations:task",args=[obj.pk]))

KINDS={"tasks":"tasks","notes":"event_notes","documents":"event_documents"}
@staff_view
def event_operations(request,event_id,section="tasks"):
    if section not in KINDS:raise Http404
    event=get_object_or_404(Event,pk=event_id);kind=KINDS[section];svc.event_access(request.user,event,kind)
    managing=svc.manager(request.user,kind)
    notes=event.operational_notes.select_related("author","edited_by")
    docs=event.operational_documents.select_related("uploaded_by")
    if not managing:notes=notes.filter(visibility="staff",archived_at__isnull=True);docs=docs.filter(visibility="staff",archived_at__isnull=True)
    form=NoteForm(manager=managing)
    if request.method=="POST":
        if section!="notes":raise PermissionDenied
        saved,form=svc.save_note(actor=request.user,event_id=event.pk,data=request.POST)
        if saved:messages.success(request,"Event note added.");return redirect("operations:event",event_id=event.pk,section="notes")
    tabs=[(name,reverse("operations:event",args=[event.pk,name])) for name,k in KINDS.items() if svc.manager(request.user,k) or svc.assigned_event(request.user,event)]
    back=reverse("events:event_detail",args=[event.pk]) if request.user.has_perm("events.view_event") else reverse("staffing:staff-event",args=[event.pk])
    return render(request,"operations/event.html",dict(page_title=event.name,event=event,section=section,tabs=tabs,back=back,managing=managing,can_write=event.status!="archived",tasks=Paginator(svc.tasks_for(request.user).filter(event=event),20).get_page(request.GET.get("page")),notes=Paginator(notes,30).get_page(request.GET.get("page")),documents=Paginator(docs,20).get_page(request.GET.get("page")),form=form,uploads_available=storage.uploads_available(),demo_available=settings.DEBUG or getattr(settings,"BTW_PREVIEW_GATE_ENABLED",False)),status=400 if form.is_bound else 200)
@staff_view
def events_index(request):
    if request.method!="GET":raise PermissionDenied
    p=svc.person(request.user)
    if any(svc.manager(request.user,k) for k in KINDS.values()):qs=Event.objects.all()
    else:qs=Event.objects.filter(staff_assignments__staff=p,staff_assignments__status="confirmed").exclude(status__in=("draft","cancelled","archived")) if p else Event.objects.none()
    q=request.GET.get("q","").strip()[:200]
    if q:qs=qs.filter(name__icontains=q)
    page=Paginator(qs.distinct().order_by("-start_at"),20).get_page(request.GET.get("page"))
    rows=[]
    for e in page:
        rows.append((e,[(name,reverse("operations:event",args=[e.pk,name])) for name,k in KINDS.items() if svc.manager(request.user,k) or svc.assigned_event(request.user,e)]))
    return render(request,"operations/events.html",dict(page_title="Event operations",rows=rows,q=q,events=page,query="q="+__import__("urllib.parse",fromlist=["quote"]).quote(q)))
@staff_view
def note_edit(request,note_id):
    obj=get_object_or_404(EventNote,pk=note_id);svc.event_access(request.user,obj.event,"event_notes")
    managing=svc.manager(request.user,"event_notes")
    if not managing and (obj.author_id!=request.user.pk or obj.visibility!="staff"):raise PermissionDenied
    svc.mutable(obj.event)
    form=NoteForm(instance=obj,manager=managing)
    if request.method=="POST":
        saved,form=svc.save_note(actor=request.user,event_id=obj.event_id,note_id=obj.pk,data=request.POST)
        if saved:messages.success(request,"Note corrected; prior content remains in the audit history.");return redirect("operations:event",event_id=obj.event_id,section="notes")
    return form_page(request,form,"Edit event note",reverse("operations:event",args=[obj.event_id,"notes"]))
@staff_view
def document_upload(request,event_id,demo=False):
    svc.require_manager(request.user,"event_documents");event=get_object_or_404(Event,pk=event_id);svc.mutable(event)
    if not demo and not storage.uploads_available():raise ValidationError("Real uploads are unavailable until private durable operational storage is configured. Use the clearly labeled synthetic demonstration on gated preview.")
    form=DemoForm() if demo else DocumentForm()
    if request.method=="POST":
        saved,form=svc.create_document(actor=request.user,event_id=event.pk,data=request.POST,upload=request.FILES.get("file"),demo=demo)
        if saved:messages.success(request,"Synthetic preview document created." if demo else "Private operational document uploaded.");return redirect("operations:event",event_id=event.pk,section="documents")
    return form_page(request,form,"Create synthetic preview document" if demo else "Upload operational document",reverse("operations:event",args=[event.pk,"documents"]))
@staff_view
def archive(request,obj_id,kind):
    if kind not in ("event_notes","event_documents"):raise Http404
    svc.require_manager(request.user,kind)
    obj=get_object_or_404(EventNote if kind=="event_notes" else EventDocument,pk=obj_id)
    form=RevisionForm(initial={"revision":obj.updated_at.isoformat()})
    if request.method=="POST":
        saved,form=svc.archive(actor=request.user,obj_id=obj.pk,kind=kind,data=request.POST)
        if saved:messages.success(request,"Archived. History and audit records are retained.");return redirect("operations:event",event_id=obj.event_id,section="notes" if kind=="event_notes" else "documents")
    return form_page(request,form,"Archive operational record",reverse("operations:event",args=[obj.event_id,"notes" if kind=="event_notes" else "documents"]))
@staff_view
def download(request,document_id):
    if request.method!="GET":raise PermissionDenied
    obj=get_object_or_404(EventDocument,pk=document_id);svc.event_access(request.user,obj.event,"event_documents")
    if not svc.manager(request.user,"event_documents") and (obj.visibility!="staff" or obj.archived_at):raise PermissionDenied
    try:
        if obj.is_demo:payload=storage.demo_pdf()
        else:
            with storage.private_storage().open(obj.storage_key,"rb") as stream:payload=stream.read(10*1024*1024+1)
    except (OSError,ValueError):raise Http404("Document bytes are unavailable; metadata is retained.")
    if len(payload)!=obj.file_size or hashlib.sha256(payload).hexdigest()!=obj.sha256:raise Http404("Document integrity check failed.")
    svc.audit(request.user,"document.downloaded",obj)
    response=FileResponse(BytesIO(payload),as_attachment=True,filename=obj.original_filename,content_type="application/octet-stream")
    response["X-Content-Type-Options"]="nosniff";response["Content-Security-Policy"]="sandbox; default-src 'none'";response["Cache-Control"]="private, no-store"
    return response
