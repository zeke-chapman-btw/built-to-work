import uuid
from datetime import date,timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.http import Http404,HttpResponseBadRequest
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from apps.events.models import Event
from apps.internal_ui.views import _staff_context
from apps.participants.identity import identity_context
from .access import staff,allowed_tabs,require_tab,can_finalize,can_approve_payroll
from .definitions import METRICS,CALCULATION_VERSION,REPORT_TIMEZONE
from .forms import ReportFilter,ReasonForm,PayrollStartForm,ManualFigureForm,AdjustmentForm
from .models import EventReport,EventReportVersion,PayrollPeriod,PayrollVersion,PayrollApproval,ManualBusinessFigure,CompensationAdjustment
from . import metrics,services,payroll,exports
from .presentation import TAB_LABELS,workspace_data,table

PAY_COLUMNS=[("contractor","Contractor","text"),("event","Event","text"),("occurred_on","Work/trip date","text"),("source_kind","Source","text"),("base","Base USD","money"),("per_diem","Per diem USD","money"),("mileage","Mileage USD","money"),("incentives","Incentives USD","money"),("adjustments","Adjustments USD","money"),("total","Total USD","money")]


def page(request,template,**context):
    query=request.GET.copy();query.pop("page",None);query.pop("format",None)
    context.setdefault("pagination_query",query.urlencode())
    context.setdefault("historical_readonly",bool(request.GET.get("version")))
    context.setdefault("current_year",timezone.localdate(timezone.now(),ZoneInfo(REPORT_TIMEZONE)).year)
    context.setdefault("calculation_version",CALCULATION_VERSION)
    context.setdefault("report_timezone",REPORT_TIMEZONE)
    context.setdefault("synthetic_review",getattr(settings,"REPORTING_SYNTHETIC_REVIEW",False))
    response=render(request,template,_staff_context(request,**context));response['Cache-Control']='private, no-store';return response


def tabs_for(actor,selected):
    allowed=allowed_tabs(actor)
    return [{"key":key,"label":label,"url":reverse("internal:reports")+"?tab="+key,"active":key==selected} for key,label in TAB_LABELS if key in allowed]


def export_links(request):
    q=request.GET.copy();q.pop("format",None);q.pop("page",None)
    result=[]
    for kind,label in [("pdf","PDF"),("xlsx","Excel"),("csv","CSV")]:
        query=q.copy();query['format']=kind;result.append({"label":label,"url":request.path+"?"+query.urlencode()})
    return result


def report_export(request,actor,title,columns,rows,filters,*,status="Live calculated report",version="",source="Authoritative recorded platform sources",private=False,extra_meta=None):
    kind=request.GET.get("format")
    if not kind:return None
    if kind not in exports.FORMATS:
        exports.authorize(actor,private=private)
        return HttpResponseBadRequest("Choose PDF, Excel or CSV.")
    return exports.export_response(actor=actor,format=kind,title=title,columns=columns,rows=rows,
        meta={**exports.metadata(title,filters,status=status,version=version,source=source),**(extra_meta or {})},private=private)


@login_required(login_url="accounts:login")
@require_http_methods(["GET"])
@never_cache
def workspace(request):
    actor=staff(request.user);tab=request.GET.get("tab","highlights")
    if tab not in dict(TAB_LABELS):raise Http404
    require_tab(actor,tab)
    form=ReportFilter(request.GET,actor=actor,tab=tab)
    context={"cards":[],"charts":[],"notes":[],"table":None,"columns":[],"report_rows":[],"page":None}
    if form.is_valid():
        with identity_context():context=workspace_data(actor,tab,form.cleaned_data,request,export=bool(request.GET.get("format")))
        if request.GET.get("format"):
            columns=context["columns"] or [("metric","Metric","text"),("value","Value","text"),("scope","Source / scope","text")]
            rows=context["report_rows"] or [{"metric":c["label"],"value":c["value"],"scope":c["detail"]} for c in context["cards"]]
            return report_export(request,actor,"BTW "+dict(TAB_LABELS)[tab],columns,rows,form.cleaned_data)
    elif request.GET.get("format"):
        return HttpResponseBadRequest("Correct the report filters before exporting.")
    from apps.internal_ui.reporting import report_destinations
    destinations=report_destinations(actor)
    return page(request,"reporting/workspace.html",page_title="Reports & Analytics",report_tabs=tabs_for(actor,tab),selected_tab=tab,filters=form,
        definitions=METRICS,exports=export_links(request) if actor.has_perm("reporting.export_reports") else [],
        legacy_reports=destinations,report_destinations=destinations,**context)


