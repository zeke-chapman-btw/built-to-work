import hashlib
import json
from django.db.models import F
from apps.simulators.models import SimulatorCapture
from .models import PublicationGrant


def leaderboard_data(binding):
    event=binding.preparation.event
    base={"event":event.name,"groups":[],"scope":"Accepted official local simulator captures only","central_result_sync":False}
    if event.status in ("draft","cancelled","archived") or not binding.active:return base
    grants=dict(PublicationGrant.objects.filter(event=event,published=True,participant__kind="person",participant__archived_at__isnull=True).values_list("participant_id","alias"))
    rows=SimulatorCapture.objects.filter(event=event,status__in=("matched","resolved"),mode="official",is_official=True,voided_at__isnull=True,total_score__gte=0,total_score__lte=100,
        participant_id__in=grants,registration__event=event,registration__status="active",registration__participant_id=F("participant_id"),
        experience_session__event=event,experience_session__mode="official",experience_session__registration_id=F("registration_id"),experience_session__participant_id=F("participant_id")).select_related("station","profile")
    if binding.profile_station_id:rows=rows.filter(station_id=binding.profile_station_id)
    groups={}
    for row in rows.order_by("station_id","profile_version","-total_score","received_at","id"):
        # Immutable captured snapshot is part of compatibility, not just a shared percent unit.
        snapshot=json.dumps(row.profile_snapshot,sort_keys=True,separators=(",",":"))
        key=(str(row.station_id),str(row.profile_id),row.profile_version,hashlib.sha256(snapshot.encode()).hexdigest())
        group=groups.setdefault(key,{"simulator":row.station.name,"profile":row.profile.name,"version":row.profile_version,"rows":[]})
        group["rows"].append({"alias":grants[row.participant_id],"score":str(row.total_score)})
    for group in groups.values():
        prior=None;rank=0
        for index,row in enumerate(group["rows"],1):
            if row["score"]!=prior:rank=index
            prior=row["score"];row["rank"]=rank
        group["rows"]=group["rows"][:5]
        base["groups"].append(group)
    return base
