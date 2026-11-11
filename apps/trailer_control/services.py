import hashlib
import hmac
import math
import secrets
import uuid
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.utils import timezone
from apps.core.audit import record_audit
from apps.events.models import OfflineEventPreparation, SyncOutboxItem, CentralEventDownload
from .access import require
from .models import ControlConfiguration, Device, DisplayBinding, HeartbeatCursor, PublicationGrant, SyncRunRequest


def audit(actor, action, obj, reason, **data):
    record_audit(actor=actor,action="trailer.control."+action,instance=obj,source="trailer_control_v1",reason=reason,new_data=data or None)


def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def selected(trailer):
    return ControlConfiguration.objects.select_related("preparation__event").filter(trailer=trailer).first()


@transaction.atomic
def set_context(actor,trailer,preparation_id,revision,reason):
    require(actor,"manage_control_center")
    type(trailer).objects.select_for_update().get(pk=trailer.pk)
    cfg=ControlConfiguration.objects.select_for_update().filter(trailer=trailer).first()
    if int(revision)!=(cfg.revision if cfg else 0): raise ValidationError("Context changed. Reload before saving.")
    prep=OfflineEventPreparation.objects.filter(pk=preparation_id,trailer=trailer).exclude(event__status__in=("draft","cancelled","archived")).first()
    reason = (reason or "").strip() or "Set by staff"
    if not prep: raise ValidationError("Choose a local event preparation.")
    if cfg is None: cfg=ControlConfiguration(trailer=trailer)
    cfg.preparation=prep; cfg.revision+=1; cfg.save()
    audit(actor,"context.changed",cfg,reason,preparation=str(prep.pk))
    return cfg


@transaction.atomic
def enroll_device(actor,trailer,*,name,kind,connection="unknown",station=None):
    require(actor,"manage_control_center")
    if not actor.is_superuser: raise ValidationError("Device enrollment requires an Administrator.")
    if not name.strip() or kind not in Device.Kind.values: raise ValidationError("Choose a device name and supported type.")
    if connection not in {"unknown","wifi","ethernet"}: raise ValidationError("Invalid connection type.")
    cfg=selected(trailer)
    if station and (not cfg or not cfg.preparation_id or not station.event_assignments.filter(event=cfg.preparation.event,enabled=True).exists()):
        raise ValidationError("Station must be assigned to the selected event.")
    secret=secrets.token_urlsafe(32)
    device=Device.objects.create(trailer=trailer,name=name.strip()[:100],kind=kind,connection=connection,station=station,credential_hash=digest(secret))
    audit(actor,"device.enrolled",device,"Explicit local device enrollment",kind=kind)
    return device,secret


@transaction.atomic
def rotate_device(actor,device,*,disable=False):
    require(actor,"manage_control_center")
    if not actor.is_superuser: raise ValidationError("Credential changes require an Administrator.")
    device=Device.objects.select_for_update().get(pk=device.pk)
    secret=secrets.token_urlsafe(32) if not disable else ""
    device.credential_hash=digest(secret) if secret else "";device.credential_revision+=1
    device.active=not disable;device.current_boot=None;device.last_seen_at=None;device.save()
    DisplayBinding.objects.filter(device=device).update(active=False,session_revision=models_f_increment(),pairing_hash="")
    audit(actor,"device.disabled" if disable else "device.rotated",device,"Administrator device credential change")
    return secret


def models_f_increment():
    from django.db.models import F
    return F("session_revision")+1


def communication(device,now=None):
    now=now or timezone.now()
    if not device.active:return "Disabled"
    if device.last_seen_at is None:return "Unknown"
    return "Recent" if timedelta(0)<=now-device.last_seen_at<=timedelta(seconds=45) else "Stale"


