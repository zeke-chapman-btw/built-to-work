"""Explicit, locked and audited event execution; checklist completion is not certification."""
from datetime import datetime
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.utils import timezone
from apps.events.models import Event
from apps.events.services import audit_action
from apps.staffing.models import StaffProfile
from apps.staffing.services import require_staff,revision
from .models import Task
from .services import snap
from .execution_models import (ChecklistMaster,MasterChecklistCategory,MasterChecklistItem,
    EventExecution,EventChecklistCategory,EventChecklistItem)
from .execution_forms import (ManagerForm,ItemForm,CategoryForm,ItemActionForm,
    MasterCategoryForm,MasterItemForm,ExecutionActionForm)


def administrator(user):
    return bool(user.is_authenticated and user.is_active and user.is_staff and (
        user.is_superuser or user.has_perm("operations.manage_event_execution") or
        user.has_perm("events.change_event")))


def master_administrator(user):
    return bool(user.is_authenticated and user.is_active and user.is_staff and (
        user.is_superuser or user.has_perm("operations.manage_master_checklist")))


def manager(user,execution):
    return administrator(user) or bool(execution and execution.primary_manager_id and
        execution.primary_manager.is_active and execution.primary_manager.user_id==user.pk
        and user.is_authenticated and user.is_active and user.is_staff)


def can_view(user,event):
    if not user.is_authenticated or not user.is_active or not user.is_staff:return False
    execution=EventExecution.objects.select_related("primary_manager").filter(event=event).first()
    if user.has_perm("events.view_event") or manager(user,execution):return True
    return event.staff_assignments.filter(status="confirmed",staff__user=user,staff__is_active=True).exists() and event.status not in ("draft","cancelled","archived")


def require_view(user,event):
    if not can_view(user,event):raise PermissionDenied


def require_manager(user,execution):
    require_staff(user)
    if not manager(user,execution):raise PermissionDenied


def unlocked(execution):
    if execution.closed_at:raise ValidationError("This checklist is closed and locked. Reopen closeout with a reason before making corrections.")
    if execution.event.status=="archived":raise ValidationError("Archived events are read-only.")


def audit(user,action,obj,before=None,reason=""):
    audit_action(actor=user,action="event.execution."+action,instance=obj,
        old_data=before,new_data=snap(obj),reason=reason,source="build_10d")


@transaction.atomic
def ensure_execution(*,event,actor=None):
    Event.objects.select_for_update().get(pk=event.pk)
    execution,created=EventExecution.objects.get_or_create(event=event)
    if not created:return execution
    master=ChecklistMaster.objects.select_for_update().filter(key="btw").first()
    if master:
        execution.master_revision=master.updated_at.isoformat();execution.save()
        for category in master.categories.filter(is_active=True):
            copied=EventChecklistCategory.objects.create(execution=execution,source_category=category,
                title=category.title,phase=category.phase,position=category.position)
            EventChecklistItem.objects.bulk_create([
                EventChecklistItem(category=copied,source_item=item,original_title=item.title,
                    title=item.title,details=item.details,is_required=item.is_required,
                    required_from_master=item.is_required,position=item.position)
                for item in category.items.filter(is_active=True)])
    audit(actor,"checklist.initialized",execution,reason="Independent snapshot; future master changes do not overwrite this event.")
    return execution


def lock_execution(event_id):
    event=Event.objects.select_for_update().get(pk=event_id)
    execution=EventExecution.objects.select_for_update(of=("self",)).select_related("primary_manager").get(event=event)
    execution.event=event
    return execution


def _active_person(profile):
    profile=StaffProfile.objects.select_for_update().get(pk=profile.pk)
    user=get_user_model().objects.select_for_update().filter(pk=profile.user_id).first()
    if not profile.is_active or not user or not user.is_active or not user.is_staff:
        raise ValidationError("Choose a staff profile with an active staff login.")
    return profile