def history_id(value):
    try:return uuid.UUID(str(value))
    except (ValueError,TypeError,AttributeError):raise Http404("Choose a valid report version.")

def financial_source_rows(snapshot):
    rows=[]
    labels={'workday':'Workday','mileage':'Mileage','expense':'Event expense','distributed_incentive':'Distributed incentive','compensation_adjustment':'Contractor incentive / adjustment'}
    for source in snapshot['financial_sources']:
        row=dict(source);kind=row.get('source_type','');info=[row.get('source_state','Recorded')]
        row['source_label']=labels.get(kind,kind.replace('_',' ').title())
        row['recorded_on']=row.get('work_date') or row.get('claim_date') or row.get('expense_date') or row.get('occurred_on')
        if kind=='workday':info += ['Base '+exports.money_display(row.get('pay_amount')),'Per diem '+exports.money_display(row.get('per_diem_amount'))]
        elif kind=='mileage':info += [exports.display(row.get('round_trip_miles'))+' round-trip miles',exports.display(row.get('rate_amount'))+' USD/mile (historical snapshot)']
        elif kind=='expense':info += [row.get('category_name') or 'Category not recorded',row.get('reference')]
        elif kind=='distributed_incentive':info += [exports.money_display(row.get('unit_value'))+' per item','Quantity '+str(row.get('quantity'))]
        elif kind=='compensation_adjustment':info += [row.get('kind'),row.get('reference')]
        row['source_detail']=' · '.join(str(value) for value in info if value)
        if kind=='workday':row['url']=reverse('workdays:record',args=[row['id']])
        else:row['url']=reverse('event_reports:detail',args=[snapshot['event_id']])
        rows.append(row)
    return rows


def event_export_rows(snapshot):
    data=snapshot['data'];cost=data['cost']
    rows=[{"metric":"Unique event leads","value":data['leads'],"unit":"canonical people per event","provenance":"Calculated"},
        {"metric":"Interested in work","value":data['interested'],"unit":"people","provenance":"Recorded current interest / approved mapping"},
        {"metric":"Completed simulator plays","value":data['plays'],"unit":"accepted captures","provenance":"Calculated"},
        {"metric":"Event qualification threshold","value":snapshot['threshold'],"unit":"TOTAL SCORE percent","provenance":"Event setting frozen in this report"},
        {"metric":"Participants meeting threshold","value":data['meeting_threshold'],"unit":"people","provenance":"Highest valid score per person"},
        {"metric":"Qualified leads","value":data['qualified'],"unit":"people","provenance":"Threshold AND approved interest intersection"},
        {"metric":"Average best simulator score","value":data['average_score'],"unit":"percent","provenance":"Participant-weighted average of highest scores"},
        {"metric":"Recorded event cost USD","value":cost['recorded'],"unit":"USD","provenance":"Recorded source amount; completeness separately labelled"},
        {"metric":"Complete event cost USD","value":cost['total'],"unit":"USD","provenance":"Existing reconciliation rules"},
        {"metric":"Cost per lead USD","value":data['cost_per_lead'],"unit":"USD","provenance":"Same-event complete cost / unique leads"},
        {"metric":"Cost per qualified lead USD","value":data['cost_per_qualified'],"unit":"USD","provenance":"Same-event complete cost / qualified leads"}]
    for key,label in [("pay","Staff base pay USD"),("per_diem","Per diem USD"),("mileage","Mileage reimbursement USD")]:
        rows.append({"metric":label,"value":cost["staff"][key],"unit":"USD","provenance":"Existing approved workday/trip snapshot component; included in total cost"})
    for key,label in [("manual","Operating expenses USD"),("incentives","Distributed participant incentives USD"),("adjustments","Contractor incentives / adjustments USD")]:
        rows.append({"metric":label,"value":cost.get(key),"unit":"USD","provenance":"Recorded component; included in event cost, not an additional charge"})
    for category in cost.get("expense_categories",[]):
        rows.append({"metric":"Operating category subtotal: "+(category.get("category_name") or "Not recorded"),"value":category["amount"],"unit":"USD","provenance":"Active operating-expense category subtotal; already included in costs"})
    for row in snapshot['manual_business_figures']:rows.append({"metric":row['label'],"value":row['value'],"unit":row['unit'],"provenance":"Manual — "+row['source_note']})
    for source in snapshot['financial_sources']:
        value=source.get('amount')
        rows.append({"metric":"Source: "+source['source_type']+" "+str(source['id']),"value":value,"unit":"USD","provenance":" · ".join(str(v) for v in [source.get("contractor"),source.get("source_state"),source.get("category_name"),source.get("work_date") or source.get("claim_date") or source.get("expense_date") or source.get("occurred_on"),"Source "+str(source["id"]),source.get("reference")] if v)})
    return rows