@transaction.atomic
def accept_heartbeat(device_id,secret,payload,*,paired=None):
    device=Device.objects.select_for_update().filter(pk=device_id,active=True).first()
    if not device or (not paired and (not device.credential_hash or not hmac.compare_digest(device.credential_hash,digest(secret)))):
        raise ValidationError("Device authentication failed.",code="authentication")
    if not isinstance(payload,dict) or set(payload)-{"boot_id","sequence","event_id","state","position"}:raise ValidationError("Unsupported heartbeat fields.")
    try:boot=uuid.UUID(str(payload["boot_id"]));seq=payload["sequence"]
    except (KeyError,ValueError,TypeError):raise ValidationError("Invalid heartbeat identity.")
    if type(seq) is not int or not 1<=seq<=2**53:raise ValidationError("Invalid heartbeat sequence.")
    state=payload.get("state","idle")
    if state not in {"startup","idle","operating","paused","playing","error"}:raise ValidationError("Invalid application state.")
    pos=payload.get("position")
    if pos is not None and (type(pos) not in (float,int) or not math.isfinite(pos) or not 0<=pos<=864000):raise ValidationError("Invalid playback position.")
    cfg=selected(device.trailer)
    expected=str(cfg.preparation.event_id) if cfg and cfg.preparation_id else None
    binding=DisplayBinding.objects.filter(pk=paired[0],session_revision=paired[1],device=device,active=True).first() if paired else None
    if paired and binding is None:raise ValidationError("Display authorization expired.",code="authentication")
    if binding:expected=str(binding.preparation.event_id)
    if str(payload.get("event_id"))!=str(expected):raise ValidationError("Heartbeat event scope mismatch.")
    now=timezone.now()
    cursor=HeartbeatCursor.objects.filter(device=device,boot_id=boot).first()
    if cursor and (cursor.retired or seq<=cursor.sequence):raise ValidationError("Duplicate or retired heartbeat.",code="replay")
    if device.last_seen_at and now-device.last_seen_at<timedelta(seconds=5):raise ValidationError("Heartbeat too frequent.",code="rate")
    if device.current_boot!=boot:
        HeartbeatCursor.objects.filter(device=device,retired=False).update(retired=True)
        device.current_boot=boot
    if cursor:cursor.sequence=seq;cursor.save(update_fields=["sequence"])
    else:HeartbeatCursor.objects.create(device=device,boot_id=boot,sequence=seq)
    device.playback_advancing=bool(state=="playing" and pos is not None and device.playback_position is not None and pos>device.playback_position)
    device.playback_position=pos;device.application_state=state;device.last_seen_at=now;device.save()
    return device


@transaction.atomic
def create_display(actor,device,*,channel,station=None):
    require(actor,"manage_control_center")
    if not actor.is_superuser:raise ValidationError("Display pairing requires an Administrator.")
    device=Device.objects.select_for_update().get(pk=device.pk)
    cfg=selected(device.trailer)
    if not device.active or not cfg or not cfg.preparation_id:raise ValidationError("Select an event and an active display device first.")
    if channel not in {"leaderboard","video"} or device.kind!=channel:raise ValidationError("Display channel must match the enrolled TV type.")
    if station and (station.station_type!="simulator" or not station.event_assignments.filter(event=cfg.preparation.event,enabled=True).exists()):raise ValidationError("Choose a simulator assigned to this event.")
    code=secrets.token_urlsafe(12)
    binding,_=DisplayBinding.objects.get_or_create(device=device,defaults={"preparation":cfg.preparation,"channel":channel})
    binding.preparation=cfg.preparation;binding.channel=channel;binding.profile_station=station;binding.active=True
    binding.session_revision+=1;binding.pairing_hash=digest(code);binding.pairing_expires_at=timezone.now()+timedelta(minutes=5);binding.pairing_attempts=0;binding.save()
    audit(actor,"display.pairing_started",binding,"Administrator requested event-scoped pairing",channel=channel)
    return binding,code


def pair_display(binding_id,code):
    # Failed attempt counters must commit, so validation is raised after the transaction.
    result=None
    with transaction.atomic():
        b=DisplayBinding.objects.select_for_update().select_related("device").filter(pk=binding_id).first()
        if b and b.active and b.device.active and b.pairing_hash and b.pairing_attempts<10 and b.pairing_expires_at and b.pairing_expires_at>timezone.now():
            b.pairing_attempts+=1
            if hmac.compare_digest(b.pairing_hash,digest(code)):
                b.pairing_hash="";result=b
                audit(None,"display.paired",b,"One-time display pairing completed",channel=b.channel)
            b.save(update_fields=["pairing_attempts","pairing_hash"])
    if result is None:raise ValidationError("Pairing code is invalid, expired or already used.")
    return result


