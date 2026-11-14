"""Authorization, locked writes, financial snapshots and audit history."""
import json
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from apps.events.models import Event
from apps.events.services import audit_action
from apps.staffing.models import StaffProfile, AssignmentDay
from apps.staffing.services import require_staff, revision
from apps.staffing.calendar import event_days
from .models import StaffRole, CompensationRate, EventMileage, MileageTrip, Workday
from apps.reporting.locks import serialized_compensation
from .distance import estimate as automatic_miles
from .forms import CheckinForm, WorkdayForm, RoleForm, RateForm, DistanceForm, TripForm, RevisionForm

def manager(user):
    return bool(user.is_authenticated and user.is_active and user.is_staff and user.has_perm('workdays.manage_workdays') and user.has_perm('events.view_event'))
def administrator(user):
    from apps.accounts.operational_access import protected_configuration
    return protected_configuration(user,'workdays.manage_compensation')
def require_manager(user):
    require_staff(user)
    if not manager(user):raise PermissionDenied

def require_admin(user):
    require_staff(user)
    if not administrator(user):raise PermissionDenied

def person(user):
    require_staff(user)
    return StaffProfile.objects.filter(user=user,is_active=True).first()

def snap(obj):
    return json.loads(json.dumps({f.attname:getattr(obj,f.attname) for f in obj._meta.fields},cls=DjangoJSONEncoder))
def audit(user,action,obj,before=None):
    audit_action(actor=user,action='workdays.'+action,instance=obj,old_data=before,new_data=snap(obj),reason=getattr(obj,'reason',''))
def rate_for(kind,day,role=None):
    return CompensationRate.objects.filter(kind=kind,role=role,effective_from__lte=day).first()
def cents(value):return value.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
def snapshot_pay(obj):
    obj.pay_rate=rate_for('role',obj.work_date,obj.role)
    obj.pay_amount=cents(obj.pay_rate.amount) if obj.pay_rate else None
    obj.per_diem_rate=rate_for('per_diem',obj.work_date) if obj.per_diem=='eligible' else None
    obj.per_diem_amount=(cents(obj.per_diem_rate.amount) if obj.per_diem_rate else None) if obj.per_diem!='ineligible' else Decimal('0.00')
def destination(event):
    text=', '.join(str(getattr(event,f,'') or '').strip() for f in ['location_name','address_line_1','address_line_2','city','state','postal_code','country'] if str(getattr(event,f,'') or '').strip())
    if getattr(event,'map_latitude',None) is not None and getattr(event,'map_longitude',None) is not None:
        text+=f' · pin {event.map_latitude}, {event.map_longitude}'
    return text
def current_distance(event,actor=None):
    """The event's mileage. Worked out automatically, unless a manager set it for this same location."""
    obj=EventMileage.objects.filter(event=event).first()
    where=destination(event)
    if obj and obj.destination==where:return obj
    est=automatic_miles(event)
    if not est:return None
    before=snap(obj) if obj else None
    if not obj:obj=EventMileage(event=event)
    obj.destination=where;obj.one_way_miles=est['one_way'];obj.straight_line_miles=est['straight_line'];obj.based_on=est['based_on'];obj.method='auto'
    obj.verified_by=None;obj.verified_at=timezone.now()
    obj.reason=f"Worked out automatically: {est['straight_line']} mi straight line from Watkinsville (by {est['based_on']}) + 20% = {est['one_way']} mi each way."
    obj.full_clean();obj.save()
    if actor is not None:audit(actor,'distance.automatic',obj,before)
    return obj
def snapshot_trip(obj,actor=None):
    distance=current_distance(obj.event,actor)
    if not distance:raise ValidationError('Add a ZIP code or map pin to the event location so mileage can be worked out.')
    obj.destination=distance.destination;obj.one_way_miles=distance.one_way_miles;obj.round_trip_miles=distance.one_way_miles*2
    obj.rate=rate_for('mileage',obj.travel_start or obj.claim_date)
    if not obj.rate:raise ValidationError('No approved per-mile rate exists for this trip date. Administrator review required.')
    obj.rate_amount=obj.rate.amount;obj.amount=cents(obj.round_trip_miles*obj.rate_amount)
