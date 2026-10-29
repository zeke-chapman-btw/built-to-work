from datetime import date
from zoneinfo import ZoneInfo
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.events.services import audit_action
from apps.staffing.models import StaffProfile, AssignmentDay
from apps.staffing.calendar import event_days
from apps.staffing.views import staff_view
from . import services as svc, reports
from .models import StaffRole, CompensationRate, Workday, MileageTrip, EventMileage, ORIGIN
from .forms import CheckinForm, WorkdayForm, RoleForm, RateForm, DistanceForm, TripForm, RevisionForm

def page(request,template,**context):
    from apps.accounts.roles import has_role
    context["can_self_checkin"]=has_role(request.user,"event_staff")
    context.update(can_manage=svc.manager(request.user),can_configure=svc.administrator(request.user))
    query=request.GET.copy();query.pop('page',None);query.pop('export',None);context['pagination_query']=query.urlencode()
    query=request.GET.copy();query.pop('trip_page',None);query.pop('export',None);context['trip_pagination_query']=query.urlencode()
    response=render(request,'workdays/'+template+'.html',context)
    response['Cache-Control']='private, no-store'
    return response

def shown(value):
    return value if value is not None else 'Needs review'

def readonly(request):
    if request.method!='GET':raise PermissionDenied

def form_page(request,title,form,back,event=None,summary=""):
    return page(request,'form',page_title=title,form=form,back=back,event=event,summary=summary)

def history(obj):
    return AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(obj),object_id=str(obj.pk)).select_related('actor')[:100]

@staff_view
def index(request):
    svc.require_manager(request.user);readonly(request)
    q=request.GET.get('q','').strip()[:200]
    events=Event.objects.all()
    if q:events=events.filter(name__icontains=q)
    return page(request,'index',page_title='Staff Workdays',events=Paginator(events.order_by('-start_at','pk'),25).get_page(request.GET.get('page')),q=q)

@staff_view
def mine(request):
    readonly(request);staff=svc.person(request.user)
    if not staff:raise PermissionDenied
    days=Workday.objects.filter(staff=staff).select_related('event','role','updated_by')
    return page(request,'mine',page_title='My Workday History',days=Paginator(days,40).get_page(request.GET.get('page')))

@staff_view
def checkin(request,event_id):
    from apps.accounts.roles import has_role
    if not has_role(request.user,'event_staff'):raise PermissionDenied
    event=get_object_or_404(Event,pk=event_id);staff=svc.person(request.user)
    if not staff:raise PermissionDenied
    assigned=AssignmentDay.objects.filter(assignment__event=event,assignment__staff=staff,assignment__status='confirmed').order_by('date')
    own=Workday.objects.filter(event=event,staff=staff).select_related('role')
    if not assigned.exists() and not own.exists():raise PermissionDenied
    if event.status=='draft':raise PermissionDenied
    today=timezone.localdate(timezone.now(),ZoneInfo(event.timezone_name))
    already=own.filter(work_date=today,is_active=True).first()
    eligible=assigned.filter(date=today).exists() and today in event_days(event) and event.status not in ('draft','archived','cancelled')
    form=CheckinForm(initial={'work_date':today})
    if request.method=='POST':
        saved,form=svc.checkin(actor=request.user,event_id=event.pk,data=request.POST)
        if saved:
            messages.success(request,'Daily check-in recorded. No clock-out is needed. Driving declarations remain pending manager review.')
            return redirect('workdays:checkin',event_id=event.pk)
    return page(request,'checkin',page_title='Daily Check-In',event=event,today=today,already=already,eligible=eligible,form=form,assigned_days=assigned,days=own[:100])

@staff_view
def event_roster(request,event_id):
    svc.require_manager(request.user);readonly(request)
    event=get_object_or_404(Event,pk=event_id);calendar=event_days(event)
    try:selected=date.fromisoformat(request.GET.get('date',''))
    except ValueError:selected=timezone.localdate(timezone.now(),ZoneInfo(event.timezone_name))
    if selected not in calendar:selected=calendar[0]
    active=Workday.objects.filter(event=event,work_date=selected,is_active=True)
    recorded=set(active.values_list('staff_id',flat=True))
    assignments=AssignmentDay.objects.filter(assignment__event=event,assignment__status='confirmed',date=selected).select_related('assignment__staff')
    roster=[{'staff':a.assignment.staff,'recorded':a.assignment.staff_id in recorded} for a in assignments]
    days=Workday.objects.filter(event=event,work_date=selected).select_related('staff','role')
    trips=MileageTrip.objects.filter(event=event).select_related('staff','rate','approved_by')
    return page(request,'event',page_title='Workdays & mileage',event=event,calendar=calendar,selected=selected,roster=roster,days=days,trips=trips,distance=EventMileage.objects.filter(event=event).first(),origin=ORIGIN,totals=reports.totals(Workday.objects.filter(event=event),trips))