@transaction.atomic
def publish_score(actor,preparation,participant_id,*,publish,reason):
    require(actor,"publish_trailer_scores")
    if not actor.has_perms(("participants.view_participant","events.view_eventregistration","simulators.view_simulatorcapture")):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    if not actor.has_perms(("participants.view_participant","events.view_eventregistration","simulators.view_simulatorcapture")):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    reason=(reason or "").strip() or ("Published by staff" if publish else "Unpublished by staff")
    from apps.events.models import EventRegistration
    if not EventRegistration.objects.filter(event=preparation.event,participant_id=participant_id,status="active",participant__kind="person",participant__archived_at__isnull=True).exists():raise ValidationError("Choose an active participant registered at this event.")
    # Lock the event to serialize first grants and alias assignment on PostgreSQL.
    type(preparation.event).objects.select_for_update().get(pk=preparation.event_id)
    grant=PublicationGrant.objects.select_for_update().filter(event=preparation.event,participant_id=participant_id).first()
    if grant is None:
        alias="Player "+secrets.token_hex(4).upper()
        while PublicationGrant.objects.filter(event=preparation.event,alias=alias).exists():alias="Player "+secrets.token_hex(4).upper()
        grant=PublicationGrant(event=preparation.event,participant_id=participant_id,alias=alias)
    grant.published=publish;grant.actor=actor;grant.reason=reason.strip()[:300];grant.save()
    audit(actor,"score.published" if publish else "score.withheld",grant,reason,event=str(preparation.event_id))
    return grant


@transaction.atomic
def request_sync(actor,trailer,*,direction,key,reason):
    require(actor,"request_trailer_sync")
    type(trailer).objects.select_for_update().get(pk=trailer.pk)
    cfg=selected(trailer)
    reason=(reason or "").strip() or "Requested by staff"
    if not cfg or not cfg.preparation_id:raise ValidationError("Select a local event first.")
    if direction not in {"outbound","inbound"}:raise ValidationError("Choose a synchronization direction.")
    key=uuid.UUID(str(key))
    prior=SyncRunRequest.objects.filter(request_key=key).first()
    if prior:
        if prior.trailer_id!=trailer.pk or prior.preparation_id!=cfg.preparation_id or prior.direction!=direction:raise ValidationError("Request identifier belongs to another operation.")
        return prior,False
    active=SyncRunRequest.objects.filter(trailer=trailer,status__in=("pending","running")).first()
    if active:return active,False
    job=SyncRunRequest.objects.create(trailer=trailer,preparation=cfg.preparation,actor=actor,request_key=key,direction=direction,reason=reason.strip()[:300])
    audit(actor,"sync.requested",job,reason,direction=direction,preparation=str(cfg.preparation_id))
    return job,True


def sync_summary(trailer,preparation):
    from django.db.models import Count,Max,Min
    rows=SyncOutboxItem.objects.filter(preparation=preparation) if preparation else SyncOutboxItem.objects.none()
    counts={r["status"]:r["n"] for r in rows.values("status").annotate(n=Count("pk"))}
    return {"pending":counts.get("pending",0),"sending":counts.get("in_flight",0),"failed":counts.get("failed",0),"sent":counts.get("sent",0),"last_accepted":rows.aggregate(t=Max("accepted_at"))["t"],"next_retry":rows.filter(status__in=("pending","failed")).aggregate(t=Min("next_attempt_at"))["t"],
            "download":CentralEventDownload.objects.filter(trailer_identity=trailer.identity,event_id=preparation.event_id).first() if preparation else None,
            "jobs":SyncRunRequest.objects.filter(trailer=trailer).order_by("-created_at")[:8]}