def fill_miles(obj,actor=None):
    """Show the worked-out round trip on a pending trip. Pay is only fixed at approval."""
    distance=current_distance(obj.event,actor)
    if distance:
        obj.destination=distance.destination;obj.one_way_miles=distance.one_way_miles;obj.round_trip_miles=distance.one_way_miles*2
def driving_trip(actor,event,staff,source='staff_declaration'):
    trips=MileageTrip.objects.filter(event=event,staff=staff).order_by('number')
    obj=trips.exclude(status='void').first() or trips.first()
    if obj:return obj
    obj=MileageTrip(event=event,staff=staff,number=1,claim_date=event.local_start_at.date(),source=source,created_by=actor,updated_by=actor,reason='Drove to the event; round-trip miles worked out automatically.')
    fill_miles(obj,actor)
    obj.full_clean();obj.save();audit(actor,'trip.flagged',obj)
    return obj
@serialized_compensation
@transaction.atomic
def checkin(*,actor,event_id,data):
    from apps.accounts.roles import has_role
    if not has_role(actor,'event_staff'):raise PermissionDenied('Event Staff membership and an eligible assignment are required.')
    staff=person(actor)
    if not staff:raise PermissionDenied
    event=Event.objects.select_for_update().get(pk=event_id)
    staff=StaffProfile.objects.select_for_update().get(pk=staff.pk,is_active=True)
    form=CheckinForm(data)
    if not form.is_valid():return None,form
    d=form.cleaned_data;today=timezone.localdate(timezone.now(),ZoneInfo(event.timezone_name))
    if event.status in ('draft','archived','cancelled') or d['work_date']!=today or d['work_date'] not in event_days(event):raise ValidationError('Check in only on today’s authorized event workday in the event timezone. Ask a manager to correct a missed day.')
    if not AssignmentDay.objects.filter(assignment__event=event,assignment__staff=staff,assignment__status='confirmed',date=d['work_date']).exists():raise PermissionDenied
    if Workday.objects.filter(event=event,staff=staff,work_date=d['work_date'],is_active=True).exists():raise ValidationError('Already checked in for this event workday. Contact a manager for corrections.')
    role=StaffRole.objects.select_for_update().get(pk=d['role'].pk)
    if not role.is_active:raise ValidationError('This role is no longer active.')
    now=timezone.now()
    obj=Workday(event=event,staff=staff,work_date=d['work_date'],role=role,role_name=role.name,drove=d['drove'],source='staff',submitted_at=now,effective_checkin_at=now,created_by=actor,updated_by=actor)
    obj.per_diem={True:'eligible',False:'ineligible',None:'review'}[role.per_diem_eligible];snapshot_pay(obj)
    if obj.drove:obj.trip=driving_trip(actor,event,staff)
    obj.full_clean();obj.save();audit(actor,'checkin.created',obj)
    return obj,form
@serialized_compensation
@transaction.atomic
def save_workday(*,actor,event_id,data,record_id=None):
    require_manager(actor);event=Event.objects.select_for_update().get(pk=event_id)
    obj=Workday.objects.select_for_update().get(pk=record_id,event=event) if record_id else None
    if obj and (not obj.is_active or obj.approval=='approved'):raise ValidationError('Voided records are read-only. Reopen an approved workday before correction.')
    with timezone.override(ZoneInfo(event.timezone_name)):
        form=WorkdayForm(data,record=obj)
        if not form.is_valid():return None,form
    d=form.cleaned_data;revision(obj,d['revision']);staff=StaffProfile.objects.select_for_update().get(pk=d['staff'].pk)
    if d['work_date'] not in event_days(event) and (not obj or obj.work_date!=d['work_date']):raise ValidationError('Choose a date within the event staffing calendar.')
    if Workday.objects.filter(event=event,staff=staff,work_date=d['work_date'],is_active=True).exclude(pk=obj.pk if obj else None).exists():raise ValidationError('An active employee/event workday already exists.')
    role=StaffRole.objects.select_for_update().get(pk=d['role'].pk)
    if not role.is_active and (not obj or obj.role_id!=role.pk):raise ValidationError('Select an active role.')
    before=snap(obj) if obj else None
    if not obj:obj=Workday(event=event,staff=staff,source='manager',created_by=actor)
    if not obj.role_id or obj.role_id!=role.pk:obj.role_name=role.name
    obj.role=role
    for field in ['work_date','drove','per_diem','reason']:setattr(obj,field,d[field])
    # Check-in time is optional for managers: keep the recorded time if it is on the day, otherwise use the start of the day.
    when=d.get('effective_checkin_at') or obj.effective_checkin_at
    zone=ZoneInfo(event.timezone_name)
    if not when or timezone.localdate(when,zone)!=obj.work_date:
        from datetime import datetime, time
        start=getattr(event,'daily_start_time',None) or time(9,0)
        when=datetime.combine(obj.work_date,start,tzinfo=zone)
    obj.effective_checkin_at=when
    obj.updated_by=actor;snapshot_pay(obj);obj.trip=driving_trip(actor,event,staff,source='manager') if obj.drove else None
    obj.full_clean();obj.save();audit(actor,'workday.corrected' if before else 'workday.manual',obj,before)
    return obj,form
