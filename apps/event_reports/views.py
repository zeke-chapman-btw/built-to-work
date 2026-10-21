import csv
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count,Sum
from django.http import Http404,HttpResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.events.services import audit_action
from apps.operations import services as ops
from apps.workdays.models import Workday,MileageTrip
from apps.workdays.reports import safe
from . import services as svc,reporting
from .models import ExpenseCategory,ManualExpense,IncentiveLine,CostReview
from .forms import ExpenseForm,IncentiveForm,CategoryForm,ReviewForm,RevisionForm,EventFilter,ExpenseFilter

def portal(fn):
    @login_required(login_url='/admin/login/')
    @require_http_methods(['GET','POST'])
    @wraps(fn)
    def wrapped(request,*args,**kwargs):
        svc.require_staff(request.user)
        try:return fn(request,*args,**kwargs)
        except (ValidationError,IntegrityError) as exc:
            notice=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'The record changed or this submission already exists. Reload before trying again.'
            return page(request,'notice',page_title='Reporting action unavailable',notice=notice,status=409)
    return wrapped

def page(request,template,status=200,**context):
    context.update(can_report=svc.can_report(request.user),can_manage=svc.can_manage(request.user),can_configure=svc.can_configure(request.user))
    query=request.GET.copy();query.pop('page',None);query.pop('export',None);context['pagination_query']=query.urlencode()
    iq=request.GET.copy();iq.pop('incentive_page',None);iq.pop('export',None);context['incentive_query']=iq.urlencode()
    response=render(request,'event_reports/'+template+'.html',context,status=status);response['Cache-Control']='private, no-store';return response

def readonly(request):
    if request.method!='GET':raise PermissionDenied

def filtered_events(data):
    qs=Event.objects.all().order_by('-start_at','pk')
    if data.get('q'):qs=qs.filter(name__icontains=data['q'])
    if data.get('status'):qs=qs.filter(status=data['status'])
    if data.get('event_type'):qs=qs.filter(event_type=data['event_type'])
    # Event-date filtering follows each event's configured local date.
    return [e for e in qs if (not data.get('start') or e.local_start_at.date()>=data['start']) and (not data.get('end') or e.local_start_at.date()<=data['end'])]

@portal
def index(request):
    svc.require_report(request.user);readonly(request)
    form=EventFilter(request.GET);valid=form.is_valid()
    reports=[reporting.event_report(e) for e in filtered_events(form.cleaned_data)] if valid else []
    summary=reporting.aggregate(reports)
    if request.GET.get('export')=='csv' and valid:return performance_csv(request,reports,'cross-event-summary.csv')
    rows=Paginator(reports,20).get_page(request.GET.get('page'))
    return page(request,'index',page_title='Event Reports & Analytics',form=form,rows=rows,summary=summary,status=200 if valid else 400)

@portal
def detail(request,event_id):
    svc.require_report(request.user);readonly(request)
    event=get_object_or_404(Event,pk=event_id);report=reporting.event_report(event)
    form=ExpenseFilter(request.GET);valid=form.is_valid()
    expenses=ManualExpense.objects.filter(event=event).select_related('category','created_by','edited_by')
    if valid:
        d=form.cleaned_data
        if d.get('category'):expenses=expenses.filter(category=d['category'])
        if d.get('start'):expenses=expenses.filter(expense_date__gte=d['start'])
        if d.get('end'):expenses=expenses.filter(expense_date__lte=d['end'])
    else:expenses=expenses.none()
    lines=IncentiveLine.objects.filter(event=event).select_related('created_by','edited_by')
    export=request.GET.get('export')
    if export and valid:
        if export=='expenses':return expense_csv(request,event,expenses)
        if export=='incentives':return incentive_csv(request,event,lines)
        if export=='performance':return performance_csv(request,[report],'event-performance.csv')
        raise Http404
    notes=[];notes_access=ops.manager(request.user,'event_notes') or ops.assigned_event(request.user,event)
    if notes_access:
        notes=event.operational_notes.filter(archived_at__isnull=True).select_related('author')
        if not ops.manager(request.user,'event_notes'):notes=notes.filter(visibility='staff')
        notes=notes[:50]
    category_totals=ManualExpense.objects.filter(event=event,is_active=True,treatment='cost').values('category_name').annotate(amount=Sum('amount')).order_by('category_name')
    labels=[('Registered participants','registered'),('Unique participants','unique_participants'),('Kiosk test starts','kiosk_starts'),('Kiosk test completions','kiosk_completions'),('Simulator starts','simulator_starts'),('Simulator completions','simulator_completions'),('Fully completed experiences','completed_experiences'),('Average kiosk score (correct answers)','kiosk_average'),('Highest kiosk score (correct answers)','kiosk_high'),('Average simulator score (%)','simulator_average'),('Highest simulator score (%)','simulator_high'),('Registration-to-kiosk completion (%)','registration_to_kiosk'),('Kiosk-to-simulator completion (%)','kiosk_to_simulator'),('Overall completion rate (%)','overall_completion_rate'),('Registrations without kiosk completion','registration_dropoff'),('Kiosk completers without simulator completion','kiosk_simulator_dropoff')]
    metric_rows=[{'label':label,'value':report['metrics'][key]} for label,key in labels]
    review=report['cost']['review']
    review_form=ReviewForm(initial={'revision':svc.review_token(event,review),'manual_reviewed':review.manual_reviewed if review else False,'incentives_reviewed':review.incentives_reviewed if review else False,'staff_reviewed':review.staff_reviewed if review else False})
    report_section = request.GET.get("section")
    if report_section not in ("expenses", "report"):
        report_section = "all"  # Existing URLs, anchors, filters and exports retain their combined view.
    title = "Expenses" if report_section == "expenses" else "Event Report" if report_section == "report" else "Event Cost & Performance"
    return page(request,'detail',page_title=title,report_section=report_section,event=event,report=report,expense_filter=form,expenses=Paginator(expenses,30).get_page(request.GET.get('page')),incentives=Paginator(lines,30).get_page(request.GET.get('incentive_page')),review_form=review_form,notes=notes,notes_access=notes_access,definitions=reporting.METRIC_DEFINITIONS,category_totals=category_totals,metric_rows=metric_rows,status=200 if valid else 400)