@staff_view
def workday_edit(request,event_id=None,record_id=None):
    svc.require_manager(request.user)
    obj=get_object_or_404(Workday,pk=record_id) if record_id else None
    event=get_object_or_404(Event,pk=obj.event_id if obj else event_id)
    with timezone.override(ZoneInfo(event.timezone_name)):
        form=WorkdayForm(record=obj,initial={'work_date':event.local_start_at.date(),'per_diem':'review'})
        if request.method=='POST':
            saved,form=svc.save_workday(actor=request.user,event_id=event.pk,record_id=record_id,data=request.POST)
            if saved:
                messages.success(request,'Workday saved. Staff assignments were not changed.')
                return redirect('workdays:record',record_id=saved.pk)
        return form_page(request,'Correct workday' if obj else 'Add manager workday',form,('workdays:event',event.pk),event)

@staff_view
def record(request,record_id):
    readonly(request);obj=get_object_or_404(Workday.objects.select_related('event','staff','role','created_by','updated_by','approved_by'),pk=record_id)
    manage=svc.manager(request.user)
    if not manage and obj.staff_id!=getattr(svc.person(request.user),'pk',None):raise PermissionDenied
    return page(request,'record',page_title='Workday record',record=obj,event=obj.event,history=history(obj) if manage else [])

@staff_view
def record_action(request,record_id,action):
    svc.require_manager(request.user);obj=get_object_or_404(Workday,pk=record_id)
    if action not in ('approve','reopen','void'):raise Http404
    form=RevisionForm(initial={'revision':obj.updated_at.isoformat()})
    if request.method=='POST':
        saved,form=svc.workday_action(actor=request.user,record_id=obj.pk,data=request.POST,action=action)
        if saved:
            messages.success(request,'Workday '+action+' recorded; history retained.')
            return redirect('workdays:record',record_id=obj.pk)
    return form_page(request,action.title()+' workday',form,('workdays:record',obj.pk),obj.event,summary=f'{obj.staff} · {obj.work_date} · {obj.role_name}. Daily pay snapshot: {shown(obj.pay_amount)} USD. Per diem: {obj.per_diem} · {shown(obj.per_diem_amount)} USD. Approval retains these reviewed amounts; correct the record first to refresh configuration.')

@staff_view
def distance_edit(request,event_id):
    svc.require_manager(request.user);event=get_object_or_404(Event,pk=event_id)
    obj=EventMileage.objects.filter(event=event).first()
    form=DistanceForm(initial={'revision':obj.updated_at.isoformat() if obj else 'new','one_way_miles':obj.one_way_miles if obj else None})
    if request.method=='POST':
        saved,form=svc.save_distance(actor=request.user,event_id=event.pk,data=request.POST)
        if saved:
            messages.success(request,'Verified event driving mileage saved. Approved trip snapshots were preserved.')
            return redirect('workdays:event',event_id=event.pk)
    return form_page(request,'Verify event driving distance',form,('workdays:event',event.pk),event,summary=f'Origin: {ORIGIN}. Event physical destination: {svc.destination(event)}. Enter verified one-way driving miles, not straight-line distance.')

@staff_view
def trip_edit(request,event_id=None,trip_id=None):
    svc.require_manager(request.user);obj=get_object_or_404(MileageTrip,pk=trip_id) if trip_id else None
    event=get_object_or_404(Event,pk=obj.event_id if obj else event_id)
    form=TripForm(record=obj)
    if request.method=='POST':
        saved,form=svc.save_trip(actor=request.user,event_id=event.pk,data=request.POST,trip_id=trip_id)
        if saved:
            messages.success(request,'Individual mileage trip saved for manager review; no payment was issued.')
            return redirect('workdays:event',event_id=event.pk)
    return form_page(request,'Edit mileage trip' if obj else 'Add individual round trip',form,('workdays:event',event.pk),event)