def _update_closeout_task(execution,actor,action):
    if not execution.primary_manager_id:raise ValidationError("Assign a primary Event Manager first.")
    profile=_active_person(execution.primary_manager)
    task=Task.objects.select_for_update().filter(pk=execution.closeout_task_id).first() if execution.closeout_task_id else None
    before=snap(task) if task else None
    if task is None:
        task=Task(event=execution.event,assigned_staff=profile,created_by=actor,
            title=("Closeout · "+execution.event.name)[:200],
            description="Complete the event closeout checklist, then record formal closeout in Event Closeout.",
            due_date=timezone.localtime(execution.actual_ended_at,timezone=__import__('zoneinfo').ZoneInfo(execution.event.timezone_name)).date())
    task.assigned_staff=profile
    if execution.closed_at:
        task.status="completed";task.completed_at=execution.closed_at;task.completed_by=execution.closed_by
    else:
        task.status="todo";task.completed_at=None;task.completed_by=None
        task.archived_at=None;task.archived_by=None
    task.full_clean();task.save()
    if execution.closeout_task_id!=task.pk:
        execution.closeout_task=task;execution.save()
    # Deliberately outside operations.task.*: no new notification delivery.
    audit(actor,"task."+action,task,before)
    return task


@transaction.atomic
def set_manager(*,actor,event_id,data):
    if not administrator(actor):raise PermissionDenied
    event=Event.objects.select_for_update().get(pk=event_id)
    if event.status=="archived":raise ValidationError("Archived events are read-only.")
    execution=EventExecution.objects.select_for_update().filter(event=event).first()
    form=ManagerForm(data,execution=execution)
    if not form.is_valid():return None,form
    revision(execution,form.cleaned_data["revision"])
    profile=_active_person(form.cleaned_data["primary_manager"])
    if execution and execution.primary_manager_id and execution.primary_manager_id!=profile.pk and not form.cleaned_data["reason"].strip():
        form.add_error("reason","Record a reason when changing the primary Event Manager.");return None,form
    execution=execution or ensure_execution(event=event,actor=actor)
    before=snap(execution);execution.primary_manager=profile;execution.save()
    if execution.actual_ended_at:_update_closeout_task(execution,actor,"reassigned")
    audit(actor,"manager.changed",execution,before,form.cleaned_data["reason"])
    return execution,form


def prepare_end(*,event,actor,data):
    execution=EventExecution.objects.select_related("primary_manager__user").filter(event=event).first()
    if execution is None or execution.primary_manager_id is None:
        if not administrator(actor):raise PermissionDenied
        saved,form=set_manager(actor=actor,event_id=event.pk,data={
            "revision":execution.updated_at.isoformat() if execution else "new",
            "primary_manager":data.get("primary_manager",""),"reason":data.get("reason","")})
        if saved is None:raise ValidationError("Choose a primary Event Manager with an active staff login before ending the event.")
        execution=saved
    require_current_manager(actor,execution)
    _active_person(execution.primary_manager)
    return execution


@transaction.atomic
def record_ended(*,event,actor,actual_at=None,reason=""):
    execution=EventExecution.objects.select_for_update().get(event=event)
    if execution.actual_ended_at:return execution
    before=snap(execution);execution.actual_ended_at=actual_at or timezone.now();execution.ended_by=actor
    execution.save();_update_closeout_task(execution,actor,"created")
    audit(actor,"ended",execution,before,reason)
    return execution


