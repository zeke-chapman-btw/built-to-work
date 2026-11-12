"""Bounded shared workspace projections; exports use the same report rows."""
from collections import defaultdict
from calendar import monthrange
from zoneinfo import ZoneInfo
from decimal import Decimal
from urllib.parse import urlencode
from django.core.paginator import Paginator
from django.db.models import Count,Sum,Q,F,DecimalField,ExpressionWrapper,Exists,OuterRef
from django.db.models.functions import TruncMonth
from django.urls import reverse
from apps.events.models import Event
from apps.workdays.models import Workday,MileageTrip
from apps.workdays.reports import totals as staff_totals
from apps.event_reports.models import ManualExpense,IncentiveLine
from .access import allowed_tabs
from .models import PayrollPeriod,EventReport,CompensationAdjustment,PayrollSubmission
from . import metrics,definitions
from .metrics import chart
from .payroll import submission_status
ZERO=Decimal("0.00")
TAB_LABELS=[("highlights","Highlights"),("events","Events"),("payroll","Payroll"),("participants","Participants"),("customers","Customers"),("opportunities","Opportunities"),("hires","Hires")]


def card(label,value,detail,url=None,kind="number"):
    return {"label":label,"value":value,"detail":detail,"url":url,"kind":kind}


def table(columns,rows):
    return {"columns":columns,"rows":[{"cells":[{"value":r.get(key),"kind":kind,"url":r.get("url") if index==0 else None} for index,(key,label,kind) in enumerate(columns)]} for r in rows]}


def page_rows(qs,request,export):
    if export:return qs,None
    page=Paginator(qs,20).get_page(request.GET.get("page"))
    return page.object_list,page


def serialize_filters(filters):
    return {k:(str(v.pk) if hasattr(v,"pk") else str(v)) for k,v in filters.items() if v not in (None,"")}


def recorded_cost(events):
    days=Workday.objects.filter(event__in=events);trips=MileageTrip.objects.filter(event__in=events)
    staff=staff_totals(days,trips)
    operating=ManualExpense.objects.filter(event__in=events,is_active=True,treatment="cost").aggregate(n=Sum("amount"))["n"] or ZERO
    incentives=IncentiveLine.objects.filter(event__in=events,is_active=True).aggregate(n=Sum(ExpressionWrapper(F("unit_value")*F("quantity"),output_field=DecimalField(max_digits=18,decimal_places=2))))["n"] or ZERO
    adjustments=CompensationAdjustment.objects.filter(event__in=events,is_active=True).aggregate(n=Sum("amount"))["n"] or ZERO
    return sum(staff.values(),ZERO)+operating+incentives+adjustments


def finalized_ratios(events):
    rows=EventReport.objects.filter(event__in=events,state__in=("finalized","refinalized"),current_version__isnull=False).values_list(
        "current_version__snapshot__data__cost__total","current_version__snapshot__data__leads","current_version__snapshot__data__qualified")
    count=0;cost=ZERO;leads=0;qualified=0;complete=True;qualification_known=True
    for amount,n,q in rows.iterator():
        count+=1
        if amount is None:complete=False
        else:cost+=Decimal(str(amount))
        leads+=int(n or 0)
        if q is None:qualification_known=False
        else:qualified+=int(q)
    return count,metrics.ratio(cost,leads) if count and complete else None,metrics.ratio(cost,qualified) if count and complete and qualification_known else None


def event_summary(actor,events,filters):
    rows=metrics.event_metrics(actor,events)
    def total(key):
        values=[r[key] for r in rows]
        return sum(values) if all(v is not None for v in values) and (values or key not in ("qualified","interested") or definitions.INTEREST_MAPPING is not None) else None
    monthly=defaultdict(lambda:{"leads":0,"qualified":0})
    for row in rows:
        month=row["event"].start_at.astimezone(ZoneInfo(definitions.REPORT_TIMEZONE)).strftime("%Y-%m");monthly[month]["leads"]+=row["leads"]
        if row["qualified"] is not None:monthly[month]["qualified"]+=row["qualified"]
    charts=[chart("Event lead trends",[{"label":k,"count":v["leads"],"url":reverse("internal:reports")+"?"+urlencode({**serialize_filters(filters),"tab":"events","start":k+"-01","end":k+"-"+str(monthrange(int(k[:4]),int(k[5:]))[1])})} for k,v in sorted(monthly.items())[-24:]],"Unique person-event leads; repeated registrations do not multiply people. Most recent 24 recorded months.")]
    if definitions.INTEREST_MAPPING:charts.append(chart("Qualified leads over time",[{"label":k,"count":v["qualified"]} for k,v in sorted(monthly.items())[-24:]],"Each participant must meet their event threshold and the approved job-interest rule."))
    return rows,{"leads":total("leads") if rows else 0,"qualified":total("qualified"),"plays":total("plays") if rows else 0,"interested":total("interested")},charts


