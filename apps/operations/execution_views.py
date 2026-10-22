from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from apps.core.models import AuditLog
from apps.events.models import Event
from .execution_models import (ChecklistMaster,MasterChecklistCategory,MasterChecklistItem,
    EventExecution,EventChecklistCategory,EventChecklistItem)
from .execution_forms import (ManagerForm,ExecutionActionForm,CategoryForm,ItemForm,
    ItemActionForm,MasterCategoryForm,MasterItemForm)
from . import execution_services as svc


def execution_view(fn):
    @login_required(login_url="/admin/login/")
    @require_http_methods(["GET","POST"])
    @wraps(fn)
    def wrapped(request,*args,**kwargs):
        from apps.staffing.services import require_staff
        require_staff(request.user)
        try:
            if kwargs.get("event_id"):
                from zoneinfo import ZoneInfo
                event=get_object_or_404(Event,pk=kwargs["event_id"])
                with timezone.override(ZoneInfo(event.timezone_name)):
                    return fn(request,*args,**kwargs)
            return fn(request,*args,**kwargs)
        except ValidationError as exc:
            return render(request,"operations/execution_notice.html",{"page_title":"Action unavailable",
                "notice":" ".join(exc.messages),"event_id":kwargs.get("event_id")},status=409)
    return wrapped


def setup_link_visible(user,event):
    from apps.internal_ui.event_presentation import event_tools
    from .template_services import can_view_setup
    return user.has_perm("events.view_event") and bool(event_tools(user,event) or can_view_setup(user))

def event_context(request,event_id,section):
    event=get_object_or_404(Event,pk=event_id);svc.require_view(request.user,event)
    execution=EventExecution.objects.select_related("primary_manager__user","closeout_task").filter(event=event).first()
    managing=svc.manager(request.user,execution)
    from .services import tasks_for
    return dict(event=event,execution=execution,section="operations",execution_section=section,
        can_view_setup_link=setup_link_visible(request.user,event),
        can_view_closeout_task=bool(execution and execution.closeout_task_id and tasks_for(request.user).filter(pk=execution.closeout_task_id).exists()),
        page_title={"execution":"Event execution","health":"Event Health Monitor","closeout":"Event Closeout"}[section],
        managing_execution=managing,can_assign_manager=svc.administrator(request.user) and event.status!="archived",
        checklist_writable=bool(execution and managing and not execution.closed_at and event.status!="archived"),
        indicators=svc.progress(execution) if execution else [],
        scheduled_end_overdue=event.status=="upcoming" and event.end_at<timezone.now() and not (execution and execution.actual_ended_at))


def form_page(request,form,*,title,back,event=None,**extra):
    return render(request,"operations/execution_form.html",dict(form=form,page_title=title,
        back_url=back,event=event,section="operations",can_view_setup_link=bool(event and setup_link_visible(request.user,event)),**extra),status=400 if form.is_bound else 200)


@execution_view
def board(request,event_id,section="execution"):
    if request.method!="GET":raise PermissionDenied
    context=event_context(request,event_id,section)
    execution=context["execution"]
    if execution:
        context["history"]=AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(EventExecution),
            object_id=str(execution.pk)).select_related("actor").order_by("-timestamp")[:30]
        present_history(context["history"])
        if section=="health":
            state=request.GET.get("state","all");q=request.GET.get("q","").strip()[:200];phase=request.GET.get("phase","all")
            categories=[]
            for category in execution.categories.all():
                if phase!="all" and category.phase!=phase:continue
                items=category.items.filter(archived_at__isnull=True).select_related("assigned_staff","completed_by")
                if q:items=items.filter(title__icontains=q)
                if state in ("todo","done","na"):items=items.filter(state=state)
                category.visible_items=list(items)
                for item in category.visible_items:
                    item.can_complete=not execution.closed_at and context["event"].status!="archived" and (
                        context["managing_execution"] or bool(item.assigned_staff_id and item.assigned_staff.is_active and item.assigned_staff.user_id==request.user.pk))
                categories.append(category)
            context.update(categories=categories,q=q,selected_state=state,selected_phase=phase,
                archived_items=EventChecklistItem.objects.filter(category__execution=execution,archived_at__isnull=False).select_related("category"))
    return render(request,"operations/"+section+".html",context)