@transaction.atomic
def event_action(*,actor,event_id,action,data):
    execution=lock_execution(event_id);require_current_manager(actor,execution)
    form=ExecutionActionForm(data,historical=action=="adopt_end")
    if not form.is_valid():return None,form
    revision(execution,form.cleaned_data["revision"])
    if action=="end":
        unlocked(execution)
        from apps.events.event_management import transition_event
        event,other=transition_event(event_id=event_id,action="complete",actor=actor,data={
            "revision":data.get("event_revision",""),"confirm":"on","reason":form.cleaned_data["reason"]})
        if event is None:
            form.add_error(None,ValidationError([str(x) for values in other.errors.values() for x in values]));return None,form
        execution.refresh_from_db()
    elif action=="adopt_end":
        unlocked(execution)
        if execution.event.status!="completed" or execution.actual_ended_at:raise ValidationError("Only an existing Completed event without a recorded actual end can use this action.")
        try:
            from zoneinfo import ZoneInfo
            from apps.staffing.calendar import local_instant
            raw=datetime.fromisoformat(data.get("actual_end",""))
            actual=raw if timezone.is_aware(raw) else local_instant(raw.date(),raw.time(),ZoneInfo(execution.event.timezone_name))
        except (ValueError,TypeError):raise ValidationError("Enter the actual historical event end in the event timezone.")
        if actual>timezone.now():raise ValidationError("An actual end cannot be in the future.")
        if not form.cleaned_data["reason"].strip():raise ValidationError("Record the source or reason for this historical end.")
        record_ended(event=execution.event,actor=actor,actual_at=actual,reason=form.cleaned_data["reason"]);execution.refresh_from_db()
    elif action=="close":
        unlocked(execution)
        if execution.event.status!="completed" or not execution.actual_ended_at:raise ValidationError("Mark the event Ended before formal closeout.")
        if EventChecklistItem.objects.filter(category__execution=execution,category__phase="closeout",
                archived_at__isnull=True,is_required=True,state="todo").exists():
            raise ValidationError("Complete applicable required Closeout items, or mark them Not Applicable with a reason.")
        before=snap(execution);execution.closed_at=timezone.now();execution.closed_by=actor;execution.save()
        _update_closeout_task(execution,actor,"completed");audit(actor,"closed",execution,before,form.cleaned_data["reason"])
    elif action=="reopen":
        if not execution.closed_at or execution.event.status=="archived":raise ValidationError("Only a closed, non-archived event checklist can be reopened.")
        if not form.cleaned_data["reason"].strip():
            form.add_error("reason","Record why closeout is being reopened.");return None,form
        before=snap(execution);execution.closed_at=None;execution.closed_by=None
        execution.last_reopened_at=timezone.now();execution.last_reopened_by=actor;execution.save()
        _update_closeout_task(execution,actor,"reopened");audit(actor,"reopened",execution,before,form.cleaned_data["reason"])
    else:raise ValidationError("Unsupported event execution action.")
    return execution,form


@transaction.atomic
def save_category(*,actor,event_id,data,category_id=None):
    execution=lock_execution(event_id);require_current_manager(actor,execution);unlocked(execution)
    category=execution.categories.select_for_update().get(pk=category_id) if category_id else EventChecklistCategory(execution=execution)
    revision(category if category_id else None,data.get("revision"))
    before=snap(category) if category_id else None
    form=CategoryForm(data,instance=category)
    if not form.is_valid():return None,form
    if category_id and form.cleaned_data["phase"]!=before["phase"] and category.items.filter(required_from_master=True).exists():
        form.add_error("phase","A category containing required master items keeps its original phase.");return None,form
    saved=form.save();execution.save();audit(actor,"category.saved",saved,before,form.cleaned_data["reason"])
    return saved,form


@transaction.atomic
def save_item(*,actor,event_id,data,item_id=None):
    execution=lock_execution(event_id);require_current_manager(actor,execution);unlocked(execution)
    item=EventChecklistItem.objects.select_for_update().get(pk=item_id,category__execution=execution) if item_id else EventChecklistItem()
    revision(item if item_id else None,data.get("revision"))
    before=snap(item) if item_id else None
    if item.archived_at:raise ValidationError("Archived checklist items retain their history and cannot be edited.")
    form=ItemForm(data,instance=item,execution=execution)
    if not form.is_valid():return None,form
    profile=form.cleaned_data.get("assigned_staff")
    if profile and (not before or str(profile.pk)!=before.get("assigned_staff_id")):_active_person(profile)
    saved=form.save();execution.save();audit(actor,"item.saved",saved,before,form.cleaned_data["reason"])
    return saved,form


