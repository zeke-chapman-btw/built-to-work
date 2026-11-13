from functools import wraps
from zoneinfo import ZoneInfo
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.internal_ui.record_editors import event_return, editor_response, editor_success, revision_conflict, record_unavailable
from apps.events.models import Event
from apps.events.event_management import available_actions
from .models import StaffProfile, StaffingPlan, AvailabilityRequest, StaffAssignment
from .forms import ProfileForm, AccountLinkForm, PlanForm, AvailabilityForm, AssignmentForm, DecisionForm
from . import services


def staff_view(function):
    @login_required(login_url="/admin/login/")
    @require_http_methods(["GET","POST"])
    @wraps(function)
    def wrapped(request,*args,**kwargs):
        services.require_staff(request.user)
        try:
            return function(request,*args,**kwargs)
        except ValidationError as exc:
            return render(request,"staffing/notice.html",dict(page_title="Staffing action unavailable",notice=" ".join(exc.messages)),status=409)
    return wrapped


def display_form(request,form,*,title,submit,back,assignment=False,**context):
    values=dict(form=form,page_title=title,submit_label=submit,back_url=back,assignment_form=assignment,**context)
    if context.get('event'):
        unavailable=record_unavailable(form) or bool(context.get('record') and context['record'].status!='pending')
        values.update(record_unavailable=unavailable,back=event_return(request,context['event'],staffing=True),save_label=submit,
            editor_context=context['event'].name,save_conflict=revision_conflict(form) or unavailable,fields_template='staffing/editor_fields.html')
        return editor_response(request,template='staffing/form.html',panel='internal/ui/record_form_panel.html',context=values)
    return render(request,"staffing/form.html",values,status=400 if form.is_bound else 200)


@staff_view
def directory(request):
    services.require_perm(request.user,"staffing.view_staffprofile")
    if request.method!="GET": raise PermissionDenied
    people=StaffProfile.objects.all(); q=request.GET.get("q","").strip()[:200]; state=request.GET.get("status","active")
    if state not in ("active","inactive","all"): state="active"
    if state!="all": people=people.filter(is_active=state=="active")
    if q: people=people.filter(Q(first_name__icontains=q)|Q(last_name__icontains=q)|Q(email__icontains=q))
    from apps.accounts.roles import manager, roles_for
    page_people=Paginator(people.select_related("user").order_by("last_name","first_name"),25).get_page(request.GET.get("page"))
    labels={"manager":"Manager","recruiter":"Recruiter","event_staff":"Event staff"}
    for person in page_people:
        person.roster_roles=(["Admin"] if person.user_id and person.user.is_superuser else [])+([labels[r] for r in sorted(roles_for(person.user)) if r in labels] if person.user_id else [])
        person.pay_revision=person.updated_at.isoformat()
    return render(request,"staffing/directory.html",dict(page_title="Staff roster",people=page_people,q=q,selected_status=state,can_manage_staff=manager(request.user),back=request.get_full_path()))


@staff_view
def profile(request,profile_id):
    services.require_perm(request.user,"staffing.view_staffprofile")
    if request.method!="GET": raise PermissionDenied
    person=get_object_or_404(StaffProfile.objects.select_related("user"),pk=profile_id)
    history=person.assignments.select_related("event","source_request","changed_by").prefetch_related("days")
    from apps.accounts.roles import require_account_manager, require_target
    can_manage = False
    try:
        require_account_manager(request.user)
        if person.user_id:
            require_target(request.user, person.user)
        can_manage = True
    except PermissionDenied:
        pass
    from . import roster
    from apps.accounts.roles import roles_for
    labels={"manager":"Manager","recruiter":"Recruiter","event_staff":"Event staff"}
    roles=([ "Admin"] if person.user_id and person.user.is_superuser else [])+([labels[r] for r in sorted(roles_for(person.user)) if r in labels] if person.user_id else [])
    pay=roster.pay_summary(person) if roster.can_see_pay(request.user) else None
    upcoming=[a for a in history.exclude(event__status__in=("draft","cancelled","archived")).filter(status="confirmed",event__end_at__gte=timezone.now()).order_by("event__start_at")]
    return render(request,"staffing/profile.html",dict(page_title=str(person),person=person,can_manage_staff=can_manage,roles=roles,pay=pay,upcoming=upcoming,
        can_delete=can_manage and roster.can_delete(person),pay_revision=person.updated_at.isoformat(),
        history=Paginator(history,20).get_page(request.GET.get("page")),availability=person.availability_requests.select_related("event","decided_by")[:50]))


