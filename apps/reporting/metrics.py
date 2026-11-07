"""Permission-scoped, reusable read projections. No workflow or source writes."""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, Max, Sum, F, Q, OuterRef, Subquery, Exists, Value, CharField
from django.db.models.functions import Cast, TruncMonth, Replace
from django.utils import timezone
from django.urls import reverse
from apps.events.models import Event, EventRegistration
from apps.participants.models import Participant, ParticipantCareerProfile
from apps.participants.identity import canonical_records, member_ids, identity_context
from apps.participants.workspace_access import people_for
from apps.event_reports.activity import activity_sources
from apps.event_reports.reporting import costs, ratio, aggregate
from . import definitions
from .access import require_tab, allowed_tabs
from .models import CompensationAdjustment
ZERO = Decimal("0.00")


def date_scope(qs, field, filters, *, date_field=False):
    zone = ZoneInfo(definitions.REPORT_TIMEZONE)
    start, end = filters.get("start"), filters.get("end")
    if date_field:
        if start: qs = qs.filter(**{field+"__gte":start})
        if end: qs = qs.filter(**{field+"__lte":end})
    else:
        if start: qs = qs.filter(**{field+"__gte":datetime.combine(start,time.min,zone)})
        if end: qs = qs.filter(**{field+"__lt":datetime.combine(end+timedelta(days=1),time.min,zone)})
    return qs


def event_cohort(actor, filters):
    require_tab(actor,"events")
    qs = date_scope(Event.objects.filter(admin_preview_fixture__isnull=True),"start_at",filters)
    if filters.get("event"): qs = qs.filter(pk=filters["event"].pk)
    if filters.get("region"): qs = qs.filter(state__iexact=filters["region"].strip())
    # There is no authoritative customer-event relationship in this schema.
    if filters.get("customer"):
        raise PermissionDenied("Customer-associated event reporting requires an authoritative relationship; this filter cannot be inferred.")
    return qs.order_by("-start_at","pk")


def _event_sources(actor, events):
    actor = require_tab(actor,"events")
    if not actor.has_perm("participants.view_participant"):
        raise PermissionDenied("Participant metrics require participant-view permission.")
    population = people_for(actor).filter(admin_preview_fixture__isnull=True)
    regs = canonical_records(EventRegistration.objects.filter(event__in=events,status="active",participant__kind="person"))
    regs = regs.filter(_canonical_person__in=population.values("pk"))
    return population, regs


def event_metrics(actor, events):
    """Database-grouped event/person facts; repeated plays are streamed, not sent to the browser."""
    population, regs = _event_sources(actor,events)
    rows = {str(e.pk):{"event":e,"leads":0,"registrations":0,"interested":None if definitions.INTEREST_MAPPING is None else 0,
        "plays":None,"scored_participants":None,"meeting_threshold":None,"qualified":None,"average_score":None,
        "qualification_rate":None,"interest_missing":0,"score_sum":ZERO} for e in events}
    interest = ParticipantCareerProfile.objects.filter(participant_id=OuterRef("_canonical_person")).values("employment_interest")[:1]
    regs = regs.annotate(_interest=Subquery(interest))
    for group in regs.values("event_id","_interest").annotate(leads=Count("_canonical_person",distinct=True),registrations=Count("pk")).order_by().iterator():
        row = rows[str(group["event_id"])]
        row["leads"] += group["leads"]; row["registrations"] += group["registrations"]
        if not group["_interest"]: row["interest_missing"] += group["leads"]
        if definitions.INTEREST_MAPPING and definitions.INTEREST_MAPPING.get(group["_interest"]) in ("yes","maybe"):
            row["interested"] += group["leads"]
    if not actor.has_perm("simulators.view_simulatorcapture"):
        return list(rows.values())
    caps = canonical_records(activity_sources()["captures"].filter(event__in=events,total_score__gte=0,total_score__lte=100,replaced_by__isnull=True))
    caps = caps.filter(_canonical_person__in=population.values("pk")).annotate(_interest=Subquery(interest))
    for row in rows.values():
        row.update(plays=0,scored_participants=0,meeting_threshold=0,qualified=0 if definitions.INTEREST_MAPPING else None)
    # Submission IDs are unique in the authoritative source. Manual historical scores are not joined.
    for group in caps.values("event_id","_canonical_person","_interest").annotate(highest=Max("total_score"),plays=Count("submission_id",distinct=True)).order_by().iterator(chunk_size=1000):
        row = rows[str(group["event_id"])]; row["plays"] += group["plays"]
        row["scored_participants"] += 1;row["score_sum"] += group["highest"]
        if group["highest"] >= row["event"].simulator_qualification_threshold:
            row["meeting_threshold"] += 1
            if definitions.INTEREST_MAPPING and definitions.INTEREST_MAPPING.get(group["_interest"]) in ("yes","maybe"):
                row["qualified"] += 1
    for row in rows.values():
        row["average_score"] = ratio(row["score_sum"],row["scored_participants"])
        row["qualification_rate"] = ratio(row["qualified"],row["leads"],True)
    return list(rows.values())