@transaction.atomic
def item_action(*,actor,event_id,item_id,action,data):
    execution=lock_execution(event_id);require_view(actor,execution.event);unlocked(execution)
    item=EventChecklistItem.objects.select_for_update(of=("self",)).select_related("assigned_staff").get(pk=item_id,category__execution=execution)
    can_manage=manager(actor,execution)
    if can_manage:require_current_manager(actor,execution)
    elif item.assigned_staff_id:
        profile=_active_person(item.assigned_staff)
        if profile.user_id!=actor.pk:raise PermissionDenied
    if not can_manage and not (item.assigned_staff_id and item.assigned_staff.is_active and item.assigned_staff.user_id==actor.pk):raise PermissionDenied
    if action not in ("complete","undo","na","archive"):raise ValidationError("Unsupported checklist action.")
    if not can_manage and (action in ("na","archive") or item.state=="na"):raise PermissionDenied
    if item.archived_at:raise ValidationError("This item is archived.")
    form=ItemActionForm(data)
    if not form.is_valid():return None,form
    revision(item,form.cleaned_data["revision"]);reason=form.cleaned_data["reason"].strip()
    if action in ("na","archive") and not reason:
        form.add_error("reason","Record a reason for this change.");return None,form
    if action=="archive" and item.required_from_master:raise ValidationError("Required master items cannot be removed. Mark Not Applicable with a reason instead.")
    before=snap(item)
    if action=="archive":item.archived_at=timezone.now()
    else:
        target={"complete":"done","undo":"todo","na":"na"}[action]
        if item.state==target:raise ValidationError("This item already has that state.")
        item.state=target;item.na_reason=reason if action=="na" else ""
        item.completed_at=timezone.now() if action=="complete" else None
        item.completed_by=actor if action=="complete" else None
    item.full_clean();item.save();execution.save()
    audit(actor,"item."+action,item,before,reason)
    return item,form


@transaction.atomic
def save_master(*,actor,data,kind,object_id=None):
    if not master_administrator(actor):raise PermissionDenied
    master=ChecklistMaster.objects.select_for_update().get(key="btw")
    cls=MasterChecklistCategory if kind=="category" else MasterChecklistItem
    obj=cls.objects.select_for_update().get(pk=object_id,**({"master":master} if kind=="category" else {"category__master":master})) if object_id else cls()
    if kind=="category":obj.master=master
    revision(obj if object_id else None,data.get("revision"));before=snap(obj) if object_id else None
    form=MasterCategoryForm(data,instance=obj) if kind=="category" else MasterItemForm(data,instance=obj,master=master)
    if not form.is_valid():return None,form
    saved=form.save();master.save();audit(actor,"master."+kind+".saved",saved,before,form.cleaned_data["reason"])
    return saved,form


def progress(execution):
    rows=list(EventChecklistItem.objects.filter(category__execution=execution,archived_at__isnull=True).select_related("category"))
    indicators=[]
    for key,label,phases in (("ready","Ready to Go?",("prepare","arrival")),("live","Live Operations",("live",)),("closeout","Closeout",("closeout",))):
        selected=[x for x in rows if x.category.phase in phases]
        applicable=[x for x in selected if x.state!="na"]
        complete=sum(x.state=="done" for x in applicable)
        indicators.append(dict(key=key,label=label,total=len(applicable),completed=complete,
            na=sum(x.state=="na" for x in selected),percent=(100 if complete==len(applicable) else int(complete*100/len(applicable))) if applicable else None))
    return indicators


def guard_managed_task(task):
    if EventExecution.objects.filter(closeout_task=task).exists():
        raise ValidationError("This is an event closeout task. Manage its assignment and completion from Event Closeout; it stays open until formal closeout.")

def can_end_event(user,event_id):
    if not user.is_authenticated or not user.is_active or not user.is_staff:return False
    return administrator(user) or EventExecution.objects.filter(event_id=event_id,
        primary_manager__user=user,primary_manager__is_active=True).exists()


def end_reminder_candidates():
    """Data for future reminders only; this function never ends events or delivers notifications."""
    from django.db.models import Q
    return Event.objects.filter(status="upcoming",end_at__lt=timezone.now()).filter(
        Q(execution__isnull=True)|Q(execution__actual_ended_at__isnull=True))

def require_current_manager(user,execution):
    """Write-side recheck under profile/account locks; GET authorization remains read-only."""
    require_manager(user,execution)
    if not administrator(user):
        profile=_active_person(execution.primary_manager)
        if profile.user_id!=user.pk:raise PermissionDenied