def workspace_data(actor,tab,filters,request,*,export=False):
    tabs=allowed_tabs(actor);data={"cards":[],"charts":[],"notes":[],"columns":[],"report_rows":[],"page":None,"title":dict(TAB_LABELS)[tab],"source_detail":""}
    def add(label,value,detail,url=None,kind="number"):data["cards"].append(card(label,value,detail,url,kind))
    if tab in ("highlights","events") and "events" in tabs and not filters.get("customer") and not filters.get("campaign") and not filters.get("opportunity"):
        events=metrics.event_cohort(actor,filters);event_rows,counts,charts=event_summary(actor,events,filters)
        add("Events conducted",events.filter(Q(status="completed")|Q(status="archived",execution__actual_ended_at__isnull=False)).count(),"Explicit completed lifecycle, including archived events with retained actual-end evidence. Dates alone do not prove completion.",reverse("internal:reports")+"?tab=events")
        add("Unique event leads",counts["leads"],"One canonical person per event; this is not attendance.",reverse("internal:reports")+"?tab=events")
        add("Qualified leads",counts["qualified"],"Event threshold AND Yes/Maybe interest under the approved source policy.",reverse("internal:reports")+"?tab=events")
        add("Qualification rate",metrics.ratio(counts["qualified"],counts["leads"],True),"Qualified leads / the same cohort of unique event leads; percent, not attendance.",kind="percent")
        add("Completed simulator plays",counts["plays"],"Accepted official captures; invalid/test/replaced sources excluded.")
        data["charts"]+=charts
        if tab=="highlights":
            add("Recorded event costs",recorded_cost(events),"Recorded approved source amounts; reconciliation/completeness may still be pending.",reverse("internal:reports")+"?tab=events","money")
            monthly_cost=defaultdict(lambda:ZERO)
            for start,amount in EventReport.objects.filter(event__in=events,state__in=("finalized","refinalized"),current_version__isnull=False).values_list("event__start_at","current_version__snapshot__data__cost__total").iterator():
                if amount is not None:monthly_cost[start.astimezone(ZoneInfo(definitions.REPORT_TIMEZONE)).strftime("%Y-%m")]+=Decimal(str(amount))
            cost_chart=chart("Finalized event cost trend",[{"label":month,"count":amount} for month,amount in sorted(monthly_cost.items())[-24:]],"USD, complete finalized snapshot totals grouped by event start month. Draft/incomplete costs are excluded.")
            cost_chart["money"]=True;data["charts"].append(cost_chart)
            finalized,cpl,cpq=finalized_ratios(events)
            add("Cost per lead · finalized",cpl,str(finalized)+" finalized reports; numerator and denominator from the same frozen versions.",reverse("internal:reports")+"?tab=events","money")
            add("Cost per qualified lead · finalized",cpq,"Unavailable if frozen costs are incomplete, interest is unknown or denominator is zero.",reverse("internal:reports")+"?tab=events","money")
        else:
            by_id={str(r["event"].pk):r for r in event_rows}
            records,data["page"]=page_rows(events.select_related("analytics_report"),request,export)
            rows=[]
            for e in records:
                row=by_id[str(e.pk)];state=getattr(e,"analytics_report",None)
                rows.append({"event":e.name,"start":e.local_start_at.strftime("%b %d, %Y"),"leads":row["leads"],"qualified":row["qualified"],"average":row["average_score"],"state":state.get_state_display() if state else "Draft","url":reverse("reporting:event",args=[e.pk])})
            data["columns"]=[("event","Event","text"),("start","Start","text"),("leads","Unique leads","number"),("qualified","Qualified leads","number"),("average","Average best score %","number"),("state","Report state","text")];data["report_rows"]=rows
            data["source_detail"]="Date filters select event starts in America/New_York; each selected event retains its complete event associations. Open a report for source records, financial reconciliation and historical versions."
    elif tab=="highlights" and "events" in tabs:
        data["notes"].append("Event metrics are unavailable for the selected customer/campaign filter: the application has no authoritative customer-event relationship.")
    if tab in ("highlights","participants") and "participants" in tabs and not filters.get("customer") and not filters.get("campaign") and not filters.get("opportunity"):
        population,intelligence,criteria=metrics.participant_projection(actor,filters)
        if tab=="participants":
            data["cards"]=[card(m["label"],m["value"],m["detail"]) for m in intelligence["metrics"]]
            from apps.participants.discovery import segment_criteria
            from urllib.parse import parse_qs,urlsplit
            data["charts"]=intelligence["charts"]
            for graph in data["charts"]:
                dimension=graph.get("dimension")
                for bar in graph["rows"]:
                    query={**serialize_filters(filters),"tab":"participants"}
                    if "bucket" in bar and dimension:
                        if dimension=="travel":query["travel"]=bar["bucket"]
                        else:
                            segment=segment_criteria({"segments":query.get("segments","")},dimension,bar["bucket"])
                            query["segments"]=segment.get("segments","")
                    elif bar.get("url"):
                        original=parse_qs(urlsplit(bar["url"]).query)
                        for key in ("assessment","score_min","score_max","segments"):
                            if key in original:query[key]=original[key][-1]
                    else:continue
                    bar["url"]=reverse("internal:reports")+"?"+urlencode(query)
            records,data["page"]=page_rows(population.select_related("career_profile","acquisition"),request,export)
            rows=[]
            for p in records:
                acq=getattr(p,"acquisition",None)
                rows.append({"participant":str(p),"identifier":p.get_primary_identifier_display() or "Unclassified","region":p.state or "Not recorded","source":acq.get_channel_display() if acq else "Unknown / unverified","acquired":acq.acquired_at.isoformat() if acq and acq.acquired_at else None,"url":reverse("participants:staff-profile",args=[p.pk])})
            data["columns"]=[("participant","Participant","text"),("identifier","Identifier","text"),("region","State / region","text"),("source","Original source","text"),("acquired","Original acquisition","text")];data["report_rows"]=rows
            data["source_detail"]="Existing canonical discovery and verification services. Date filters and acquisition-month groups use Eastern time. Missing stages and historical dates are not inferred. Registration, participation, assessment and hiring remain distinct."
            data["notes"].append("Skills, certifications and score-band groups may overlap. These are recorded evidence, not a new suitability or matching score.")
        else:
            add("Participant acquisitions" if filters.get("start") or filters.get("end") else "Active participants",population.count(),"Current canonical directory cohort; original-acquisition date filters use existing UTC discovery semantics. Unknown dates are not inferred.",reverse("internal:reports")+"?tab=participants")
            data["charts"] += [g for g in intelligence["charts"] if g.get("dimension") in ("acquisition_month","participation")]
    if tab in ("highlights","opportunities") and "opportunities" in tabs and not filters.get("event"):
        opps,candidates,counts,stages,months=metrics.recruiting_metrics(actor,filters)
        add("Active opportunities",counts["active_opportunities"],"Authorized active positions with approved revisions.",reverse("internal:reports")+"?tab=opportunities")
        add("Requested workers",counts["requested_positions"],"Recorded approved worker quantities; missing quantity remains unavailable.")
        add("Active candidate engagements",counts["candidates"],"Current candidate engagements within your owner/collaborator opportunity scope.")
        data["charts"].append(chart("Recruiting pipeline",[{"label":r["stage__label"],"count":r["count"]} for r in stages],"Existing recorded stages only; stage labels are not invented qualification or hiring outcomes."))
        data["charts"].append(chart("Opportunity creation trend",[{"label":r["month"].strftime("%b %Y"),"count":r["count"]} for r in months[-24:]],"Opportunity creation dates, not inferred campaign starts."))
        if tab=="opportunities":
            records,data["page"]=page_rows(opps.select_related("need__campaign","need__organization","need__effective_revision").annotate(candidate_total=Count("candidates",filter=Q(candidates__merged_into__isnull=True),distinct=True)),request,export)
            data["report_rows"]=[{"position":o.need.effective_revision.title if o.need.effective_revision else "Approved position not recorded","customer":o.need.organization.name,"campaign":o.need.campaign.name if o.need.campaign_id else "General recruiting","state":o.need.get_status_display(),"quantity":o.need.effective_revision.worker_quantity if o.need.effective_revision else None,"candidates":o.candidate_total,"url":reverse("recruiting:matching",args=[o.pk])} for o in records]
            data["columns"]=[("position","Position","text"),("customer","Customer","text"),("campaign","Campaign","text"),("state","Position state","text"),("quantity","Workers requested","number"),("candidates","Candidate engagements","number")]
            data["source_detail"]="Opportunity creation date cohort; current position state and all retained unmerged candidate engagements are labelled separately. General candidates, campaign sources and employer-specific engagements are not interchangeable."
    if tab in ("highlights","hires") and "hires" in tabs and not filters.get("event"):
        hires=metrics.confirmed_hires(actor,filters)
        add("Confirmed hires",hires.count(),"Explicit BTW-confirmed hired outcomes with attributable confirmation evidence.",reverse("internal:reports")+"?tab=hires")
        groups=list(hires.values("opportunity__need__organization__name").annotate(count=Count("pk",distinct=True)).order_by("-count")[:10])
        data["charts"].append(chart("Confirmed hires by customer",[{"label":g["opportunity__need__organization__name"],"count":g["count"]} for g in groups],"Top ten authorized customers; unconfirmed employer feedback, selection and releases are excluded."))
        hire_months=list(hires.annotate(month=TruncMonth("confirmed_on",tzinfo=ZoneInfo(definitions.REPORT_TIMEZONE))).values("month").annotate(count=Count("pk")).order_by("month"))
        data["charts"].append(chart("Confirmed hiring trend",[{"label":r["month"].strftime("%b %Y"),"count":r["count"]} for r in hire_months[-24:]],"Explicit confirmation timestamp in America/New_York; no date is inferred from pipeline position."))
        if tab=="hires":
            records,data["page"]=page_rows(hires,request,export)
            data["report_rows"]=[{"participant":str(h.participant),"customer":h.opportunity.need.organization.name,"campaign":h.opportunity.need.campaign.name if h.opportunity.need.campaign_id else "General recruiting","position":h.opportunity.need.effective_revision.title if h.opportunity.need.effective_revision else "Not recorded","confirmed":h.confirmed_on.isoformat(),"elapsed":round((h.confirmed_on-h.created_at).total_seconds()/86400,2) if h.confirmed_on and h.confirmed_on>=h.created_at else None,"url":reverse("recruiting:candidate-detail",args=[h.pk])} for h in records]
            data["columns"]=[("participant","Participant","text"),("customer","Customer","text"),("campaign","Campaign","text"),("position","Position","text"),("confirmed","BTW confirmation","text"),("elapsed","Selection-to-confirmation days","number")]
            data["source_detail"]="Date filters apply to the actual confirmation audit timestamp. Elapsed days are from candidate engagement creation to confirmation; no application or employment-start date is fabricated."
    if tab in ("highlights","customers") and "customers" in tabs and not filters.get("event"):
        customers,needs,counts=metrics.customer_projection(actor,filters)
        add("Active customers",counts["active_customers"],"Current active organizations; date filters apply to activity records below.",reverse("internal:reports")+"?tab=customers")
        add("Workforce requests",counts["workforce_requests"],"Recorded request groups created within the selected period.")
        add("Open approved positions",counts["active_positions"],"Approved active positions; not an inferred count of still-unfilled seats.")
        status=list(needs.values("status").annotate(count=Count("pk")).order_by("status"))
        data["charts"].append(chart("Customer position status",[{"label":g["status"].replace("_"," ").title(),"count":g["count"]} for g in status],"Current state of positions created in the reporting period."))
        activity=list(needs.annotate(month=TruncMonth("created_at",tzinfo=ZoneInfo(definitions.REPORT_TIMEZONE))).values("month").annotate(count=Count("pk")).order_by("month"))
        data["charts"].append(chart("Customer position activity",[{"label":r["month"].strftime("%b %Y"),"count":r["count"]} for r in activity[-24:]],"Recorded position creation; this is not spending, revenue or inferred hiring."))
        if tab=="customers":
            records,data["page"]=page_rows(customers.annotate(position_count=Count("workforce_needs",distinct=True),request_count=Count("workforce_requests",distinct=True)),request,export)
            data["report_rows"]=[{"customer":c.name,"state":c.get_status_display(),"positions":c.position_count,"requests":c.request_count,"url":reverse("customers:workspace",args=[c.pk,"overview"])} for c in records]
            data["columns"]=[("customer","Customer","text"),("state","Current state","text"),("positions","Retained position records","number"),("requests","Retained request groups","number")]
            data["source_detail"]="Customer rows show retained organization totals; activity KPIs and charts use the selected source-creation period. Open the source workspace for full position/request history."
            data["notes"].extend(["Customer-associated events: unavailable because no authoritative customer-event relationship exists.","Qualified-candidate and open-seat totals: unavailable without a reliable recorded common outcome/filled-seat definition. Existing position matching remains available; event qualification is not substituted.","Revenue, customer spend, invoices and profitability are outside event-cost reporting."])
    if tab=="payroll":
        qs=metrics.date_scope(PayrollPeriod.objects.select_related("current_version").defer("current_version__snapshot").annotate(
            reported_total=F("current_version__snapshot__totals__total"),
            report_current_submitted=Exists(PayrollSubmission.objects.filter(version_id=OuterRef("current_version_id"))),
            report_prior_submitted=Exists(PayrollSubmission.objects.filter(version__period_id=OuterRef("pk")).exclude(version_id=OuterRef("current_version_id")))) ,"starts_on",filters,date_field=True)
        records,data["page"]=page_rows(qs,request,export)
        rows=[]
        for p in records:
            rows.append({"week":str(p.starts_on)+" – "+str(p.ends_on),"state":p.get_state_display(),"version":p.current_version.number if p.current_version_id else None,"total":p.reported_total if p.current_version_id else None,"ap":submission_status(p),"url":reverse("reporting:payroll",args=[p.starts_on.isoformat()])})
        data["report_rows"]=rows;data["columns"]=[("week","Monday–Sunday period","text"),("state","State","text"),("version","Version","number"),("total","Reported amount USD","money"),("ap","A/P submission","text")]
        add("Recorded weekly periods",qs.count(),"Nonoverlapping Monday–Sunday periods; date filters select week starts.")
        add("Approved periods",qs.filter(state="approved").count(),"Explicit designated-Administrator approval, not payment status.")
        add("Under correction",qs.filter(state="reopened").count(),"Previous approved versions and submissions are retained.")
        data["charts"]=[chart("Weekly payroll state",[{"label":r["state"].title(),"count":r["count"]} for r in qs.values("state").annotate(count=Count("pk"))],"1099 contractor reporting only. No tax withholding, overtime, net-pay or payment engine.")]
        data["notes"].append("Payroll reminders and earnings-statement delivery remain deferred to Build 18. A/P submission tracking records a manual action; it sends no email.")
        data["source_detail"]="Compensation and reimbursements remain separate. Open a week to compile, approve, correct, export or record its manual A/P submission."
    if definitions.INTEREST_MAPPING is None and tab in ("highlights","events"):
        data["notes"].append("Interested-in-work and Qualified Lead metrics are unavailable pending the owner decision on mapping the existing employment-interest values. No mapping has been assumed.")
    if not data["cards"] and tab=="highlights":data["notes"].append("No business reporting sources are available under your current permissions. Your private My Pay area remains separate.")
    from urllib.parse import urlsplit,parse_qs
    base=reverse("internal:reports")
    scopes={"events":{"start","end","event","region"},"participants":{"start","end","event","region","identifier","segments","travel","assessment","score_min","score_max"},"customers":{"start","end","customer","region","campaign","opportunity"},"opportunities":{"start","end","customer","region","campaign","opportunity"},"hires":{"start","end","customer","region","campaign","opportunity"},"payroll":{"start","end"}}
    for item in data["cards"]:
        if not item.get("url") or urlsplit(item["url"]).path!=base:continue
        original={key:values[-1] for key,values in parse_qs(urlsplit(item["url"]).query).items()};target=original.get("tab","")
        original.update({key:value for key,value in serialize_filters(filters).items() if key in scopes.get(target,set())})
        item["url"]=base+"?"+urlencode(original)
    data["table"]=table(data["columns"],data["report_rows"])
    return data
