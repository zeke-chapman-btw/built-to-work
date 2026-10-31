import os
import shutil
from urllib.parse import urlsplit
from urllib import request,error
from django.conf import settings
from django.db import connection
from django.utils import timezone
from .models import ControlConfiguration,Device
from .services import communication


def endpoint_allowed(url):
    p=urlsplit(url)
    return bool(p.hostname and not p.username and not p.password and not p.fragment and not p.query and
        (p.scheme=="https" or (settings.DEBUG and p.scheme=="http" and p.hostname in {"127.0.0.1","localhost"})))


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError("Redirect refused")


def sample_health(trailer):
    now=timezone.now(); result={"application":"Responding","database":"Unknown","storage":"Unknown","central":"Not configured","free_bytes":None,"sampled_at":now.isoformat()}
    try:
        with connection.cursor() as c:c.execute("SELECT 1");c.fetchone()
        result["database"]="Responding"
    except Exception:result["database"]="Unavailable"
    root=getattr(settings,"TRAILER_CONTROL_DATA_ROOT","")
    if root:
        try:
            if not os.path.isdir(root):raise OSError
            result["free_bytes"]=shutil.disk_usage(root).free
            result["storage"]="Available" if os.access(root,os.W_OK) else "Not writable"
        except OSError:result["storage"]="Unavailable"
    endpoint=getattr(settings,"BTW_CENTRAL_SYNC_URL","")
    if endpoint_allowed(endpoint):
        try:
            req=request.Request(endpoint,method="HEAD")
            with request.build_opener(NoRedirect).open(req,timeout=2) as response:
                result["central"]="Reachable" if response.status<400 else "Server error"
        except error.HTTPError as exc:result["central"]="Reachable" if exc.code in (401,403,405) else "Server error"
        except (OSError,ValueError):result["central"]="Unavailable"
    elif endpoint:result["central"]="Invalid configuration"
    cfg,_=ControlConfiguration.objects.get_or_create(trailer=trailer)
    cfg.health=result;cfg.sampled_at=now;cfg.worker_seen_at=now;cfg.save(update_fields=["health","sampled_at","worker_seen_at","updated_at"])
    return result


def health_view(cfg,devices):
    now=timezone.now();fresh=bool(cfg and cfg.sampled_at and 0<=(now-cfg.sampled_at).total_seconds()<=45)
    return {"fresh":fresh,"values":cfg.health if fresh else {},"sampled_at":cfg.sampled_at if cfg else None,
            "worker":"Recent" if cfg and cfg.worker_seen_at and 0<=(now-cfg.worker_seen_at).total_seconds()<=45 else "Unknown / stale",
            "recent_devices":sum(communication(d,now)=="Recent" for d in devices),"stale_devices":sum(communication(d,now)=="Stale" for d in devices),"unknown_devices":sum(communication(d,now)=="Unknown" for d in devices)}