@portal
def entry(request,event_id=None,record_id=None,kind='expense'):
    svc.require_manage(request.user)
    cls,form_cls=(ManualExpense,ExpenseForm) if kind=='expense' else (IncentiveLine,IncentiveForm)
    obj=get_object_or_404(cls,pk=record_id) if record_id else None
    event=get_object_or_404(Event,pk=obj.event_id if obj else event_id);svc.mutable(event)
    if obj and not obj.is_active:raise ValidationError('Voided records are read-only.')
    form=form_cls(instance=obj,initial={'expense_date':event.local_start_at.date()} if obj is None else None)
    if request.method=='POST':
        saved,form=svc.save_entry(actor=request.user,event_id=event.pk,data=request.POST,kind=kind,record_id=record_id)
        if saved:messages.success(request,'Expense saved.' if kind=='expense' else 'Distributed incentive line saved.');return redirect('event_reports:detail',event_id=event.pk)
    return page(request,'form',page_title=('Correct ' if obj else 'Add ')+('event expense' if kind=='expense' else 'distributed incentive'),form=form,event=event,back=reverse('event_reports:detail',args=[event.pk]),status=400 if form.is_bound else 200)

@portal
def action(request,record_id,kind,action):
    svc.require_report(request.user)
    cls=ManualExpense if kind=='expense' else IncentiveLine if kind=='incentive' else None
    if cls is None or action not in ('void','history'):raise Http404
    obj=get_object_or_404(cls,pk=record_id)
    if action=='history':
        readonly(request)
        logs=AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(obj),object_id=str(obj.pk)).select_related('actor').order_by('-timestamp')
        return page(request,'history',page_title='Financial correction history',record=obj,logs=Paginator(logs,40).get_page(request.GET.get('page')))
    svc.require_manage(request.user);svc.mutable(obj.event)
    form=RevisionForm(initial={'revision':obj.updated_at.isoformat()})
    if request.method=='POST':
        saved,form=svc.void_entry(actor=request.user,record_id=obj.pk,kind=kind,data=request.POST)
        if saved:messages.success(request,'Voided. History is retained and active cost totals updated.');return redirect('event_reports:detail',event_id=obj.event_id)
    return page(request,'form',page_title='Void '+kind,form=form,event=obj.event,summary=obj.description,back=reverse('event_reports:detail',args=[obj.event_id]),status=400 if form.is_bound else 200)

@portal
def review(request,event_id):
    svc.require_manage(request.user)
    event=get_object_or_404(Event,pk=event_id);svc.mutable(event)
    if request.method=='POST':
        saved,form=svc.save_review(actor=request.user,event_id=event.pk,data=request.POST)
        if saved:messages.success(request,'Cost reconciliation saved. Pending or missing staff records still prevent a complete total.');return redirect('event_reports:detail',event_id=event.pk)
    else:
        obj=CostReview.objects.filter(event=event).first()
        form=ReviewForm(initial={'revision':svc.review_token(event,obj),**{k:getattr(obj,k) if obj else False for k in ['manual_reviewed','incentives_reviewed','staff_reviewed']}})
    return page(request,'form',page_title='Reconcile event costs',form=form,event=event,summary='Confirm recorded sources, including intentional zero components. Source changes invalidate this review. Pending workdays/trips and missing assignment-day workdays remain incomplete.',back=reverse('event_reports:detail',args=[event.pk]),status=400 if form.is_bound else 200)