@serialized_compensation
@transaction.atomic
def workday_action(*,actor,record_id,data,action):
    require_manager(actor);initial=Workday.objects.get(pk=record_id);Event.objects.select_for_update().get(pk=initial.event_id)
    obj=Workday.objects.select_for_update().get(pk=record_id);form=RevisionForm(data)
    if not form.is_valid():return None,form
    revision(obj,form.cleaned_data['revision'])
    if not obj.is_active:raise ValidationError('Voided records are retained read-only.')
    before=snap(obj)
    if action=='approve':
        if obj.approval=='approved':raise ValidationError('Already approved.')
        if obj.pay_amount is None or obj.per_diem_amount is None:raise ValidationError('Resolve missing compensation or per diem eligibility before approval.')
        obj.approval='approved';obj.approved_by=actor;obj.approved_at=timezone.now()
    elif action=='reopen':
        if obj.approval!='approved':raise ValidationError('Only approved workdays can be reopened.')
        obj.approval='pending';obj.approved_by=None;obj.approved_at=None
    elif action=='void':obj.is_active=False
    else:raise ValidationError('Unknown workday action.')
    obj.reason=form.cleaned_data['reason'];obj.updated_by=actor;obj.full_clean();obj.save();audit(actor,'workday.'+action,obj,before)
    return obj,form

@transaction.atomic
def save_role(*,actor,data,role_id=None):
    require_admin(actor);obj=StaffRole.objects.select_for_update().get(pk=role_id) if role_id else None;form=RoleForm(data)
    if not form.is_valid():return None,form
    d=form.cleaned_data;revision(obj,d['revision']);before=snap(obj) if obj else None
    if not obj:obj=StaffRole()
    for field in ['name','is_active','per_diem_eligible']:setattr(obj,field,d[field])
    obj.full_clean();obj.save();audit_action(actor=actor,action='workdays.role.changed',instance=obj,old_data=before,new_data=snap(obj),reason=d['reason'])
    return obj,form
@transaction.atomic
def save_rate(*,actor,data):
    require_admin(actor);form=RateForm(data)
    if not form.is_valid():return None,form
    d=form.cleaned_data
    list(StaffRole.objects.select_for_update().order_by('pk'))
    latest=CompensationRate.objects.filter(kind=d['kind'],role=d['role']).order_by('-created_at','-pk').first()
    revision(latest,d['revision'])
    obj=CompensationRate(kind=d['kind'],role=d['role'],amount=d['amount'],effective_from=d['effective_from'],reason=d['reason'],created_by=actor)
    obj.full_clean();obj.save();audit(actor,'rate.version',obj,snap(latest) if latest else None)
    return obj,form
