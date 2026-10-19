"""All staffing writes are permission-checked, locked and audited here."""
from datetime import date
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Count
from django.utils import timezone
from apps.events.models import Event
from apps.events.services import audit_action
from .models import StaffProfile, StaffingPlan, DailyStaffingNeed, AvailabilityRequest, StaffAssignment, AssignmentDay
from .calendar import event_days, day_window
from .forms import ProfileForm, AccountLinkForm, PlanForm, AvailabilityForm, AssignmentForm, DecisionForm


def require_staff(user):
    if not user.is_authenticated or not user.is_active or not user.is_staff: raise PermissionDenied


def require_perm(user,permission):
    require_staff(user)
    if not user.has_perm(permission): raise PermissionDenied


def require_manager(user):
    require_perm(user,"staffing.manage_event_staffing")
    require_perm(user,"events.view_event")


def own_profile(user):
    require_staff(user)
    return StaffProfile.objects.filter(user=user,is_active=True).first()


def mutable(event):
    if event.status not in ("draft","upcoming"):
        raise ValidationError("Staffing changes are closed for completed, cancelled and archived events. History is retained.")


def revision(obj,value):
    current=obj.updated_at.isoformat() if obj and obj.updated_at else "new"
    if value!=current: raise ValidationError("This record changed after you opened it. Reload and review the latest version.")


def snapshot(obj):
    result={}
    for field in obj._meta.fields:
        value=getattr(obj,field.attname)
        result[field.attname]=value.isoformat() if hasattr(value,"isoformat") else str(value) if field.is_relation and value is not None else value
    if isinstance(obj,StaffAssignment):
        result["days"]=[{k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in row.items()} for row in obj.days.values("date","starts_at","ends_at","arrival_at")]
    import json
    from django.core.serializers.json import DjangoJSONEncoder
    return json.loads(json.dumps(result, cls=DjangoJSONEncoder))


def audit(actor,action,obj,before=None,reason=""):
    audit_action(actor=actor,action="staffing."+action,instance=obj,old_data=before,new_data=snapshot(obj),reason=reason)


@transaction.atomic
def save_profile(*,actor,data,profile_id=None):
    require_perm(actor,"staffing.change_staffprofile" if profile_id else "staffing.add_staffprofile")
    profile=StaffProfile.objects.select_for_update().get(pk=profile_id) if profile_id else StaffProfile()
    before=snapshot(profile) if profile_id else None
    if profile_id: revision(profile,data.get("revision"))
    form=ProfileForm(data,instance=profile)
    if not form.is_valid(): return None,form
    profile=form.save(); audit(actor,"profile.updated" if profile_id else "profile.created",profile,before)
    return profile,form


@transaction.atomic
def link_account(*,actor,profile_id,data):
    require_perm(actor,"staffing.link_staff_account"); require_perm(actor,"staffing.change_staffprofile")
    profile=StaffProfile.objects.select_for_update().get(pk=profile_id)
    form=AccountLinkForm(data,profile=profile)
    if not form.is_valid(): return None,form
    revision(profile,form.cleaned_data["revision"])
    user=form.cleaned_data["user"]
    if user:
        # Serialize concurrent attempts to link the same account. No identity/permissions change.
        type(user).objects.select_for_update().get(pk=user.pk)
        if StaffProfile.objects.exclude(pk=profile.pk).filter(user=user).exists(): raise ValidationError("This account is already linked to another staff profile.")
    before=snapshot(profile); profile.user=user; profile.full_clean(); profile.save(); audit(actor,"profile.account_link",profile,before)
    return profile,form


@transaction.atomic
def save_plan(*,actor,event_id,data):
    require_manager(actor)
    event=Event.objects.select_for_update().get(pk=event_id); mutable(event)
    plan=StaffingPlan.objects.filter(event=event).first() or StaffingPlan(event=event)
    revision(plan,data.get("revision")); before=snapshot(plan) if not plan._state.adding else None
    form=PlanForm(data,event=event,plan=plan)
    if not form.is_valid(): return None,form
    for name in ("required_staff","is_published","signup_deadline"): setattr(plan,name,form.cleaned_data[name])
    plan.full_clean(); plan.save()
    old_needs=list(plan.daily_needs.values("date","required_staff"))
    for day in event_days(event): DailyStaffingNeed.objects.update_or_create(plan=plan,date=day,defaults={"required_staff":form.cleaned_data["need_"+day.isoformat()]})
    audit(actor,"plan.updated",plan,before)
    audit_action(actor=actor,action="staffing.daily_needs.updated",instance=plan,old_data={"days":[dict(date=n["date"].isoformat(),required_staff=n["required_staff"]) for n in old_needs]},new_data={"days":[dict(date=d.isoformat(),required_staff=form.cleaned_data["need_"+d.isoformat()]) for d in event_days(event)]})
    return plan,form


