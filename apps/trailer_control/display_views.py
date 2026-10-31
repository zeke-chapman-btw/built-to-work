import hashlib
import json
import mimetypes
from pathlib import Path
from functools import wraps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse,Http404,FileResponse,HttpResponse,HttpResponseBadRequest
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods,require_POST
from .access import enabled,local_trailer
from .models import DisplayBinding
from .services import pair_display,accept_heartbeat
from .leaderboard import leaderboard_data


def scoped_binding(request,binding_id):
    if not enabled():raise Http404
    trailer=local_trailer()
    b=get_object_or_404(DisplayBinding.objects.select_related("device","preparation__event","preparation__trailer"),pk=binding_id,active=True,device__active=True,device__trailer=trailer,preparation__trailer=trailer)
    return b


def paired(request,b):
    v=request.session.get("trailer_displays",{}).get(str(b.pk),{})
    return bool(v.get("binding")==str(b.pk) and v.get("revision")==b.session_revision and v.get("expires",0)>timezone.now().timestamp())


def display_guard(fn):
    @never_cache
    @wraps(fn)
    def wrapped(request,binding_id):
        b=scoped_binding(request,binding_id)
        if not paired(request,b):return JsonResponse({"error":"Display pairing required"},status=403)
        response=fn(request,b)
        response["Referrer-Policy"]="no-referrer";response["X-Content-Type-Options"]="nosniff"
        return response
    return wrapped


@never_cache
@require_http_methods(["GET","POST"])
def display(request,binding_id):
    b=scoped_binding(request,binding_id);error=""
    if request.method=="POST":
        try:bound=pair_display(b.pk,request.POST.get("code","")[:200])
        except ValidationError as exc:error=" ".join(exc.messages)
        else:
            now=timezone.now().timestamp()
            bindings={k:v for k,v in request.session.get("trailer_displays",{}).items() if v.get("expires",0)>now}
            if len(bindings)>=8:bindings.pop(next(iter(bindings)))
            bindings[str(bound.pk)]={"binding":str(bound.pk),"revision":bound.session_revision,"expires":now+43200}
            request.session["trailer_displays"]=bindings
            return redirect("trailer_control:display",binding_id=b.pk)
    if not paired(request,b):return render(request,"trailer_control/pair.html",{"binding":b,"error":error},status=400 if error else 200)
    template="trailer_control/leaderboard.html" if b.channel=="leaderboard" else "trailer_control/video.html"
    response=render(request,template,{"binding":b,"board":leaderboard_data(b) if b.channel=="leaderboard" else None,"has_media":bool(b.media_name and b.media_sha256 and settings.TRAILER_CONTROL_VIDEO_ROOT)})
    response["Referrer-Policy"]="no-referrer";return response


@display_guard
@require_http_methods(["GET"])
def data(request,b):
    if b.channel!="leaderboard":raise Http404
    payload=leaderboard_data(b);payload["as_of"]=timezone.now().isoformat()
    return JsonResponse(payload)


@display_guard
@require_POST
def display_heartbeat(request,b):
    if request.content_type!="application/json":return HttpResponse(status=415)
    if len(request.body)>2048:return HttpResponseBadRequest()
    try:
        payload=json.loads(request.body)
        accept_heartbeat(b.device_id,"",payload,paired=(b.pk,b.session_revision))
    except (ValueError,ValidationError,TypeError):return JsonResponse({"error":"Heartbeat not accepted"},status=409)
    return JsonResponse({"accepted":True})


@csrf_exempt
@never_cache
@require_POST
def device_heartbeat(request,device_id):
    if not enabled():raise Http404
    trailer=local_trailer()
    from .models import Device
    if not Device.objects.filter(pk=device_id,trailer=trailer,active=True).exists():return JsonResponse({"error":"Device authentication failed"},status=401)
    if request.content_type!="application/json":return HttpResponse(status=415)
    if len(request.body)>2048:return HttpResponseBadRequest()
    header=request.headers.get("Authorization","")
    if not header.startswith("Bearer ") or len(header)>200:return JsonResponse({"error":"Device authentication failed"},status=401)
    try:accept_heartbeat(device_id,header[7:],json.loads(request.body))
    except ValidationError as exc:return JsonResponse({"error":"Heartbeat not accepted"},status=401 if exc.code=="authentication" else 409)
    except (ValueError,TypeError):return HttpResponseBadRequest()
    return JsonResponse({"accepted":True})


@display_guard
@require_http_methods(["GET","HEAD"])
def media(request,b):
    if b.channel!="video" or not b.media_name or not b.media_sha256:raise Http404
    root=Path(settings.TRAILER_CONTROL_VIDEO_ROOT).resolve() if settings.TRAILER_CONTROL_VIDEO_ROOT else None
    if root is None or Path(b.media_name).name!=b.media_name:raise Http404
    path=(root/b.media_name).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in {".mp4",".webm"} or not path.is_file():raise Http404
    # Approved asset hash is checked before serving; never select/copy arbitrary media.
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    if h.hexdigest()!=b.media_sha256:raise Http404
    size=path.stat().st_size;start=0;end=size-1;status=200
    byte_range=request.headers.get("Range","")
    if byte_range:
        import re
        match=re.fullmatch(r"bytes=(\d*)-(\d*)",byte_range)
        if not match or not any(match.groups()):return HttpResponse(status=416,headers={"Content-Range":f"bytes */{size}"})
        a,z=match.groups()
        if a:start=int(a);end=min(int(z),size-1) if z else size-1
        else:start=max(0,size-int(z))
        if start>end or start>=size:return HttpResponse(status=416,headers={"Content-Range":f"bytes */{size}"})
        status=206
    length=max(0,end-start+1)
    def chunks():
        with path.open("rb") as f:
            f.seek(start);remaining=length
            while remaining:
                part=f.read(min(256*1024,remaining))
                if not part:break
                remaining-=len(part);yield part
    from django.http import StreamingHttpResponse
    response=StreamingHttpResponse(iter(()) if request.method=="HEAD" else chunks(),status=status,content_type="video/mp4" if path.suffix.lower()==".mp4" else "video/webm")
    response["Content-Length"]=str(length);response["Accept-Ranges"]="bytes"
    if status==206:response["Content-Range"]=f"bytes {start}-{end}/{size}"
    return response