@staff_view
def trip_action(request,trip_id,action):
    svc.require_manager(request.user);obj=get_object_or_404(MileageTrip.objects.select_related('event','staff'),pk=trip_id)
    if action not in ('verify','approve','reopen','void','history'):raise Http404
    if action=='history':
        readonly(request)
        return page(request,'trip',page_title='Mileage trip history',trip=obj,event=obj.event,history=history(obj))
    form=RevisionForm(initial={'revision':obj.updated_at.isoformat()})
    if request.method=='POST':
        saved,form=svc.trip_action(actor=request.user,trip_id=obj.pk,data=request.POST,action=action)
        if saved:
            messages.success(request,'Mileage '+action+' recorded. Approval records a reimbursement amount; it does not issue payment.')
            return redirect('workdays:event',event_id=obj.event_id)
    return form_page(request,action.title()+' mileage claim',form,('workdays:event',obj.event_id),obj.event,summary=f'{obj.staff} · Trip {obj.number} · {obj.get_status_display()}. Verified round-trip miles: {shown(obj.round_trip_miles)}; rate: {shown(obj.rate_amount)} USD/mile; reimbursement: {shown(obj.amount)} USD. Approval retains the verified snapshot. Verify and review the amount first.')

@staff_view
def settings(request):
    svc.require_admin(request.user);readonly(request)
    today=timezone.localdate()
    roles=list(StaffRole.objects.all())
    for role in roles:role.current_rate=svc.rate_for('role',today,role)
    rates=CompensationRate.objects.select_related('role','created_by')
    return page(request,'settings',page_title='Staff Compensation Settings',roles=roles,rates=Paginator(rates,40).get_page(request.GET.get('page')),per_diem=svc.rate_for('per_diem',today),mileage=svc.rate_for('mileage',today),origin=ORIGIN)

@staff_view
def role_edit(request,role_id=None):
    svc.require_admin(request.user);obj=get_object_or_404(StaffRole,pk=role_id) if role_id else None
    initial={'revision':obj.updated_at.isoformat(),'name':obj.name,'is_active':obj.is_active,'per_diem_eligible':{True:'yes',False:'no',None:'unknown'}[obj.per_diem_eligible]} if obj else {'per_diem_eligible':'unknown'}
    form=RoleForm(initial=initial)
    if request.method=='POST':
        saved,form=svc.save_role(actor=request.user,data=request.POST,role_id=role_id)
        if saved:
            messages.success(request,'Role saved. Prior workday role and compensation snapshots were retained.')
            return redirect('workdays:settings')
    return form_page(request,'Edit staff role' if obj else 'Add staff role',form,('workdays:settings',None))

@staff_view
def rate_edit(request,kind):
    svc.require_admin(request.user)
    if kind not in ('role','per_diem','mileage'):raise Http404
    role=get_object_or_404(StaffRole,pk=request.GET.get('role')) if kind=='role' else None
    latest=CompensationRate.objects.filter(kind=kind,role=role).order_by('-created_at','-pk').first()
    initial={'kind':kind,'role':role,'effective_from':timezone.localdate(),'revision':latest.updated_at.isoformat() if latest else 'new'}
    form=RateForm(initial=initial)
    if request.method=='POST':
        data=request.POST.copy();data['kind']=kind;data['role']=str(role.pk) if role else ''
        saved,form=svc.save_rate(actor=request.user,data=data)
        if saved:
            messages.success(request,'New rate version saved. Approved historical workdays and claims were not recalculated.')
            return redirect('workdays:settings')
    form.fields['kind'].disabled=True;form.fields['kind'].initial=kind
    form.fields['role'].disabled=True;form.fields['role'].initial=role
    return form_page(request,'New '+dict(CompensationRate.KINDS)[kind]+' rate version',form,('workdays:settings',None))

@staff_view
def report(request):
    svc.require_manager(request.user);readonly(request)
    form=reports.ReportFilter(request.GET)
    days,trips=reports.querysets(form.cleaned_data if form.is_valid() else {})
    if not form.is_valid():days=days.none();trips=trips.none()
    if request.GET.get('export')=='csv' and form.is_valid():
        audit_action(actor=request.user,action='workdays.report.exported',instance=request.user,new_data={'filters':request.GET.dict()})
        return reports.csv_export(days,trips)
    query=request.GET.copy();query.pop('page',None);query.pop('trip_page',None);query['export']='csv'
    return page(request,'report',page_title='Staff Workday & Reimbursement Reports',form=form,days=Paginator(days,40).get_page(request.GET.get('page')),trips=Paginator(trips,40).get_page(request.GET.get('trip_page')),totals=reports.totals(days,trips),csv_query=query.urlencode())