@staff_view
def roster_action(request,profile_id,action):
    """Archive, restore, delete, or switch My pay from the staff roster."""
    from . import roster
    if request.method!="POST":raise PermissionDenied
    back=request.POST.get("back") or ""
    if not back.startswith("/staff/directory/"):back=reverse("staffing:profile",args=[profile_id])
    try:
        if action in ("archive","restore"):
            roster.set_archived(actor=request.user,profile_id=profile_id,archived=action=="archive")
            messages.success(request,"Archived. Their history is kept." if action=="archive" else "Restored.")
        elif action=="delete":
            roster.delete_profile(actor=request.user,profile_id=profile_id)
            messages.success(request,"Deleted.")
            return redirect("staffing:directory")
        elif action=="my-pay":
            from apps.accounts.staff_onboarding import set_own_pay_access
            set_own_pay_access(actor=request.user,profile_id=profile_id,enabled=request.POST.get("enabled")=="1",expected_revision=request.POST.get("revision",""))
            messages.success(request,"My pay updated.")
        else:raise PermissionDenied
    except ValidationError as exc:
        messages.error(request," ".join(exc.messages))
    return redirect(back)


@staff_view
def profile_edit(request,profile_id=None):
    if profile_id is None and request.method == 'GET':
        from apps.accounts.roles import manager
        if manager(request.user):
            return redirect('accounts:staff-create')
    services.require_perm(request.user,"staffing.change_staffprofile" if profile_id else "staffing.add_staffprofile")
    person=get_object_or_404(StaffProfile,pk=profile_id) if profile_id else StaffProfile()
    form=ProfileForm(instance=person)
    if request.method=="POST":
        try:
            saved,form=services.save_profile(actor=request.user,data=request.POST,profile_id=profile_id)
            if saved:
                messages.success(request,"Staff profile saved. Account links and login permissions were not changed.")
                return redirect("staffing:profile",profile_id=saved.pk)
        except ValidationError as exc:
            form=ProfileForm(request.POST,instance=person); form.is_valid(); form.add_error(None,exc)
    from django.urls import reverse
    return display_form(request,form,title="Edit staff" if profile_id else "Add staff",submit="Save staff profile",back=reverse("staffing:directory"))


@staff_view
def account_link(request,profile_id):
    services.require_perm(request.user,"staffing.link_staff_account"); services.require_perm(request.user,"staffing.change_staffprofile")
    person=get_object_or_404(StaffProfile,pk=profile_id); form=AccountLinkForm(profile=person)
    if request.method=="POST":
        try:
            saved,form=services.link_account(actor=request.user,profile_id=person.pk,data=request.POST)
            if saved:
                messages.success(request,"Account link updated. No account or permissions were created.")
                return redirect("staffing:profile",profile_id=person.pk)
        except ValidationError as exc:
            form=AccountLinkForm(request.POST,profile=person); form.is_valid(); form.add_error(None,exc)
    from django.urls import reverse
    return display_form(request,form,title="Confirm staff account link",submit="Confirm account link",back=reverse("staffing:profile",args=[person.pk]),person=person)


def manager_context(request,event):
    can_records=request.user.has_perms(("events.view_eventregistration","participants.view_participant"))
    from apps.events.experience_operations import can_view_event_operations
    return dict(event=event,page_title=event.name,section="staffing",management_actions=available_actions(event),can_view_records=can_records,can_view_operations=can_view_event_operations(request.user))


@staff_view
def event_staffing(request,event_id):
    services.require_manager(request.user)
    if request.method!="GET": raise PermissionDenied
    event=get_object_or_404(Event,pk=event_id); plan=StaffingPlan.objects.filter(event=event).first()
    context=manager_context(request,event)
    try: summary=services.coverage(event)
    except ValidationError as exc: summary=None; context["calendar_error"]=" ".join(exc.messages)
    context.update(plan=plan,summary=summary,assignments=Paginator(event.staff_assignments.select_related("staff","changed_by","source_request").prefetch_related("days").order_by("status","-created_at"),20).get_page(request.GET.get("page")),availability=event.staff_availability.select_related("staff","decided_by")[:100],can_manage=event.status in ("draft","upcoming"))
    # Present current and retained records separately without changing pagination.
    context["confirmed_assignments"] = [a for a in context["assignments"] if a.status == "confirmed"]
    context["assignment_history"] = [a for a in context["assignments"] if a.status != "confirmed"]
    from datetime import date as _date
    days=[d["date"] for d in summary["days"]] if summary else []
    def day_chips(requested):
        wanted=set()
        for value in requested or []:
            try: wanted.add(_date.fromisoformat(str(value)))
            except ValueError: pass
        return [dict(date=d,on=d in wanted) for d in days] or [dict(date=d,on=True) for d in sorted(wanted)]
    for record in context["availability"]:
        record.day_chips=day_chips(record.requested_dates)
    context["pending_requests"]=[r for r in context["availability"] if r.status=="pending"]
    context["decided_requests"]=[r for r in context["availability"] if r.status!="pending"]
    for assignment in context["confirmed_assignments"]:
        worked={d.date for d in assignment.days.all()}
        assignment.day_chips=[dict(date=d,on=d in worked) for d in days] or [dict(date=d,on=True) for d in sorted(worked)]
    context["signup_open"]=services.opportunity_open(event,plan)
    with timezone.override(ZoneInfo(event.timezone_name)):
        return render(request,"staffing/event_manage.html",context)