def event_people(actor, event):
    population, regs = _event_sources(actor,Event.objects.filter(pk=event.pk))
    lead_ids = regs.order_by().values("_canonical_person")
    people = population.filter(pk__in=lead_ids).select_related("career_profile")
    if actor.has_perm("simulators.view_simulatorcapture"):
        source = canonical_records(activity_sources(event)["captures"].filter(total_score__gte=0,total_score__lte=100,replaced_by__isnull=True))
        source = source.filter(_canonical_person=OuterRef("pk")).order_by().values("_canonical_person")
        people = people.annotate(best_score=Subquery(source.annotate(n=Max("total_score")).values("n")[:1]),
            play_count=Subquery(source.annotate(n=Count("submission_id",distinct=True)).values("n")[:1]))
    return people.order_by("last_name","first_name","pk")


def event_finances(actor,event):
    require_tab(actor,"events")
    result = costs(event)  # Existing authoritative financial engine, including its reconciliation rules.
    adjustments = CompensationAdjustment.objects.filter(event=event,is_active=True).aggregate(amount=Sum("amount"))["amount"] or ZERO
    from apps.event_reports.models import ManualExpense
    result["expense_categories"] = list(ManualExpense.objects.filter(event=event,is_active=True,treatment="cost").values("category_name").annotate(amount=Sum("amount")).order_by("category_name"))
    result["expense_categories_available"] = True
    for component in result["components"]:
        component["label"]=component["label"].replace("Approved Build 9E staff cost","Approved staff pay and reimbursements")
    result["warnings"]=[warning.replace("Approved Build 9E staff cost","Staff pay and reimbursements") for warning in result["warnings"]]
    result["adjustments"] = adjustments
    result["recorded"] += adjustments
    if result["total"] is not None: result["total"] += adjustments
    result["components"].append({"label":"Recorded contractor incentives / adjustments","amount":adjustments,"state":"Audited financial source; separate from distributed participant incentives"})
    return result


def event_detail(actor,event):
    row = event_metrics(actor,Event.objects.filter(pk=event.pk))[0]
    cost = event_finances(actor,event)
    row.update(cost=cost,cost_per_lead=ratio(cost["total"],row["leads"]),cost_per_qualified=ratio(cost["total"],row["qualified"]))
    return row


def participant_projection(actor,filters):
    require_tab(actor,"participants")
    from apps.participants.discovery import discover
    from apps.participants.intelligence import build_intelligence
    criteria = {}
    for name,target in [("start","acquired_from"),("end","acquired_to"),("region","state"),("identifier","identifier"),("event","event")]:
        value = filters.get(name)
        if value: criteria[target] = str(value.pk) if hasattr(value,"pk") else str(value)
    for key in ("segments","travel","assessment","score_min","score_max"):
        if filters.get(key) not in (None,""):criteria[key]=str(filters[key])
    form,population,criteria = discover(actor,criteria,strict=True)
    if not form.is_valid(): raise PermissionDenied("Participant report filters require their source permissions.")
    population=population.filter(admin_preview_fixture__isnull=True)
    return population,build_intelligence(actor,population,criteria,current_group=True),criteria


def recruiting_cohort(actor,filters):
    require_tab(actor,"opportunities")
    from apps.recruiting.selection import opportunities_for
    qs = date_scope(opportunities_for(actor),"created_at",filters)
    if filters.get("customer"): qs = qs.filter(need__organization=filters["customer"])
    if filters.get("region"): qs = qs.filter(need__effective_revision__location_state__iexact=filters["region"].strip())
    if filters.get("campaign"): qs = qs.filter(need__campaign_id=getattr(filters["campaign"],"pk",filters["campaign"]))
    if filters.get("opportunity"): qs = qs.filter(pk=getattr(filters["opportunity"],"pk",filters["opportunity"]))
    return qs.order_by("-created_at","pk")


def candidates_for(actor,opportunities):
    from apps.recruiting.models import CandidateOpportunity
    # The opportunity selector is the established owner/collaborator boundary. A stage is not consent or employer release.
    return CandidateOpportunity.objects.filter(opportunity__in=opportunities,merged_into__isnull=True,participant__kind="person").select_related("participant","stage","opportunity__need__organization","opportunity__need__effective_revision","opportunity__need__campaign")


