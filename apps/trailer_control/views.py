import json
import uuid
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import IntegrityError
from django.http import JsonResponse,Http404,HttpResponseBadRequest
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.events.models import EventRegistration
from .access import require,allowed,local_trailer
from .models import Device,DisplayBinding,PublicationGrant
from .forms import ContextForm,DeviceForm,SyncForm
from . import services
from .health import health_view


def staff_view(fn):
    @login_required
    @never_cache
    @wraps(fn)
    def wrapped(request,*args,**kwargs):
        require(request.user)
        request.control_trailer=local_trailer()
        cfg=services.selected(request.control_trailer)
        from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
        try:zone=ZoneInfo(cfg.preparation.event.timezone_name) if cfg and cfg.preparation_id else ZoneInfo("UTC")
        except ZoneInfoNotFoundError:zone=ZoneInfo("UTC")
        with timezone.override(zone):response=fn(request,*args,**kwargs)
        # Keep HTTPS form Origin/Referer checks usable without leaking referrers cross-origin.
        # Public TV responses retain their separate no-referrer policy.
        response["Referrer-Policy"]="same-origin"
        return response
    return wrapped


def context(request,**extra):
    trailer=request.control_trailer;cfg=services.selected(trailer);prep=cfg.preparation if cfg else None
    devices=list(Device.objects.filter(trailer=trailer).select_related("station"))
    for d in devices:
        d.communication=services.communication(d)
        d.last_activity=None
        if prep and d.station_id and request.user.has_perm("simulators.view_simulatorcapture"):
            from apps.simulators.models import SimulatorCapture
            d.last_activity=SimulatorCapture.objects.filter(event=prep.event,station=d.station).order_by("-received_at").values_list("received_at",flat=True).first()
    manage=allowed(request.user,"manage_control_center")
    can_sync=allowed(request.user,"request_trailer_sync") and request.user.has_perms(("events.view_syncoutboxitem","events.view_offlineeventpreparation"))
    return {"page_title":"Trailer Control Center","trailer":trailer,"configuration":cfg,"preparation":prep,
            "devices":devices,"health":health_view(cfg,devices),"can_manage":manage,
            "can_enroll":manage and request.user.is_superuser,"can_sync":can_sync,
            "can_view_sync":request.user.has_perm("events.view_syncoutboxitem"),
            "can_publish":allowed(request.user,"publish_trailer_scores") and request.user.has_perms(("participants.view_participant","events.view_eventregistration","simulators.view_simulatorcapture")),
            "displays":DisplayBinding.objects.filter(device__trailer=trailer).select_related("device","preparation__event"),**extra}


def links_for(user,prep):
    links=[]
    if not prep:return links
    if user.has_perm("events.view_event"):links.append(("Current event",reverse("events:event_detail",args=[prep.event_id])))
    if user.has_perms(("participants.view_participant","events.view_eventregistration")):
        links.append(("Participants & tickets",reverse("participants:staff-directory")+"?event="+str(prep.event_id)))
    from apps.events.experience_operations import can_view_event_operations
    if can_view_event_operations(user):links.append(("Activity & results",reverse("events:event_detail",args=[prep.event_id])+"?section=results"))
    from apps.operations.execution_services import can_view
    if can_view(user,prep.event):links.append(("Event checklist",reverse("operations:health",args=[prep.event_id])))
    if user.has_perm("events.view_offlineeventpreparation"):links.append(("Offline preparation",reverse("events:offline-preparation-detail",args=[prep.pk])))
    if user.is_superuser:links.append(("Trailer Form Builder setup",reverse("participants:intake-trailer-manage",args=[prep.pk])))
    return links


@staff_view
@require_http_methods(["GET","POST"])
def dashboard(request):
    trailer=request.control_trailer;cfg=services.selected(trailer)
    form=ContextForm(request.POST if request.method=="POST" else None,trailer=trailer,initial={"revision":cfg.revision if cfg else 0,"preparation":cfg.preparation_id if cfg else None})
    if request.method=="POST":
        require(request.user,"manage_control_center")
        if form.is_valid():
            try:services.set_context(request.user,trailer,form.cleaned_data["preparation"].pk,form.cleaned_data["revision"],form.cleaned_data["reason"])
            except ValidationError as exc:form.add_error(None,exc)
            else:messages.success(request,"Local dashboard context saved. Station assignments and lifecycle are unchanged.");return redirect("trailer_control:index")
    data=context(request,context_form=form);prep=data["preparation"]
    data["links"]=links_for(request.user,prep)
    if data["can_view_sync"]:data["sync"]=services.sync_summary(trailer,prep)
    if prep and request.user.has_perms(("events.view_eventregistration","participants.view_participant")):
        data["counts"]={"registered":EventRegistration.objects.filter(event=prep.event,status="active",participant__kind="person").count(),"started":None,"complete":None}
        from apps.events.experience_operations import can_view_event_operations,event_experience_overview
        if can_view_event_operations(request.user):
            metrics={m["label"]:m["value"] for m in event_experience_overview(prep.event)["metrics"]}
            data["counts"].update(started=metrics["Official experience sessions"],complete=metrics["Completed experience sessions"])
    return render(request,"trailer_control/dashboard.html",data,status=400 if request.method=="POST" and form.errors else 200)