@execution_view
def manager_edit(request,event_id):
    if not svc.administrator(request.user):raise PermissionDenied
    event=get_object_or_404(Event,pk=event_id)
    execution=EventExecution.objects.filter(event=event).first()
    form=ManagerForm(request.POST if request.method=="POST" else None,execution=execution)
    if request.method=="POST":
        try:
            saved,form=svc.set_manager(actor=request.user,event_id=event.pk,data=request.POST)
            if saved:
                messages.success(request,"Primary Event Manager saved. Existing checklist records are retained.")
                return redirect("operations:execution",event_id=event.pk)
        except ValidationError as exc:
            form.is_valid();form.add_error(None,exc)
    return form_page(request,form,title="Primary Event Manager",back=reverse("operations:execution",args=[event.pk]),event=event)


ACTION_LABELS={"end":"Mark Ended","adopt_end":"Record historical end","close":"Formally close out event","reopen":"Reopen closeout"}
@execution_view
def action(request,event_id,action):
    if action not in ACTION_LABELS:raise Http404
    context=event_context(request,event_id,"closeout" if action!="end" else "execution")
    execution=context["execution"]
    if not execution:raise ValidationError("Assign a primary Event Manager and initialize the event checklist first.")
    svc.require_manager(request.user,execution)
    initial={"revision":execution.updated_at.isoformat(),"event_revision":context["event"].updated_at.isoformat()}
    form=ExecutionActionForm(request.POST if request.method=="POST" else None,initial=initial,historical=action=="adopt_end")
    if request.method=="POST":
        try:
            saved,form=svc.event_action(actor=request.user,event_id=event_id,action=action,data=request.POST)
            if saved:
                messages.success(request,ACTION_LABELS[action]+" saved.")
                return redirect("operations:closeout" if action!="end" else "operations:execution",event_id=event_id)
        except ValidationError as exc:
            form.is_valid();form.add_error(None,exc)
    return form_page(request,form,title=ACTION_LABELS[action],back=reverse("operations:closeout" if action!="end" else "operations:execution",args=[event_id]),
        event=context["event"],execution_action=action,submit_label=ACTION_LABELS[action])


@execution_view
def category_edit(request,event_id,category_id=None):
    context=event_context(request,event_id,"health");execution=context["execution"]
    if not execution:raise ValidationError("Initialize this event checklist first.")
    svc.require_manager(request.user,execution);svc.unlocked(execution)
    obj=get_object_or_404(EventChecklistCategory,pk=category_id,execution=execution) if category_id else EventChecklistCategory(execution=execution)
    form=CategoryForm(request.POST if request.method=="POST" else None,instance=obj)
    if request.method=="POST":
        try:
            saved,form=svc.save_category(actor=request.user,event_id=event_id,category_id=category_id,data=request.POST)
            if saved:return redirect("operations:health",event_id=event_id)
        except ValidationError as exc:form.is_valid();form.add_error(None,exc)
    return form_page(request,form,title="Edit checklist category" if category_id else "Add checklist category",back=reverse("operations:health",args=[event_id]),event=context["event"])


@execution_view
def item_edit(request,event_id,item_id=None):
    context=event_context(request,event_id,"health");execution=context["execution"]
    if not execution:raise ValidationError("Initialize this event checklist first.")
    svc.require_manager(request.user,execution);svc.unlocked(execution)
    obj=get_object_or_404(EventChecklistItem,pk=item_id,category__execution=execution) if item_id else EventChecklistItem()
    form=ItemForm(request.POST if request.method=="POST" else None,instance=obj,execution=execution)
    if request.method=="POST":
        try:
            saved,form=svc.save_item(actor=request.user,event_id=event_id,item_id=item_id,data=request.POST)
            if saved:return redirect("operations:health",event_id=event_id)
        except ValidationError as exc:form.is_valid();form.add_error(None,exc)
    return form_page(request,form,title="Edit checklist item" if item_id else "Add checklist item",back=reverse("operations:health",args=[event_id]),event=context["event"])


@execution_view
def item_action(request,event_id,item_id,action):
    context=event_context(request,event_id,"health")
    item=get_object_or_404(EventChecklistItem,pk=item_id,category__execution__event_id=event_id)
    if action=="history":
        if request.method!="GET":raise PermissionDenied
        history=AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(EventChecklistItem),
            object_id=str(item.pk)).select_related("actor").order_by("-timestamp")
        page=Paginator(history,30).get_page(request.GET.get("page"))
        present_history(page.object_list)
        return render(request,"operations/checklist_history.html",{**context,"item":item,"history":page})
    if action not in ("complete","undo","na","archive"):raise Http404
    execution=context["execution"];svc.unlocked(execution)
    can_manage=svc.manager(request.user,execution)
    if not can_manage and not (item.assigned_staff_id and item.assigned_staff.is_active and item.assigned_staff.user_id==request.user.pk):raise PermissionDenied
    if not can_manage and (action in ("na","archive") or item.state=="na"):raise PermissionDenied
    form=ItemActionForm(request.POST if request.method=="POST" else None,initial={"revision":item.updated_at.isoformat()})
    if request.method=="POST":
        try:
            saved,form=svc.item_action(actor=request.user,event_id=event_id,item_id=item_id,action=action,data=request.POST)
            if saved:
                messages.success(request,"Checklist item updated.")
                return redirect("operations:health",event_id=event_id)
        except ValidationError as exc:form.is_valid();form.add_error(None,exc)
    return form_page(request,form,title={"complete":"Complete checklist item","undo":"Reopen checklist item","na":"Mark Not Applicable","archive":"Archive optional checklist item"}[action],
        back=reverse("operations:health",args=[event_id]),event=context["event"],checklist_item=item,submit_label="Confirm change")


