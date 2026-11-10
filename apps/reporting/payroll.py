"""1099 reporting from recorded financial sources. No payment or delivery engine."""
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Max, Sum
from django.utils import timezone
from apps.core.audit import record_audit
from apps.workdays.models import Workday, MileageTrip
from apps.workdays.services import require_manager
from apps.staffing.models import StaffProfile
from .access import staff, require_payroll_approver
from .definitions import CALCULATION_VERSION, REPORT_TIMEZONE
from .locks import compensation_lock
from .models import PayrollPeriod, PayrollVersion, PayrollApproval, PayrollSubmission, CompensationAdjustment, ContractorStatement, snapshot_hash
ZERO = Decimal("0.00")


def reason(value):
    value = str(value or "").strip()
    if not value or len(value)>500: raise ValidationError("Enter an attributable reason up to 500 characters.")
    return value


def payroll_sources(starts_on):
    if starts_on.weekday()!=0: raise ValidationError("Choose a Monday; periods end Sunday.")
    end = starts_on + timedelta(days=6)
    days = Workday.objects.filter(work_date__range=(starts_on,end),is_active=True).select_related("staff","event").order_by("work_date","staff_id","pk")
    trips = MileageTrip.objects.filter(claim_date__range=(starts_on,end)).exclude(status="void").select_related("staff","event").order_by("claim_date","staff_id","pk")
    adjustments = CompensationAdjustment.objects.filter(occurred_on__range=(starts_on,end),is_active=True).select_related("staff","event").order_by("occurred_on","staff_id","pk")
    return days,trips,adjustments


def compile_data(starts_on):
    days,trips,adjustments = payroll_sources(starts_on)
    rows=[];issues=[]
    def row(kind,obj,day,**amounts):
        entry={"source_key":kind+":"+str(obj.pk),"source_kind":kind,"source_id":str(obj.pk),"source_updated_at":obj.updated_at.isoformat(),
            "staff_id":str(obj.staff_id),"contractor":str(obj.staff),"event_id":str(obj.event_id),"event":obj.event.name,"occurred_on":day.isoformat(),
            "base":"0.00","per_diem":"0.00","mileage":"0.00","incentives":"0.00","adjustments":"0.00",**amounts}
        entry["total"]=str(sum((Decimal(entry[k]) for k in ("base","per_diem","mileage","incentives","adjustments")),ZERO)) if all(entry[k] is not None for k in ("base","per_diem","mileage","incentives","adjustments")) else None
        rows.append(entry)
    for d in days.iterator():
        if d.approval!="approved" or d.pay_amount is None or d.per_diem_amount is None:
            issues.append("Workday "+str(d.pk)+" requires existing daily compensation review/approval or a missing rate.")
        row("workday",d,d.work_date,base=str(d.pay_amount) if d.pay_amount is not None else None,
            per_diem=str(d.per_diem_amount) if d.per_diem_amount is not None else None,source_status=d.approval,
            role=d.role_name,pay_rate_id=str(d.pay_rate_id or ""),per_diem_rate_id=str(d.per_diem_rate_id or ""),effective_checkin=d.effective_checkin_at.isoformat())
    for t in trips.iterator():
        if t.status!="approved" or t.amount is None: issues.append("Mileage trip "+str(t.pk)+" requires existing verification/approval.")
        row("trip",t,t.claim_date,mileage=str(t.amount) if t.amount is not None else None,source_status=t.status,
            round_trip_miles=str(t.round_trip_miles) if t.round_trip_miles is not None else None,rate_per_mile=str(t.rate_amount) if t.rate_amount is not None else None,rate_id=str(t.rate_id or ""))
    for a in adjustments.iterator():
        row("adjustment",a,a.occurred_on,**{("incentives" if a.kind=="incentive" else "adjustments"):str(a.amount)},source_status="Recorded authorized adjustment",reason=a.reason,reference=a.reference)
    keys=[r["source_key"] for r in rows]
    if len(keys)!=len(set(keys)):raise ValidationError("Duplicate compensation source detected.")
    totals={k:(str(sum((Decimal(r[k]) for r in rows),ZERO)) if all(r[k] is not None for r in rows) else None) for k in ("base","per_diem","mileage","incentives","adjustments","total")}
    totals["compensation"]=str(sum((Decimal(totals[k]) for k in ("base","incentives","adjustments")),ZERO)) if all(totals[k] is not None for k in ("base","incentives","adjustments")) else None
    totals["reimbursements"]=str(Decimal(totals["per_diem"])+Decimal(totals["mileage"])) if totals["per_diem"] is not None and totals["mileage"] is not None else None
    data={"report":"Weekly Contractor Compensation","calculation_version":CALCULATION_VERSION,"timezone":REPORT_TIMEZONE,
        "period_start":starts_on.isoformat(),"period_end":(starts_on+timedelta(days=6)).isoformat(),"rows":rows,"totals":totals,
        "issues":issues,"ready":not issues,"model":"1099 contractor reporting; actual workdays and authorized financial records only"}
    return data,snapshot_hash(data)


