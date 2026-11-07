"""Audited reporting transitions; original domain records remain authoritative."""
from decimal import Decimal
from uuid import UUID
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.contrib.auth import get_user_model
from apps.accounts.roles import lock_role_groups
from apps.core.audit import record_audit
from apps.events.models import Event
from apps.event_reports.services import require_manage
from apps.participants.identity import fresh_identity_context
from .access import require_tab, require_finalizer
from .definitions import definition_snapshot, METRICS
from .metrics import event_detail, event_people
from .models import EventReport, EventReportVersion, ManualBusinessFigure, CompensationAdjustment, snapshot_hash
from .payroll import reason
from .locks import compensation_lock


def json_value(value):
    if isinstance(value,(Decimal,UUID)):return str(value)
    if hasattr(value,"isoformat"):return value.isoformat()
    if isinstance(value,dict):return {str(k):json_value(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [json_value(v) for v in value]
    return value


def event_snapshot(actor,event):
    from apps.workdays.models import Workday,MileageTrip
    from apps.event_reports.models import ManualExpense,IncentiveLine
    data=event_detail(actor,event)
    data.pop("event")
    cost=data["cost"];cost.pop("review",None)
    for k in ["score_sum"]:data.pop(k,None)
    figures=[{"label":r.label,"value":str(r.value),"unit":r.unit,"source_note":r.source_note,"recorded_by":r.recorded_by.get_username(),"recorded_at":r.updated_at.isoformat(),"provenance":"Authorized manual business figure"} for r in ManualBusinessFigure.objects.filter(event=event,is_active=True).select_related("recorded_by").order_by("label","pk")]
    # No participant PII is duplicated into financial/final report snapshots. Authorized participant drill-down stays live and explicitly separate.
    sources=[]
    for kind,qs,fields in [
        ("workday",Workday.objects.filter(event=event),("id","staff_id","work_date","is_active","approval","pay_amount","per_diem_amount","pay_rate_id","per_diem_rate_id")),
        ("mileage",MileageTrip.objects.filter(event=event),("id","staff_id","claim_date","status","round_trip_miles","rate_amount","amount")),
        ("expense",ManualExpense.objects.filter(event=event),("id","expense_date","category_name","treatment","amount","is_active","reference")),
        ("distributed_incentive",IncentiveLine.objects.filter(event=event),("id","expense_date","unit_value","quantity","is_active")),
        ("compensation_adjustment",CompensationAdjustment.objects.filter(event=event),("id","staff_id","occurred_on","kind","amount","is_active","reference"))]:
        extra=("staff__first_name","staff__last_name") if "staff_id" in fields else ()
        for row in qs.order_by("pk").values(*fields,*extra).iterator():
            if extra:row["contractor"]=(row.pop("staff__first_name")+" "+row.pop("staff__last_name")).strip()
            if kind=="workday":row["amount"]=row["pay_amount"]+row["per_diem_amount"] if row["pay_amount"] is not None and row["per_diem_amount"] is not None else None
            if kind=="distributed_incentive":row["amount"]=row["unit_value"]*row["quantity"]
            row["source_state"]=str(row.get("approval") or row.get("status") or row.get("treatment") or "Recorded")+(" · void/inactive" if row.get("is_active") is False else "")
            sources.append({"source_type":kind,**json_value(row)})
    return json_value({"report":"Event Performance & Cost Report","event_id":str(event.pk),"event":event.name,"event_start":event.start_at,"event_end":event.end_at,
        "event_timezone":event.timezone_name,"region":event.state,"threshold":event.simulator_qualification_threshold,"data":data,
        "definitions":definition_snapshot(),"manual_business_figures":figures,"financial_sources":sources,
        "scope":"Cost only; no revenue or profit. Calculated, manual and missing figures remain distinct."})


def report_state(event,expected_revision):
    row,_=EventReport.objects.get_or_create(event=event)
    row=EventReport.objects.select_for_update().get(pk=row.pk)
    if row.revision!=expected_revision:raise ValidationError("The report changed. Reload the latest version.")
    return row


def finalize_event(*,actor,event_id,expected_revision,why):
    with transaction.atomic():
        lock_role_groups();actor=require_finalizer(actor);why=reason(why)
        event=Event.objects.select_for_update().get(pk=event_id)
        state=report_state(event,expected_revision)
        if state.state in ("finalized","refinalized"):raise ValidationError("Reopen with a reason before finalizing another version.")
        with fresh_identity_context():data=event_snapshot(actor,event)
        number=(event.analytics_report_versions.aggregate(n=Max("number"))["n"] or 0)+1
        version=EventReportVersion.objects.create(event=event,number=number,snapshot=data,content_hash=snapshot_hash(data),finalized_by=actor,reason=why)
        previous=state.state;state.state="finalized" if number==1 else "refinalized";state.current_version=version;state.revision+=1;state.save()
        record_audit(actor=actor,action="reporting.event.finalized",instance=version,source="build17",old_data={"state":previous},new_data={"state":state.state,"version":number,"hash":version.content_hash,"threshold":str(event.simulator_qualification_threshold)},reason=why)
        return version


def reopen_event(*,actor,event_id,expected_revision,why):
    with transaction.atomic():
        lock_role_groups();actor=require_finalizer(actor);why=reason(why)
        event=Event.objects.select_for_update().get(pk=event_id);state=report_state(event,expected_revision)
        if state.state not in ("finalized","refinalized"):raise ValidationError("Only finalized reports can be reopened.")
        previous=state.state;state.state="reopened";state.revision+=1;state.save()
        record_audit(actor=actor,action="reporting.event.reopened",instance=state,source="build17",old_data={"state":previous},new_data={"state":state.state,"retained_version":str(state.current_version_id)},reason=why)
        return state


def prepare_event(*,actor,event_id,expected_revision,why):
    with transaction.atomic():
        lock_role_groups();actor=require_tab(actor,"events");why=reason(why)
        event=Event.objects.select_for_update().get(pk=event_id);state=report_state(event,expected_revision)
        if state.state in ("finalized","refinalized"):raise ValidationError("An authorized finalizer must reopen this report first.")
        state.revision+=1;state.save()
        record_audit(actor=actor,action="reporting.event.prepared",instance=state,source="build17",new_data={"state":state.state,"revision":state.revision},reason=why)
        return state


def verify_event_version(version):
    if snapshot_hash(version.snapshot)!=version.content_hash:raise ValidationError("Historical event report integrity check failed.")
    return version.snapshot


def save_manual_figure(*,actor,event_id,data,figure_id=None):
    with transaction.atomic():
        lock_role_groups();actor=require_tab(actor,"events");require_manage(actor)
        event=Event.objects.select_for_update().get(pk=event_id)
        if EventReport.objects.filter(event=event,state__in=("finalized","refinalized")).exists():raise ValidationError("Reopen the report before changing manual business figures.")
        reserved={"".join(c for c in m.label.lower() if c.isalnum()) for m in METRICS}|{m.key.replace("_","") for m in METRICS}|{"totalleads","simulatorplays","qualifiedleads","registrations","attendance","totaleventcost","eventcosts","average simulatorscore".replace(" ","")}
        label=str(data.get("label","")).strip();key="".join(c for c in label.lower() if c.isalnum())
        if not label or key in reserved or any(token in key for token in ("lead","simulator","registration","qualified","score","cost","payroll","revenue","profit","billing","spend")):raise ValidationError("Manual business figures cannot replace calculated platform metrics.")
        if len(label)>120:raise ValidationError("Use a label up to 120 characters.")
        why=reason(data.get("reason"));note=reason(data.get("source_note"));unit=str(data.get("unit","")).strip()
        if not unit or len(unit)>40:raise ValidationError("Enter a unit up to 40 characters.")
        row=ManualBusinessFigure.objects.select_for_update().get(pk=figure_id,event=event) if figure_id else ManualBusinessFigure(event=event)
        if row.revision!=int(data.get("revision",0)):raise ValidationError("This figure changed. Reload it.")
        before={"label":row.label,"value":str(row.value),"unit":row.unit,"source_note":row.source_note,"recorded_by":str(row.recorded_by_id),"reason":row.reason,"revision":row.revision} if figure_id else None
        row.label=label;row.value=data["value"];row.unit=unit;row.source_note=note;row.reason=why;row.recorded_by=actor;row.revision+=1;row.full_clean();row.save()
        record_audit(actor=actor,action="reporting.manual_business_figure.saved",instance=row,source="build17",old_data=before,new_data={"label":label,"value":str(row.value),"unit":unit,"source_note":note,"recorded_by":str(actor.pk),"provenance":"manual","revision":row.revision},reason=why)
        return row


def add_adjustment(*,actor,data):
    with compensation_lock():
        actor=require_tab(actor,"payroll");require_manage(actor)
        event=Event.objects.select_for_update().get(pk=data["event"].pk)
        day=data.get("workday")
        if day:
            from apps.workdays.models import Workday
            day=Workday.objects.select_for_update().get(pk=day.pk)
            if not day.is_active or day.staff_id!=data["staff"].pk or day.event_id!=event.pk or day.work_date!=data["occurred_on"]:
                raise ValidationError("The linked recorded workday must match the contractor, event and date.")
        if CompensationAdjustment.objects.filter(operation_id=data["operation_id"]).exists():raise ValidationError("This adjustment has already been recorded.")
        row=CompensationAdjustment(event=event,staff=data["staff"],workday=day,occurred_on=data["occurred_on"],kind=data["kind"],amount=data["amount"],reason=reason(data["reason"]),reference=str(data["reference"]).strip(),operation_id=data["operation_id"],recorded_by=actor)
        if not row.reference:raise ValidationError("Enter the attributable financial reference.")
        row.full_clean();row.save()
        record_audit(actor=actor,action="reporting.compensation_adjustment.recorded",instance=row,source="build17",new_data={"event":str(event.pk),"staff":str(row.staff_id),"amount":str(row.amount),"kind":row.kind,"reference":row.reference},reason=row.reason)
        return row


def void_adjustment(*,actor,adjustment_id,expected_revision,why):
    with compensation_lock():
        actor=require_tab(actor,"payroll");require_manage(actor)
        first=CompensationAdjustment.objects.get(pk=adjustment_id);Event.objects.select_for_update().get(pk=first.event_id)
        row=CompensationAdjustment.objects.select_for_update().get(pk=adjustment_id)
        if row.revision!=expected_revision or not row.is_active:raise ValidationError("This adjustment changed or is already void.")
        why=reason(why);row.is_active=False;row.revision+=1;row.reason=why;row.save()
        record_audit(actor=actor,action="reporting.compensation_adjustment.voided",instance=row,source="build17",old_data={"active":True},new_data={"active":False,"amount":str(row.amount)},reason=why)
        return row
