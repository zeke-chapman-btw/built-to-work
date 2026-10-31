"""Bounded orchestration over the existing synchronization contracts; no second outbox."""
from datetime import timedelta
import io
import json
import os
import time
from urllib import request
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from apps.events.models import SyncOutboxItem
from apps.events.sync import build_sync_payload,claim_sync_item,acknowledge_sync,record_sync_failure
from apps.events.management.commands.sync_central_events import Command as DownloadCommand
from .models import SyncRunRequest,ControlConfiguration
from .health import endpoint_allowed,NoRedirect
from .services import audit
from .access import allowed


def configured_transport(direction,trailer):
    identity=os.environ.get("BTW_TRAILER_IDENTITY","").strip()
    token=os.environ.get("BTW_SYNC_API_TOKEN","").strip()
    url=os.environ.get("BTW_SYNC_ENDPOINT" if direction=="outbound" else "BTW_CENTRAL_DOWNLOAD_URL","").strip()
    if identity!=trailer.identity or not token or not endpoint_allowed(url):
        raise ValidationError("Approved HTTPS synchronization transport is not configured for this trailer.")
    return url,token


def read_json(url,headers,*,body=None,method="GET"):
    req=request.Request(url,data=body,headers=headers,method=method)
    with request.build_opener(NoRedirect).open(req,timeout=15) as response:
        raw=response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise ValueError("Response limit")
        data=json.loads(raw)
        return response.status,data


def send_batch(job,url,token,*,maximum=20,seconds=45):
    deadline=time.monotonic()+seconds;processed=0
    while processed<maximum and time.monotonic()<deadline:
        now=timezone.now()
        item=SyncOutboxItem.objects.filter(preparation=job.preparation).filter(
            Q(status__in=("pending","failed"),next_attempt_at__lte=now)|Q(status="in_flight",updated_at__lte=now-timedelta(seconds=60))).order_by("created_at").first()
        if item is None:break
        item=claim_sync_item(item.pk)
        if item is None:continue
        processed+=1
        try:
            status,result=read_json(url,{"Content-Type":"application/json","Authorization":"Bearer "+token},body=json.dumps(build_sync_payload(item)).encode(),method="POST")
            if status!=200 or result.get("status")!="accepted":raise ValueError("Central acceptance pending")
            acknowledge_sync(item.pk)
        except (OSError,ValueError,TypeError):
            # Retain existing retry/backoff and accepted/review semantics; redact transport/PII.
            record_sync_failure(item.pk,"Central acceptance unavailable; retained for retry/review")
    return f"Processed {processed} eligible records; outstanding/review rows may remain."


class BoundedDownload(DownloadCommand):
    def __init__(self,deadline,pages=5):
        super().__init__(stdout=io.StringIO(),stderr=io.StringIO())
        self.deadline=deadline;self.pages=pages
    def fetch(self,endpoint,identity,token,params):
        from urllib.parse import urlencode
        if self.pages<=0 or time.monotonic()>=self.deadline:raise TimeoutError("Bounded download paused")
        self.pages-=1
        status,payload=read_json(endpoint+("?"+urlencode(params) if params else ""),{"Authorization":"Bearer "+token,"X-Trailer-Identity":identity})
        if status!=200:raise ValueError("Central response unavailable")
        return payload


def pull_selected(job,url,token):
    command=BoundedDownload(time.monotonic()+45)
    catalog=command.fetch(url,job.trailer.identity,token,{})
    item=next((r for r in catalog.get("events",[]) if r.get("event_id")==str(job.preparation.event_id) and r.get("preparation_id")==str(job.preparation_id)),None)
    if item is None:raise ValidationError("Selected event is not available in the scoped central catalog.")
    command.pull_event(url,job.trailer.identity,token,item,force=False)
    return "Requested selected event updates; checkpoint/error reflects the retained download state."


def recover_expired(trailer):
    with transaction.atomic():
        type(trailer).objects.select_for_update().get(pk=trailer.pk)
        for job in SyncRunRequest.objects.select_for_update().filter(trailer=trailer,status="running",lease_until__lte=timezone.now()):
            job.status="failed";job.finished_at=timezone.now();job.summary="Worker lease expired; retained queue/checkpoints can be resumed by a new request.";job.save()
            audit(None,"sync.interrupted",job,"Expired worker lease; no queue deletion or forced retry")


def run_one(trailer):
    recover_expired(trailer)
    with transaction.atomic():
        type(trailer).objects.select_for_update().get(pk=trailer.pk)
        if SyncRunRequest.objects.filter(trailer=trailer,status="running").exists():return None
        job=SyncRunRequest.objects.select_for_update(of=("self",)).select_related("preparation__event","actor","trailer").filter(trailer=trailer,status="pending").order_by("created_at").first()
        if job is None:return None
        job.status="running";job.started_at=timezone.now();job.lease_until=timezone.now()+timedelta(minutes=5);job.save()
    try:
        if not job.actor or not allowed(job.actor,"request_trailer_sync") or not job.actor.has_perms(("events.view_syncoutboxitem","events.view_offlineeventpreparation")):
            raise ValidationError("Requesting account no longer has synchronization authority.")
        if job.direction=="inbound" and not job.actor.has_perms(("events.view_eventregistration","participants.view_participant")):
            raise ValidationError("Inbound participant access is unavailable.")
        url,token=configured_transport(job.direction,trailer)
        summary=send_batch(job,url,token) if job.direction=="outbound" else pull_selected(job,url,token)
        status="finished"
    except Exception:
        # Never put endpoint secrets or remote intake payloads in the UI/audit.
        summary="Request could not finish. Check local transport/authority; existing queue and checkpoints are retained."
        status="failed"
    with transaction.atomic():
        current=SyncRunRequest.objects.select_for_update().get(pk=job.pk)
        if current.status=="running":
            current.status=status;current.summary=summary;current.finished_at=timezone.now();current.lease_until=None;current.save()
            audit(None,"sync.finished",current,"Bounded worker run completed",status=status)
    return current