def verify_version(version):
    if snapshot_hash(version.snapshot)!=version.content_hash:raise ValidationError("Stored report integrity check failed. History has not been changed.")
    return version.snapshot


def _period(starts_on,expected_revision=None):
    obj,_=PayrollPeriod.objects.get_or_create(starts_on=starts_on)
    obj=PayrollPeriod.objects.select_for_update().get(pk=obj.pk)
    if expected_revision is not None and obj.revision!=expected_revision:raise ValidationError("Payroll changed. Reload the current version.")
    return obj


def compile_payroll(*,actor,starts_on,why,expected_revision=None):
    with compensation_lock():
        actor=staff(actor);require_manager(actor);why=reason(why)
        period=_period(starts_on,expected_revision)
        if period.state=="approved":raise ValidationError("A designated Administrator must reopen approved payroll with a reason before recompilation.")
        data,source_hash=compile_data(starts_on)
        number=(period.versions.aggregate(n=Max("number"))["n"] or 0)+1
        version=PayrollVersion.objects.create(period=period,number=number,snapshot=data,content_hash=snapshot_hash(data),source_fingerprint=source_hash,compiled_by=actor,reason=why)
        before={"state":period.state,"revision":period.revision,"version":str(period.current_version_id or "")}
        period.current_version=version;period.state="compiled";period.revision+=1;period.save()
        record_audit(actor=actor,action="reporting.payroll.compiled",instance=version,source="build17",old_data=before,new_data={"period":str(period.pk),"version":number,"hash":version.content_hash,"ready":data["ready"]},reason=why)
        return version


def approve_payroll(*,actor,starts_on,expected_revision,why):
    with compensation_lock():
        actor=require_payroll_approver(actor);why=reason(why);period=_period(starts_on,expected_revision)
        if period.state!="compiled" or not period.current_version_id:raise ValidationError("Compile current payroll before approval.")
        version=period.current_version;data=verify_version(version)
        current,fingerprint=compile_data(starts_on)
        if fingerprint!=version.source_fingerprint:raise ValidationError("Financial sources changed after compilation. Recompile and review before approval.")
        if not data["ready"]:raise ValidationError("Resolve missing rates and existing workday/mileage approvals before weekly approval.")
        if PayrollApproval.objects.filter(version=version).exists():raise ValidationError("This version is already approved.")
        approval=PayrollApproval.objects.create(version=version,approved_by=actor,reason=why)
        grouped={}
        for source_row in data["rows"]:grouped.setdefault(source_row["staff_id"],[]).append(source_row)
        for staff_id,source_rows in grouped.items():
            private=statement_data(approval,source_rows)
            ContractorStatement.objects.create(approval=approval,staff_id=staff_id,snapshot=private,content_hash=snapshot_hash(private))
        period.state="approved";period.revision+=1;period.save()
        record_audit(actor=actor,action="reporting.payroll.approved",instance=approval,source="build17",new_data={"period":str(period.pk),"version":version.number,"hash":version.content_hash},reason=why)
        return approval


def reopen_payroll(*,actor,starts_on,expected_revision,why):
    with compensation_lock():
        actor=require_payroll_approver(actor);why=reason(why);period=_period(starts_on,expected_revision)
        if period.state!="approved":raise ValidationError("Only approved weekly payroll can be reopened.")
        before={"state":period.state,"version":str(period.current_version_id)}
        period.state="reopened";period.correction_reason=why;period.revision+=1;period.save()
        record_audit(actor=actor,action="reporting.payroll.reopened",instance=period,source="build17",old_data=before,new_data={"state":period.state},reason=why)
        return period