@login_required(login_url="accounts:login")
@require_http_methods(["GET","POST"])
@never_cache
def event_report(request,event_id):
    if request.method=="POST" and request.GET.get("version"):return HttpResponseBadRequest("Historical versions are read-only.")
    from apps.event_reports.services import can_manage
    actor=require_tab(request.user,"events");event=get_object_or_404(Event,pk=event_id)
    state=EventReport.objects.select_related('current_version').filter(event=event).first()
    form=ReasonForm(request.POST if request.method=="POST" else None,initial={"revision":state.revision if state else 0})
    if request.method=="POST" and form.is_valid():
        action=request.POST.get('action')
        handlers={"prepare":services.prepare_event,"finalize":services.finalize_event,"reopen":services.reopen_event}
        if action not in handlers:return HttpResponseBadRequest("Unsupported report action.")
        try:handlers[action](actor=actor,event_id=event.pk,expected_revision=form.cleaned_data['revision'],why=form.cleaned_data['reason'])
        except ValidationError as exc:form.add_error(None,exc)
        else:messages.success(request,"Event report history updated.");return redirect('reporting:event',event_id=event.pk)
    selected=None
    if request.GET.get('version'):
        selected=get_object_or_404(EventReportVersion,event=event,pk=history_id(request.GET['version']))
    elif state and state.state in ('finalized','refinalized'):
        selected=state.current_version
    with identity_context():snapshot=services.verify_event_version(selected) if selected else services.event_snapshot(actor,event)
    status=("Finalized version "+str(selected.number)) if selected else (state.get_state_display() if state else "Draft live calculations")
    if request.GET.get('format'):
        return report_export(request,actor,"Event Report — "+event.name,[("metric","Metric / source","text"),("value","Recorded value","metric"),("unit","Unit","text"),("provenance","Provenance / source detail","text")],event_export_rows(snapshot),
            {"start":snapshot['event_start'],"end":snapshot['event_end'],"event":event.name},status=status,version=selected.number if selected else '',source=snapshot['definitions']['version'])
    data=snapshot['data']
    source_page=Paginator(financial_source_rows(snapshot),25).get_page(request.GET.get("page"))
    return page(request,"reporting/event_report.html",page_title="Event Report",event=event,snapshot=snapshot,metrics=data,cost=data['cost'],report_status=status,selected_version=selected,
        reason_form=form,report_state=state,can_finalize=can_finalize(actor),can_manage=can_manage(actor),versions=event.analytics_report_versions.select_related('finalized_by').all(),
        figures=ManualBusinessFigure.objects.filter(event=event,is_active=True),exports=export_links(request) if actor.has_perm('reporting.export_reports') else [],
        live_detail_url=reverse('reporting:event-people',args=[event.pk]),financial_rows=table([('source_label','Source','text'),('contractor','Contractor','text'),('recorded_on','Recorded date','text'),('amount','Source amount USD','money'),('source_detail','Components / treatment','text'),('id','Record reference','text')],source_page.object_list),page=source_page)