def opportunity_open(event,plan):
    return bool(plan and plan.is_published and event.status=="upcoming" and event.end_at>timezone.now() and (plan.signup_deadline is None or timezone.now()<plan.signup_deadline))


@transaction.atomic
def request_availability(*,actor,event_id,data):
    require_staff(actor)
    event=Event.objects.select_for_update().get(pk=event_id)
    profile=own_profile(actor)
    if not profile: raise PermissionDenied
    profile=StaffProfile.objects.select_for_update().get(pk=profile.pk)
    plan=StaffingPlan.objects.filter(event=event).first()
    if not profile.is_active or not opportunity_open(event,plan): raise ValidationError("Staff sign-ups are closed for this event.")
    if AvailabilityRequest.objects.filter(event=event,staff=profile,status__in=("pending","approved")).exists(): raise ValidationError("You already have a pending or approved request for this event.")
    if StaffAssignment.objects.filter(event=event,staff=profile,status="confirmed").exists(): raise ValidationError("You already have a confirmed assignment for this event.")
    form=AvailabilityForm(data,event=event)
    if not form.is_valid(): return None,form
    record=AvailabilityRequest.objects.create(event=event,staff=profile,requested_dates=sorted(form.cleaned_data["dates"]),note=form.cleaned_data["note"])
    audit(actor,"request.created",record)
    return record,form


def validate_assignment(*,event,staff,days,exclude=None):
    if not staff.is_active: raise ValidationError("Only active staff can receive a confirmed assignment.")
    if StaffAssignment.objects.filter(event=event,staff=staff,status="confirmed").exclude(pk=exclude).exists(): raise ValidationError("This employee already has a confirmed event assignment. Edit that assignment instead.")
    for row in days:
        if row["date"] not in event_days(event): raise ValidationError("An assigned date is outside this event's staffing calendar.")
        start=row["arrival_at"] or row["starts_at"]
        conflicts=AssignmentDay.objects.filter(assignment__staff=staff,assignment__status="confirmed",assignment__event__status="upcoming").exclude(assignment_id=exclude)
        if conflicts.filter(starts_at__lt=row["ends_at"]).filter(Q(ends_at__gt=start)).exists() or conflicts.filter(arrival_at__lt=row["ends_at"],ends_at__gt=start).exists():
            raise ValidationError("This employee has a conflicting confirmed assignment. Review working and arrival times.")


def _create_assignment(*,actor,event,staff,days,role="",source=None,old=None,reason=""):
    validate_assignment(event=event,staff=staff,days=days,exclude=old.pk if old else None)
    before=snapshot(old) if old else None
    if old:
        old.status="replaced"; old.changed_by=actor; old.change_reason=reason; old.save()
    assignment=StaffAssignment.objects.create(event=event,staff=staff,role=role,source_request=source,supersedes=old,changed_by=actor,change_reason=reason)
    AssignmentDay.objects.bulk_create([AssignmentDay(assignment=assignment,**row) for row in days])
    audit(actor,"assignment.revised" if old else "assignment.confirmed",assignment,before,reason)
    return assignment


@transaction.atomic
def save_assignment(*,actor,event_id,data,assignment_id=None):
    require_manager(actor)
    event=Event.objects.select_for_update().get(pk=event_id); mutable(event)
    if event.status!="upcoming": raise ValidationError("Publish the event to staff before confirming assignments.")
    old=StaffAssignment.objects.select_for_update().get(pk=assignment_id,event=event) if assignment_id else None
    if old and old.status!="confirmed": raise ValidationError("This assignment is no longer active. Open its current revision.")
    revision(old,data.get("revision"))
    form=AssignmentForm(data,event=event,assignment=old)
    if not form.is_valid(): return None,form
    staff=form.cleaned_data["staff"]
    list(StaffProfile.objects.select_for_update().filter(pk__in=[staff.pk]+([old.staff_id] if old else [])).order_by("pk"))
    staff.refresh_from_db()
    assignment=_create_assignment(actor=actor,event=event,staff=staff,days=form.cleaned_data["days"],role=form.cleaned_data["role"],source=old.source_request if old else None,old=old,reason=form.cleaned_data["reason"])
    return assignment,form