@staff_view
@require_http_methods(["GET"])
def status(request):
    data=context(request)
    response={"sampled_at":data["health"]["sampled_at"],"fresh":data["health"]["fresh"],"server":data["health"]["values"],"worker":data["health"]["worker"],"devices":[{"id":str(d.pk),"name":d.name,"communication":d.communication,"last_seen":d.last_seen_at,"application":d.application_state or "Unknown","playback_advancing":d.playback_advancing,"last_capture_activity":d.last_activity,"connection":d.get_connection_display(),"wifi_signal":"Unknown"} for d in data["devices"]]}
    return JsonResponse(response)


@staff_view
@require_http_methods(["GET","POST"])
def devices(request):
    data=context(request);trailer=request.control_trailer;secret=None;pairing=None
    form=DeviceForm(request.POST if request.method=="POST" and request.POST.get("action")=="enroll" else None,preparation=data["preparation"])
    if request.method=="POST":
        require(request.user,"manage_control_center")
        if not request.user.is_superuser:raise PermissionDenied
        try:
            action=request.POST.get("action")
            if action=="enroll":
                if form.is_valid():device,secret=services.enroll_device(request.user,trailer,**form.cleaned_data)
            elif action in {"rotate","disable","pair"}:
                device=get_object_or_404(Device,pk=request.POST.get("device"),trailer=trailer)
                if action=="pair":
                    binding,code=services.create_display(request.user,device,channel=device.kind)
                    pairing={"code":code,"url":reverse("trailer_control:display",args=[binding.pk])}
                else:secret=services.rotate_device(request.user,device,disable=action=="disable")
            else:raise ValidationError("Choose a supported device action.")
        except ValidationError as exc:form.add_error(None,exc)
        if secret:messages.success(request,"Device credential shown once below. Keep it only in the intended local device configuration.")
    return render(request,"trailer_control/devices.html",context(request,device_form=form,credential=secret,pairing=pairing))


@staff_view
@require_http_methods(["GET","POST"])
def sync_center(request):
    if not request.user.has_perm("events.view_syncoutboxitem"):raise PermissionDenied
    form=SyncForm(request.POST if request.method=="POST" else None,initial={"request_key":uuid.uuid4()})
    if request.method=="POST":
        require(request.user,"request_trailer_sync")
        if not request.user.has_perm("events.view_offlineeventpreparation"):raise PermissionDenied
        if form.is_valid():
            if form.cleaned_data["direction"]=="inbound" and not request.user.has_perms(("participants.view_participant","events.view_eventregistration")):raise PermissionDenied
            try:job,created=services.request_sync(request.user,request.control_trailer,**dict(direction=form.cleaned_data["direction"],key=form.cleaned_data["request_key"],reason=form.cleaned_data["reason"]))
            except (ValidationError,ValueError) as exc:form.add_error(None,str(exc))
            except IntegrityError:form.add_error(None,"Another request is active. Reload to review its status.")
            else:messages.success(request,"Background synchronization requested." if created else "The existing request was retained; no duplicate run created.");return redirect("trailer_control:sync")
    data=context(request,sync_form=form)
    data["sync"]=services.sync_summary(request.control_trailer,data["preparation"])
    data["review_url"]=reverse("events:match-review-list") if request.user.has_perms(("participants.view_participant","events.view_participantmatchreview")) else ""
    return render(request,"trailer_control/sync.html",data,status=400 if request.method=="POST" and form.errors else 200)


@staff_view
@require_http_methods(["GET","POST"])
def publication(request):
    data=context(request)
    if not data["can_publish"]:raise PermissionDenied
    prep=data["preparation"]
    if not prep:raise Http404
    error=""
    if request.method=="POST":
        try:
            if request.POST.get("decision") not in {"publish","withhold"}:raise ValidationError("Choose publish or withhold.")
            services.publish_score(request.user,prep,int(request.POST.get("participant","")),publish=request.POST["decision"]=="publish",reason=request.POST.get("reason",""))
        except (ValidationError,ValueError) as exc:error=" ".join(getattr(exc,"messages",[str(exc)]))
        else:messages.success(request,"Event-specific publication decision saved. Official scores were not changed.");return redirect("trailer_control:publication")
    people=EventRegistration.objects.filter(event=prep.event,status="active",participant__kind="person",participant__archived_at__isnull=True).select_related("participant").order_by("participant__last_name")
    from django.core.paginator import Paginator
    grants={g.participant_id:g for g in PublicationGrant.objects.filter(event=prep.event)}
    rows=Paginator(people,25).get_page(request.GET.get("page"))
    for row in rows:row.publication=grants.get(row.participant_id)
    data.update(rows=rows,error=error)
    return render(request,"trailer_control/publication.html",data,status=400 if error else 200)