@login_required(login_url="accounts:login")
@require_http_methods(["GET"])
@never_cache
def event_participants(request,event_id):
    actor=require_tab(request.user,"events");event=get_object_or_404(Event,pk=event_id)
    with identity_context():
        qs=metrics.event_people(actor,event);page_obj=Paginator(qs,25).get_page(request.GET.get('page'))
        rows=[]
        from .definitions import INTEREST_MAPPING
        for person in page_obj:
            career=getattr(person,'career_profile',None);raw=career.employment_interest if career else ''
            interest=INTEREST_MAPPING.get(raw,'unknown') if INTEREST_MAPPING else None
            best=getattr(person,'best_score',None)
            rows.append({'participant':str(person),'preference':career.get_employment_interest_display() if career and raw else 'Not recorded','interest':interest,'score':best,'plays':getattr(person,'play_count',None) or 0,'threshold':event.simulator_qualification_threshold,
                'qualified':None if interest is None or best is None else interest in ('yes','maybe') and best>=event.simulator_qualification_threshold,
                'url':reverse('participants:staff-profile',args=[person.pk])})
    return page(request,'reporting/detail_table.html',page_title="Event participant evidence",event=event,description="Current authorized evidence. This live drill-down is separate from historical finalized metric snapshots; open a participant for assessment history.",
        report_table=table([('participant','Participant','text'),('preference','Recorded employment preference','text'),('interest','Approved Yes/Maybe/No mapping','text'),('score','Highest valid score %','number'),('plays','Completed plays','number'),('threshold','Event threshold %','number'),('qualified','Qualified under approved rule','text')],rows),page=page_obj,back_url=reverse('reporting:event',args=[event.pk]))


@login_required(login_url="accounts:login")
@require_http_methods(["GET","POST"])
@never_cache
def manual_figure(request,event_id,figure_id=None):
    actor=require_tab(request.user,'events')
    from apps.event_reports.services import require_manage
    require_manage(actor);event=get_object_or_404(Event,pk=event_id)
    obj=get_object_or_404(ManualBusinessFigure,pk=figure_id,event=event) if figure_id else None
    initial={k:getattr(obj,k) for k in ('label','value','unit','source_note','revision')} if obj else {'revision':0}
    form=ManualFigureForm(request.POST if request.method=='POST' else None,initial=initial)
    if request.method=='POST' and form.is_valid():
        try:services.save_manual_figure(actor=actor,event_id=event.pk,data=form.cleaned_data,figure_id=figure_id)
        except ValidationError as exc:form.add_error(None,exc)
        else:return redirect('reporting:event',event_id=event.pk)
    return page(request,'reporting/form.html',page_title="Manual business figure",form=form,event=event,description="Attributable business figures only. Calculated leads, plays, qualifications and financial totals cannot be replaced.",back_url=reverse('reporting:event',args=[event.pk]))


@login_required(login_url="accounts:login")
@require_http_methods(["GET","POST"])
@never_cache
def payroll_start(request):
    actor=require_tab(request.user,'payroll');today=timezone.localdate(timezone.now(),ZoneInfo(REPORT_TIMEZONE))
    form=PayrollStartForm(request.POST if request.method=='POST' else None,initial={'starts_on':today-timedelta(days=today.weekday())})
    if request.method=='POST' and form.is_valid():
        try:version=payroll.compile_payroll(actor=actor,starts_on=form.cleaned_data['starts_on'],why=form.cleaned_data['reason'])
        except ValidationError as exc:form.add_error(None,exc)
        else:return redirect('reporting:payroll',starts_on=version.period_id.isoformat())
    return page(request,'reporting/form.html',page_title="Compile weekly payroll",form=form,description="Monday–Sunday · 1099 contractor reporting · Actual recorded workdays and authorized financial sources only.",back_url=reverse('internal:reports')+'?tab=payroll')