def confirmed_hires(actor,filters):
    require_tab(actor,"hires")
    from apps.core.models import AuditLog
    from django.contrib.contenttypes.models import ContentType
    from apps.recruiting.models import CandidateOpportunity
    # Filter hiring dates on the immutable explicit-confirmation audit, never on an arbitrary updated_at.
    opportunities = recruiting_cohort(actor,{k:v for k,v in filters.items() if k not in ("start","end")})
    logs = AuditLog.objects.annotate(reporting_object_id=Replace("object_id",Value("-"),Value(""))).filter(content_type=ContentType.objects.get_for_model(CandidateOpportunity),reporting_object_id=Replace(Cast(OuterRef("pk"),CharField()),Value("-"),Value("")),
        action="recruiting.candidate.stage_changed",new_data__btw_hiring_confirmation=True,new_data__outcome="hired").order_by("-timestamp")
    qs = candidates_for(actor,opportunities).filter(outcome="hired").annotate(confirmed_on=Subquery(logs.values("timestamp")[:1]))
    return date_scope(qs.filter(confirmed_on__isnull=False),"confirmed_on",filters).order_by("-confirmed_on","pk")


def recruiting_metrics(actor,filters):
    qs = recruiting_cohort(actor,filters)
    candidates = candidates_for(actor,qs)
    active = qs.filter(archived_at__isnull=True,need__status="active",need__effective_revision__state="approved")
    counts = {"opportunities":qs.count(),"active_opportunities":active.count(),
        "requested_positions":active.aggregate(n=Sum("need__effective_revision__worker_quantity"))["n"],
        "candidates":candidates.filter(active=True).count(),"interests":qs.aggregate(n=Count("interests",filter=Q(interests__active=True),distinct=True))["n"]}
    stages = list(candidates.values("stage__label").annotate(count=Count("pk")).order_by("stage__position"))
    months = list(qs.annotate(month=TruncMonth("created_at",tzinfo=ZoneInfo(definitions.REPORT_TIMEZONE))).values("month").annotate(count=Count("pk",distinct=True)).order_by("month"))
    return qs,candidates,counts,stages,months


def customer_projection(actor,filters):
    require_tab(actor,"customers")
    from apps.customers.access import organizations
    from apps.customers.models import WorkforceNeed,Campaign
    from apps.recruiting.models import WorkforceRequest
    qs = organizations(actor)
    if filters.get("campaign"):
        qs=qs.filter(workforce_needs__campaign_id=getattr(filters["campaign"],"pk",filters["campaign"])).distinct()
    if filters.get("opportunity"):
        scope=recruiting_cohort(actor,{k:v for k,v in filters.items() if k not in ("start","end")})
        qs=qs.filter(workforce_needs__recruiting_opportunity__in=scope).distinct()
    if filters.get("customer"): qs = qs.filter(pk=filters["customer"].pk)
    if filters.get("region"): qs = qs.filter(locations__is_active=True,locations__state__iexact=filters["region"].strip()).distinct()
    needs = date_scope(WorkforceNeed.objects.filter(organization__in=qs),"created_at",filters)
    requests = date_scope(WorkforceRequest.objects.filter(organization__in=qs),"created_at",filters)
    campaigns = date_scope(Campaign.objects.filter(organization__in=qs),"created_at",filters)
    if filters.get("campaign"):
        campaign_id=getattr(filters["campaign"],"pk",filters["campaign"]);needs=needs.filter(campaign_id=campaign_id);campaigns=campaigns.filter(pk=campaign_id)
        requests=requests.filter(positions__need__in=needs).distinct()
    if filters.get("opportunity"):
        scoped=recruiting_cohort(actor,filters);needs=needs.filter(recruiting_opportunity__in=scoped);requests=requests.filter(positions__need__in=needs).distinct()
    active_needs = needs.filter(status="active",effective_revision__state="approved")
    counts = {"customers":qs.count(),"active_customers":qs.filter(status="active").count(),"workforce_requests":requests.count(),"positions":needs.count(),"active_positions":active_needs.count(),"requested_workers":active_needs.aggregate(n=Sum("effective_revision__worker_quantity"))["n"],"campaigns":campaigns.count()}
    return qs.order_by("name","pk"),needs,counts


def chart(title,rows,detail=""):
    largest=max((abs(Decimal(str(r["count"] or 0))) for r in rows),default=0)
    return {"title":title,"detail":detail,"rows":[{**r,"negative":Decimal(str(r["count"] or 0))<0,"bar":float(abs(Decimal(str(r["count"] or 0)))*100/largest) if largest else 0} for r in rows]}