@transaction.atomic
def save_distance(*,actor,event_id,data):
    require_manager(actor);event=Event.objects.select_for_update().get(pk=event_id)
    obj=EventMileage.objects.select_for_update().filter(event=event).first();form=DistanceForm(data)
    if not form.is_valid():return None,form
    d=form.cleaned_data;revision(obj,d['revision']);before=snap(obj) if obj else None
    if not destination(event) or not (event.address_line_1 or event.city or event.postal_code or event.map_latitude is not None):raise ValidationError('Record the event location before setting mileage.')
    if d.get('one_way_miles') is None:
        if obj:obj.destination='';obj.save(update_fields=['destination'])
        auto=current_distance(event,actor)
        if not auto:raise ValidationError('Mileage could not be worked out. Add a ZIP code or map pin to the event, or type the miles.')
        return auto,form
    est=automatic_miles(event)
    if not obj:obj=EventMileage(event=event)
    obj.destination=destination(event);obj.one_way_miles=d['one_way_miles'];obj.method='manager';obj.straight_line_miles=est['straight_line'] if est else None;obj.based_on=est['based_on'] if est else ''
    obj.reason=d['reason'] or 'Miles set by a manager.';obj.verified_by=actor;obj.verified_at=timezone.now()
    obj.full_clean();obj.save();audit(actor,'distance.verified',obj,before)
    return obj,form
@serialized_compensation
@transaction.atomic
def save_trip(*,actor,event_id,data,trip_id=None):
    require_manager(actor);event=Event.objects.select_for_update().get(pk=event_id)
    obj=MileageTrip.objects.select_for_update().get(pk=trip_id,event=event) if trip_id else None
    if obj and obj.status not in ('pending','verified'):raise ValidationError('Reopen approved claims before editing. Voided claims are read-only.')
    form=TripForm(data,record=obj)
    if not form.is_valid():return None,form
    d=form.cleaned_data;revision(obj,d['revision']);staff=StaffProfile.objects.select_for_update().get(pk=d['staff'].pk);before=snap(obj) if obj else None
    if not obj:
        prior=MileageTrip.objects.filter(event=event,staff=staff)
        if prior.exists() and not d['additional']:raise ValidationError('A claim already exists. Explicitly confirm another complete round trip.')
        number=(prior.aggregate(value=Max('number'))['value'] or 0)+1
        obj=MileageTrip(event=event,staff=staff,number=number,claim_date=event.local_start_at.date(),source='manager',created_by=actor)
    obj.claim_date=d['travel_start'] or event.local_start_at.date();obj.travel_start=d['travel_start'];obj.travel_end=d['travel_end'];obj.reason=d['reason'];obj.updated_by=actor
    obj.status='pending';obj.rate=None;obj.rate_amount=None;obj.amount=None;obj.one_way_miles=None;obj.round_trip_miles=None;obj.destination='';fill_miles(obj,actor)
    obj.full_clean();obj.save();audit(actor,'trip.corrected' if before else 'trip.manual',obj,before)
    return obj,form
@serialized_compensation
@transaction.atomic
def trip_action(*,actor,trip_id,data,action):
    require_manager(actor);initial=MileageTrip.objects.get(pk=trip_id);Event.objects.select_for_update().get(pk=initial.event_id)
    obj=MileageTrip.objects.select_for_update().get(pk=trip_id);form=RevisionForm(data)
    if not form.is_valid():return None,form
    revision(obj,form.cleaned_data['revision'])
    if obj.status=='void':raise ValidationError('Voided claims are read-only.')
    before=snap(obj)
    if action in ('verify','approve'):
        if obj.status=='approved':raise ValidationError('Already approved; reopen before correction.')
        if action=='approve' and obj.staff.user_id==actor.pk:raise PermissionDenied
        if action=='approve' and obj.status=='pending':snapshot_trip(obj,actor)
        if action=='approve':
            if obj.amount is None or obj.rate_amount is None or obj.round_trip_miles is None:
                raise ValidationError('Verify the claim and review its distance, rate and amount before approval.')
            if obj.destination!=destination(obj.event):raise ValidationError('The event location changed. Verify the claim again before approval.')
        else:snapshot_trip(obj,actor)
        obj.status='approved' if action=='approve' else 'verified'
        if action=='approve':obj.approved_by=actor;obj.approved_at=timezone.now()
    elif action=='reopen':
        if obj.status!='approved':raise ValidationError('Only approved claims can be reopened.')
        obj.status='pending';obj.approved_by=None;obj.approved_at=None
    elif action=='void':obj.status='void'
    else:raise ValidationError('Unknown claim action.')
    obj.reason=form.cleaned_data['reason'];obj.updated_by=actor;obj.full_clean();obj.save();audit(actor,'trip.'+action,obj,before)
    return obj,form