def period_date(value):
    try:parsed=date.fromisoformat(value)
    except (ValueError,TypeError):raise Http404
    if parsed.weekday()!=0:raise Http404
    return parsed


@login_required(login_url="accounts:login")
@require_http_methods(["GET","POST"])
@never_cache
def payroll_detail(request,starts_on):
    if request.method=='POST' and request.GET.get('version'):return HttpResponseBadRequest('Historical versions are read-only.')
    actor=require_tab(request.user,'payroll');start=period_date(starts_on);period=get_object_or_404(PayrollPeriod,pk=start)
    form=ReasonForm(request.POST if request.method=='POST' else None,initial={'revision':period.revision})
    if request.method=='POST' and form.is_valid():
        args=dict(actor=actor,starts_on=start,expected_revision=form.cleaned_data['revision'],why=form.cleaned_data['reason'])
        try:
            action=request.POST.get('action')
            if action=='compile':payroll.compile_payroll(**args)
            elif action=='approve':payroll.approve_payroll(**args)
            elif action=='reopen':payroll.reopen_payroll(**args)
            elif action=='submit':payroll.submit_payroll(**args,reference=request.POST.get('reference'),operation_id=uuid.UUID(request.POST.get('operation_id','')))
            else:return HttpResponseBadRequest("Unsupported payroll action.")
        except (ValidationError,ValueError,TypeError) as exc:form.add_error(None,exc if isinstance(exc,ValidationError) else "Use the current submission form and a valid operation reference.")
        else:messages.success(request,"Weekly payroll history updated; no payments or email were sent.");return redirect('reporting:payroll',starts_on=start.isoformat())
    selected=get_object_or_404(PayrollVersion,period=period,pk=history_id(request.GET['version'])) if request.GET.get('version') else period.current_version
    if not selected:raise Http404
    snapshot=payroll.verify_version(selected);approval=PayrollApproval.objects.select_related('approved_by').filter(version=selected).first()
    status=("Historical approved version" if request.GET.get("version") else "Approved") if approval else "Compiled / unapproved"
    if request.GET.get('format'):
        response=report_export(request,actor,"Weekly Contractor Payroll",PAY_COLUMNS,[*snapshot['rows'],{'contractor':'TOTAL REPORTED','event':'','occurred_on':'','source_kind':'',**snapshot['totals']}],{'start':snapshot['period_start'],'end':snapshot['period_end']},status=status,version=selected.number,
            source=snapshot['calculation_version']+'; approver: '+(approval.approved_by.get_username() if approval else 'not approved'),extra_meta={'Compensation USD':exports.money_display(snapshot['totals']['compensation']),'Reimbursements USD':exports.money_display(snapshot['totals']['reimbursements'])})
        return response
    current,fingerprint=payroll.compile_data(start)
    detail_page=Paginator(snapshot["rows"],25).get_page(request.GET.get("page"))
    return page(request,'reporting/payroll.html',page_title="Weekly Payroll",period=period,snapshot=snapshot,selected_version=selected,approval=approval,
        reason_form=form,selected_report_status=status,can_approve=can_approve_payroll(actor),source_changed=fingerprint!=selected.source_fingerprint,
        versions=period.versions.select_related('compiled_by').all(),submissions=selected.submissions.select_related('submitted_by').order_by('-submitted_at'),ap_status="Historical version — its submission evidence is listed below" if request.GET.get("version") else payroll.submission_status(period),operation_id=uuid.uuid4(),
        report_table=table(PAY_COLUMNS,detail_page.object_list),page=detail_page,exports=export_links(request) if actor.has_perm('reporting.export_reports') else [],
        adjustments=CompensationAdjustment.objects.filter(occurred_on__range=(start,period.ends_on)).select_related('staff','event'))