@portal
def categories(request):
    svc.require_configure(request.user);readonly(request)
    rows=ExpenseCategory.objects.annotate(usage=Count('expenses'))
    return page(request,'categories',page_title='Expense Categories',rows=rows)

@portal
def category_edit(request,category_id=None):
    svc.require_configure(request.user)
    obj=get_object_or_404(ExpenseCategory,pk=category_id) if category_id else None
    form=CategoryForm(instance=obj)
    if request.method=='POST':
        saved,form=svc.save_category(actor=request.user,data=request.POST,category_id=category_id)
        if saved:messages.success(request,'Expense category saved. Historical expense labels and accounting treatment were preserved.');return redirect('event_reports:categories')
    return page(request,'form',page_title='Edit expense category' if obj else 'Add expense category',form=form,back=reverse('event_reports:categories'),status=400 if form.is_bound else 200)

def csv_response(request,filename,headers,rows,event=None):
    svc.require_report(request.user)
    response=HttpResponse(content_type='text/csv; charset=utf-8');response['Content-Disposition']='attachment; filename="'+filename+'"';response['Cache-Control']='private, no-store'
    writer=csv.writer(response);writer.writerow(headers)
    for row in rows:writer.writerow(map(safe,row))
    audit_action(actor=request.user,action='event_reports.exported',instance=event or ExpenseCategory.objects.order_by('pk').first(),new_data={'report':filename,'filters':request.GET.dict()},reason='Authorized report CSV export')
    return response

def expense_csv(request,event,expenses):
    rows=[]
    for e in expenses:rows.append([event.name,e.category_name,e.description,'Manual',e.expense_date,e.amount,'Active' if e.is_active else 'Voided',e.get_treatment_display(),e.amount if e.is_active and e.treatment=='cost' else 0,str(e.pk)])
    for d in Workday.objects.filter(event=event).select_related('staff'):
        active=d.is_active and d.approval=='approved'
        for label,value in [('Staff daily pay',d.pay_amount),('Staff per diem',d.per_diem_amount)]:rows.append([event.name,label,'Workday '+str(d.pk),'Build 9E approved snapshot',d.work_date,value if active else None,'Approved' if active else 'Excluded','System-derived',value if active else None,str(d.pk)])
    for t in MileageTrip.objects.filter(event=event):rows.append([event.name,'Staff mileage','Trip '+str(t.pk),'Build 9E approved snapshot',t.claim_date,t.amount if t.status=='approved' else None,t.get_status_display(),'System-derived',t.amount if t.status=='approved' else None,str(t.pk)])
    for line in IncentiveLine.objects.filter(event=event):rows.append([event.name,'Distributed incentives',line.description,'Distributed line',line.expense_date,line.line_total,'Active' if line.is_active else 'Voided','Distributed value',line.line_total if line.is_active else 0,str(line.pk)])
    return csv_response(request,'event-expenses.csv',['Event','Category','Description','Source','Date','Amount USD','Status','Accounting treatment','Included cost USD','Record ID'],rows,event)

def incentive_csv(request,event,lines):
    return csv_response(request,'event-incentives.csv',['Event','Description','Unit value USD','Quantity distributed','Line total USD','Status','Included cost USD'],([event.name,r.description,r.unit_value,r.quantity,r.line_total,'Active' if r.is_active else 'Voided',r.line_total if r.is_active else 0] for r in lines),event)

def performance_csv(request,reports,filename):
    keys=['registered','unique_participants','kiosk_starts','kiosk_completions','simulator_starts','simulator_completions','completed_experiences','kiosk_average','kiosk_high','simulator_average','simulator_high','registration_to_kiosk','kiosk_to_simulator','overall_completion_rate']
    headers=['Event','Local event date','Status','Location','Recorded cost subtotal USD','Reconciled total event cost USD','Financial completeness','Approved staff workdays']+keys+['Cost per registration USD','Cost per completed experience USD','Report warnings']
    rows=([r['event'].name,r['event'].local_start_at.date(),r['event'].get_status_display(),r['event'].location_name,r['cost']['recorded'],r['cost']['total'],'Complete' if r['cost']['complete'] else 'Incomplete',r['cost']['approved_days']]+[r['metrics'][k] for k in keys]+[r['cost_per_registration'],r['cost_per_completion'],'; '.join(r['cost']['warnings']+r['metrics']['warnings'])] for r in reports)
    return csv_response(request,filename,headers,rows,reports[0]['event'] if len(reports)==1 else None)