def submit_payroll(*,actor,starts_on,expected_revision,why,reference,operation_id):
    with compensation_lock():
        actor=require_payroll_approver(actor);why=reason(why);period=_period(starts_on,expected_revision)
        if period.state!="approved" or not period.current_version_id:raise ValidationError("Only the current approved version may be marked submitted to A/P.")
        if not str(reference or "").strip():raise ValidationError("Enter a submission reference. This records a manual action; it sends no email.")
        verify_version(period.current_version)
        current,fingerprint=compile_data(starts_on)
        if fingerprint!=period.current_version.source_fingerprint:raise ValidationError("Sources changed after approval. Reopen, recompile and reapprove before A/P submission.")
        if not PayrollApproval.objects.filter(version=period.current_version).exists():raise ValidationError("Approval evidence is missing.")
        existing=PayrollSubmission.objects.filter(operation_id=operation_id).first()
        if existing:
            if existing.version_id!=period.current_version_id:raise ValidationError("This submission operation belongs to another version.")
            return existing
        row=PayrollSubmission.objects.create(version=period.current_version,submitted_by=actor,reference=str(reference).strip()[:200],reason=why,operation_id=operation_id)
        record_audit(actor=actor,action="reporting.payroll.ap_submission_recorded",instance=row,source="build17",new_data={"period":str(period.pk),"version":period.current_version.number,"automatic_email":False},reason=why)
        return row


def submission_status(period):
    if period.state=="reopened":return "Under correction — previous approval and any submission history retained"
    if not period.current_version_id:return "Not compiled"
    current=getattr(period,"report_current_submitted",None)
    if current is None:current=period.current_version.submissions.exists()
    if current:return "Submitted — current version"
    prior=getattr(period,"report_prior_submitted",None)
    if prior is None:prior=period.versions.filter(submissions__isnull=False).exclude(pk=period.current_version_id).exists()
    if prior:return "Corrected version requires A/P resubmission"
    return "Not submitted"


def _private_statement(row):
    if snapshot_hash(row.snapshot)!=row.content_hash:raise ValidationError("Stored earnings-statement integrity check failed.")
    data=dict(row.snapshot)
    period=row.approval.version.period
    if period.state!="approved":data["status"]="Under correction — previous approved version"
    elif period.current_version_id!=row.approval.version_id:data["status"]="Superseded approved version"
    return data


def own_statements(actor):
    from .access import own_pay_profile
    profile=own_pay_profile(actor)
    from django.db.models import OuterRef,Subquery
    newest=PayrollApproval.objects.filter(version__period_id=OuterRef("approval__version__period_id")).order_by("-version__number").values("pk")[:1]
    rows=ContractorStatement.objects.filter(staff=profile,approval_id=Subquery(newest)).select_related("approval__version__period","approval__approved_by").defer("approval__version__snapshot").order_by("-approval__version__period_id")
    for row in rows.iterator():yield row.approval,_private_statement(row)


def statement_data(approval,rows):
    # Global payroll totals, other people and unrelated financial sources never enter a private statement response.
    source=approval.version.snapshot
    totals={k:str(sum((Decimal(r[k]) for r in rows if r[k] is not None),ZERO)) for k in ("base","per_diem","mileage","incentives","adjustments","total")}
    totals["compensation"]=str(sum((Decimal(totals[k]) for k in ("base","incentives","adjustments")),ZERO))
    totals["reimbursements"]=str(Decimal(totals["per_diem"])+Decimal(totals["mileage"]))
    return {"report":"Contractor Earnings Statement","period_start":source["period_start"],"period_end":source["period_end"],"timezone":source["timezone"],"rows":rows,"totals":totals,
        "calculation_version":source["calculation_version"],"version":approval.version.number,"approved_at":approval.approved_at.isoformat(),"approver":approval.approved_by.get_username(),
        "status":"Under correction — previous approved version" if approval.version.period.state=="reopened" else "Approved","contractor":rows[0]["contractor"]}


def own_statement(actor,version_id):
    from .access import own_pay_profile
    profile=own_pay_profile(actor)
    row=ContractorStatement.objects.filter(staff=profile,approval__version_id=version_id).select_related("approval__version__period").defer("approval__version__snapshot").first()
    if not row:raise PermissionDenied("This earnings statement is not yours.")
    return _private_statement(row)


def earnings_year(actor,year,*,through=None):
    totals={k:ZERO for k in ("base","per_diem","mileage","incentives","adjustments","total")}
    for approval,data in own_statements(actor):
        for row in data["rows"]:
            if int(row["occurred_on"][:4])==year and (through is None or row["occurred_on"]<=through.isoformat()):
                for key in totals:
                    if row[key] is not None:totals[key]+=Decimal(row[key])
    totals["compensation"]=totals["base"]+totals["incentives"]+totals["adjustments"]
    totals["reimbursements"]=totals["per_diem"]+totals["mileage"]
    return totals