@login_required(login_url="accounts:login")
@require_http_methods(["GET","POST"])
@never_cache
def adjustment(request):
    actor=require_tab(request.user,'payroll')
    from apps.event_reports.services import require_manage
    require_manage(actor)
    form=AdjustmentForm(request.POST if request.method=='POST' else None,initial={'operation_id':uuid.uuid4()})
    if request.method=='POST' and form.is_valid():
        try:row=services.add_adjustment(actor=actor,data=form.cleaned_data)
        except ValidationError as exc:form.add_error(None,exc)
        else:messages.success(request,"Authorized adjustment recorded once. Affected approved weeks require correction review.");return redirect(reverse('internal:reports')+'?tab=payroll')
    return page(request,'reporting/form.html',page_title="Contractor incentive / adjustment",form=form,description="Enter an attributable financial source, not a duplicate expense. A linked workday must match the contractor, event and date. Signed adjustments preserve their reason and audit history.",back_url=reverse('internal:reports')+'?tab=payroll')


@login_required(login_url="accounts:login")
@require_http_methods(["GET","POST"])
@never_cache
def adjustment_void(request,adjustment_id):
    actor=require_tab(request.user,'payroll');obj=get_object_or_404(CompensationAdjustment,pk=adjustment_id)
    from apps.event_reports.services import require_manage
    require_manage(actor);form=ReasonForm(request.POST if request.method=='POST' else None,initial={'revision':obj.revision})
    if request.method=='POST' and form.is_valid():
        try:services.void_adjustment(actor=actor,adjustment_id=obj.pk,expected_revision=form.cleaned_data['revision'],why=form.cleaned_data['reason'])
        except ValidationError as exc:form.add_error(None,exc)
        else:return redirect(reverse('internal:reports')+'?tab=payroll')
    return page(request,'reporting/form.html',page_title="Void compensation adjustment",form=form,description="The source and prior approved payroll versions remain retained. Recompile and reapprove any corrected period.",back_url=reverse('internal:reports')+'?tab=payroll')


@login_required(login_url="accounts:login")
@require_http_methods(["GET"])
@never_cache
def my_pay(request):
    actor=staff(request.user)
    try:year=int(request.GET.get('year',timezone.localdate(timezone.now(),ZoneInfo(REPORT_TIMEZONE)).year))
    except (ValueError,TypeError):return HttpResponseBadRequest("Choose a valid reporting year.")
    if not 1900<=year<=2100:return HttpResponseBadRequest("Choose a supported reporting year.")
    statements=list(payroll.own_statements(actor));page_obj=Paginator(statements,20).get_page(request.GET.get('page'))
    return page(request,'reporting/my_pay.html',page_title="My Pay",statements=page_obj,year=year,earnings=payroll.earnings_year(actor,year,through=timezone.localdate(timezone.now(),ZoneInfo(REPORT_TIMEZONE)) if year==timezone.localdate(timezone.now(),ZoneInfo(REPORT_TIMEZONE)).year else None),description="Private Contractor Earnings Statements. Approval is not proof of payment. Compensation and reimbursements remain separate.")


@login_required(login_url="accounts:login")
@require_http_methods(["GET"])
@never_cache
def my_statement(request,version_id):
    actor=staff(request.user);snapshot=payroll.own_statement(actor,version_id)
    if request.GET.get('format'):
        return report_export(request,actor,"Contractor Earnings Statement",PAY_COLUMNS,[*snapshot['rows'],{'contractor':'TOTAL REPORTED','event':'','occurred_on':'','source_kind':'',**snapshot['totals']}],{'start':snapshot['period_start'],'end':snapshot['period_end']},status=snapshot['status'],version=snapshot['version'],source=snapshot['calculation_version']+'; approved by '+snapshot['approver']+' at '+snapshot['approved_at'],private=True)
    return page(request,'reporting/statement.html',page_title="Contractor Earnings Statement",snapshot=snapshot,report_table=table(PAY_COLUMNS,snapshot['rows']),exports=export_links(request),back_url=reverse('reporting:my-pay'))