@transaction.atomic
def decide_request(*,actor,request_id,decision,data):
    require_manager(actor)
    if decision not in ("approve","decline"): raise ValidationError("Unsupported request decision.")
    record=AvailabilityRequest.objects.select_related("event").get(pk=request_id)
    event=Event.objects.select_for_update().get(pk=record.event_id); mutable(event)
    record=AvailabilityRequest.objects.select_for_update().get(pk=request_id)
    staff=StaffProfile.objects.select_for_update().get(pk=record.staff_id)
    if staff.user_id==actor.pk: raise PermissionDenied("Managers cannot approve or decline their own availability requests.")
    form=DecisionForm(data)
    if not form.is_valid(): return None,form
    revision(record,form.cleaned_data["revision"])
    if record.status!="pending": raise ValidationError("This request has already been decided.")
    if decision=="decline" and not form.cleaned_data["reason"].strip(): raise ValidationError("Enter a reason for declining this request.")
    before=snapshot(record)
    if decision=="approve":
        if event.status!="upcoming": raise ValidationError("Publish the event before confirming staff assignments.")
        days=[day_window(event,date.fromisoformat(d)) for d in record.requested_dates]
        _create_assignment(actor=actor,event=event,staff=staff,days=days,source=record,reason=form.cleaned_data["reason"])
    record.status="approved" if decision=="approve" else "declined"
    record.decided_by=actor; record.decided_at=timezone.now(); record.decision_reason=form.cleaned_data["reason"]; record.save()
    audit(actor,"request."+record.status,record,before,record.decision_reason)
    return record,form


@transaction.atomic
def remove_assignment(*,actor,assignment_id,data):
    require_manager(actor)
    old=StaffAssignment.objects.get(pk=assignment_id)
    event=Event.objects.select_for_update().get(pk=old.event_id); mutable(event)
    old=StaffAssignment.objects.select_for_update().get(pk=assignment_id)
    StaffProfile.objects.select_for_update().get(pk=old.staff_id)
    form=DecisionForm(data)
    if not form.is_valid(): return None,form
    revision(old,form.cleaned_data["revision"])
    if old.status!="confirmed": raise ValidationError("This assignment is no longer active.")
    if not form.cleaned_data["reason"].strip(): raise ValidationError("Enter a reason for removing the assignment.")
    before=snapshot(old); old.status="removed"; old.changed_by=actor; old.change_reason=form.cleaned_data["reason"]; old.save()
    audit(actor,"assignment.removed",old,before,old.change_reason)
    return old,form


def coverage(event):
    plan=StaffingPlan.objects.filter(event=event).first()
    needs={n.date:n.required_staff for n in plan.daily_needs.all()} if plan else {}
    assignments=StaffAssignment.objects.filter(event=event,status="confirmed")
    requests=list(AvailabilityRequest.objects.filter(event=event,status="pending"))
    counts=dict(AssignmentDay.objects.filter(assignment__in=assignments).values("date").annotate(n=Count("assignment__staff_id",distinct=True)).values_list("date","n"))
    result=[]
    for day in event_days(event):
        required=needs.get(day,plan.required_staff if plan else 0)
        confirmed=counts.get(day,0)
        pending=sum(day.isoformat() in r.requested_dates for r in requests)
        result.append(dict(date=day,required=required,confirmed=confirmed,pending=pending,unfilled=max(0,required-confirmed)))
    return dict(days=result,required=sum(r["required"] for r in result),confirmed=sum(r["confirmed"] for r in result),pending=len(requests),unfilled=sum(r["unfilled"] for r in result))


def validate_event_schedule(event, before):
    """Event date edits cannot strand already confirmed daily commitments."""
    assignments=event.staff_assignments.filter(status="confirmed").prefetch_related("days")
    if not assignments.exists(): return
    if before["timezone_name"]!=event.timezone_name:
        raise ValidationError("Remove or reassign confirmed staffing before changing the event timezone.")
    allowed=event_days(event)
    for assignment in assignments:
        for row in assignment.days.all():
            if row.date not in allowed:
                raise ValidationError("The new event calendar excludes confirmed staff dates. Edit staffing first.")
            if row.date>=event.local_start_at.date() and (row.starts_at<event.start_at or row.ends_at>event.end_at):
                raise ValidationError("The new event window excludes confirmed working times. Edit staffing first.")