@execution_view
def master_list(request):
    if not svc.master_administrator(request.user):raise PermissionDenied
    if request.method!="GET":raise PermissionDenied
    master=get_object_or_404(ChecklistMaster,key="btw")
    q=request.GET.get("q","").strip()[:200]
    categories=list(master.categories.all())
    for category in categories:
        items=category.items.all()
        if q:items=items.filter(title__icontains=q)
        category.visible_items=list(items)
    return render(request,"operations/master_checklist.html",{"page_title":"Master event checklist","master":master,"categories":categories,"q":q})


@execution_view
def master_edit(request,kind,object_id=None):
    if not svc.master_administrator(request.user):raise PermissionDenied
    if kind not in ("category","item"):raise Http404
    master=get_object_or_404(ChecklistMaster,key="btw")
    cls=MasterChecklistCategory if kind=="category" else MasterChecklistItem
    obj=get_object_or_404(cls,pk=object_id,**({"master":master} if kind=="category" else {"category__master":master})) if object_id else cls()
    if kind=="category":obj.master=master
    form=MasterCategoryForm(request.POST if request.method=="POST" else None,instance=obj) if kind=="category" else MasterItemForm(request.POST if request.method=="POST" else None,instance=obj,master=master)
    if request.method=="POST":
        try:
            saved,form=svc.save_master(actor=request.user,data=request.POST,kind=kind,object_id=object_id)
            if saved:
                messages.success(request,"Master updated for future event checklists. Existing event snapshots were not changed.")
                return redirect("operations:master-checklist")
        except ValidationError as exc:form.is_valid();form.add_error(None,exc)
    return form_page(request,form,title=("Edit " if object_id else "Add ")+"master "+kind,
        back=reverse("operations:master-checklist"),master_edit=True)


def present_history(entries):
    from apps.staffing.models import StaffProfile
    actions={"checklist.initialized":"Checklist initialized","manager.changed":"Event Manager changed",
        "ended":"Event marked Ended","closed":"Event formally closed out","reopened":"Closeout reopened",
        "item.saved":"Checklist item updated","item.complete":"Checklist item completed",
        "item.undo":"Checklist item reopened","item.na":"Item marked Not Applicable",
        "item.archive":"Optional item archived"}
    rows=list(entries)
    ids={str(d["assigned_staff_id"]) for entry in rows for d in (entry.old_data or {},entry.new_data or {}) if d.get("assigned_staff_id")}
    names={str(p.pk):str(p) for p in StaffProfile.objects.filter(pk__in=ids)}
    state_labels=dict(EventChecklistItem.State.choices)
    for entry in rows:
        entry.display_action=actions.get(entry.action.removeprefix("event.execution."),"Event checklist updated")
        entry.display_changes=[]
        old,new=entry.old_data or {},entry.new_data or {}
        for key,label in (("title","Item"),("details","Details"),("state","Status"),("assigned_staff_id","Responsible staff"),
            ("is_required","Required"),("position","Order"),("na_reason","N/A reason")):
            if old.get(key)==new.get(key):continue
            def display(value):
                if key=="assigned_staff_id":return names.get(str(value),"Unassigned" if not value else "Former staff profile")
                if key=="state":return state_labels.get(value,value or "Not set")
                if isinstance(value,bool):return "Yes" if value else "No"
                return value if value not in (None,"") else "Not set"
            entry.display_changes.append((label,display(old.get(key)),display(new.get(key))))
    return rows


@execution_view
def execution_index(request):
    if request.method!="GET":raise PermissionDenied
    from .task_access import events_for
    from .presentation import list_page
    events,filters=list_page(request,events_for(request.user).order_by("-start_at","pk"),
        search_fields=("name","location_name","city"),newest_field="-start_at",
        status_field="status",status_choices=Event.Status.choices)
    return render(request,"operations/execution_index.html",{"page_title":"Event execution","events":events,**filters})