@staff_view
def plan_edit(request,event_id):
    services.require_manager(request.user)
    event=get_object_or_404(Event,pk=event_id); plan=StaffingPlan.objects.filter(event=event).first() or StaffingPlan(event=event)
    services.mutable(event)
    try: form=PlanForm(event=event,plan=plan)
    except ValidationError as exc: return render(request,"staffing/notice.html",dict(page_title="Review event schedule",notice=" ".join(exc.messages)),status=409)
    if request.method=="POST":
        try:
            saved,form=services.save_plan(actor=request.user,event_id=event.pk,data=request.POST)
            if saved:
                messages.success(request,"Staffing needs, publication and sign-up deadline saved.")
                return editor_success(request,event_return(request,event,staffing=True))
        except ValidationError as exc:
            form=PlanForm(request.POST,event=event,plan=plan); form.is_valid(); form.add_error(None,exc)
    from django.urls import reverse
    return display_form(request,form,title="Staffing needs and sign-up deadline",submit="Save staffing settings",back=reverse("staffing:event",args=[event.pk]),event=event)


@staff_view
def assignment_edit(request,event_id,assignment_id=None):
    services.require_manager(request.user)
    event=get_object_or_404(Event,pk=event_id); services.mutable(event)
    old=get_object_or_404(StaffAssignment,event=event,pk=assignment_id) if assignment_id else None
    if old and old.status!="confirmed" and request.method!="POST": raise Http404
    try: form=AssignmentForm(event=event,assignment=old)
    except ValidationError as exc: return render(request,"staffing/notice.html",dict(page_title="Review event schedule",notice=" ".join(exc.messages)),status=409)
    if request.method=="POST":
        try:
            saved,form=services.save_assignment(actor=request.user,event_id=event.pk,assignment_id=assignment_id,data=request.POST)
            if saved:
                messages.success(request,"Confirmed assignment saved. The staff schedule and coverage are updated; original availability is retained.")
                return editor_success(request,event_return(request,event,staffing=True))
        except ValidationError as exc:
            form=AssignmentForm(request.POST,event=event,assignment=old); form.is_valid(); form.add_error(None,exc)
    from django.urls import reverse
    return display_form(request,form,title="Edit or reassign staff" if old else "Assign Staff",submit="Save confirmed assignment",back=reverse("staffing:event",args=[event.pk]),assignment=True,event=event)


@staff_view
def request_decision(request,request_id,decision):
    services.require_manager(request.user)
    if decision not in ("approve","decline"): raise Http404
    record=get_object_or_404(AvailabilityRequest.objects.select_related("staff","event"),pk=request_id)
    form=DecisionForm(initial={"revision":record.updated_at.isoformat()},approve=decision=="approve")
    if request.method=="POST":
        try:
            saved,form=services.decide_request(actor=request.user,request_id=record.pk,decision=decision,data=request.POST)
            if saved:
                messages.success(request,"Request approved and assignment confirmed." if decision=="approve" else "Request declined. No assignment was created.")
                return editor_success(request,event_return(request,record.event,staffing=True))
        except ValidationError as exc:
            form=DecisionForm(request.POST,approve=decision=="approve"); form.is_valid(); form.add_error(None,exc)
    from django.urls import reverse
    return display_form(request,form,title="Approve availability" if decision=="approve" else "Decline availability",submit="Approve and confirm assignment" if decision=="approve" else "Decline request",back=reverse("staffing:event",args=[record.event_id]),record=record,event=record.event)


@staff_view
def assignment_remove(request,assignment_id):
    services.require_manager(request.user)
    old=get_object_or_404(StaffAssignment.objects.select_related("staff","event"),pk=assignment_id)
    form=DecisionForm(initial={"revision":old.updated_at.isoformat()})
    if request.method=="POST":
        try:
            saved,form=services.remove_assignment(actor=request.user,assignment_id=old.pk,data=request.POST)
            if saved:
                messages.success(request,"Assignment removed from active coverage. History and the original request are retained.")
                return editor_success(request,event_return(request,old.event,staffing=True))
        except ValidationError as exc:
            form=DecisionForm(request.POST); form.is_valid(); form.add_error(None,exc)
    from django.urls import reverse
    return display_form(request,form,title="Remove assignment",submit="Remove confirmed assignment",back=reverse("staffing:event",args=[old.event_id]),old_assignment=old,event=old.event)


@staff_view
def request_queue(request):
    services.require_manager(request.user)
    if request.method!="GET": raise PermissionDenied
    queue=AvailabilityRequest.objects.filter(status="pending").select_related("event","staff").order_by("event__start_at","created_at")
    return render(request,"staffing/queue.html",dict(page_title="Staff availability requests",event_schedule_scope=True,requests=Paginator(queue,30).get_page(request.GET.get("page"))))


@staff_view
def schedule(request):
    from apps.accounts.operational_access import safe_event_worker
    if safe_event_worker(request.user):
        from .general_views import schedule as general_schedule
        return general_schedule(request)
    if request.method!="GET": raise PermissionDenied
    person=services.own_profile(request.user)
    if not person: return render(request,"staffing/notice.html",dict(page_title="My staff schedule",notice="Your account has no active staff profile link. Contact an authorized manager to review your profile and account link."))
    tab=request.GET.get("view","available")
    if tab not in ("available","requests","assignments","profile"): tab="available"
    events=Event.objects.filter(status="upcoming",staffing_plan__is_published=True,end_at__gt=timezone.now()).select_related("staffing_plan").order_by("start_at")
    return render(request,"staffing/schedule.html",dict(page_title="My staff schedule",event_schedule_scope=True,person=person,selected_view=tab,now=timezone.now(),events=Paginator(events,20).get_page(request.GET.get("page")),requests=Paginator(person.availability_requests.select_related("event").all(),20).get_page(request.GET.get("page")),assignments=Paginator(person.assignments.exclude(event__status="draft").select_related("event").prefetch_related("days"),20).get_page(request.GET.get("page"))))


@staff_view
def staff_event(request,event_id):
    from apps.accounts.operational_access import safe_event_worker
    if safe_event_worker(request.user):return redirect('staffing:general-event',event_id=event_id)
    person=services.own_profile(request.user)
    if not person: raise PermissionDenied
    event=get_object_or_404(Event,pk=event_id)
    plan=StaffingPlan.objects.filter(event=event).first()
    own_request=event.staff_availability.filter(staff=person).first()
    assigned=event.staff_assignments.filter(staff=person,status="confirmed").prefetch_related("days").first()
    own_history=event.staff_assignments.filter(staff=person).exists()
    published=bool(plan and plan.is_published and event.status=="upcoming")
    if event.status=="draft" or not (published or own_request or assigned or own_history): raise Http404
    form=AvailabilityForm(event=event)
    if request.method=="POST":
        try:
            saved,form=services.request_availability(actor=request.user,event_id=event.pk,data=request.POST)
            if saved:
                messages.success(request,"Availability submitted as Pending. This is not a confirmed assignment.")
                return redirect("staffing:staff-event",event_id=event.pk)
        except ValidationError as exc:
            form=AvailabilityForm(request.POST,event=event); form.is_valid(); form.add_error(None,exc)
    logistics=None
    if assigned:
        from .my_views import _stay
        logistics=dict(address=", ".join(v for v in (event.address_line_1,event.address_line_2,event.city,event.state,event.postal_code) if v),
            setup_date=event.setup_date,arrival=event.staff_arrival_time,return_date=event.return_date,travel=event.logistics_instructions,
            housing=_stay(event),housing_notes=event.housing_information,contact=dict(name=event.contact_name,phone=event.contact_phone,email=event.contact_email))
    with timezone.override(ZoneInfo(event.timezone_name)):
        return render(request,"staffing/staff_event.html",dict(page_title=event.name,event=event,plan=plan,own_request=own_request,assignment=assigned,logistics=logistics,signups_closed=not services.opportunity_open(event,plan),can_request=services.opportunity_open(event,plan) and not assigned and not (own_request and own_request.status in ("pending","approved")),form=form),status=400 if form.is_bound else 200)
