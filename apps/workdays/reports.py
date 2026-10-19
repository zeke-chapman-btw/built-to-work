"""Separate workday and trip report rows prevent mileage multiplication."""
import csv
from decimal import Decimal
from django import forms
from django.db.models import Exists, OuterRef, Sum
from django.http import HttpResponse
from .models import Workday, MileageTrip, StaffRole
from apps.staffing.models import StaffProfile
from apps.events.models import Event
class ReportFilter(forms.Form):
    event=forms.ModelChoiceField(queryset=Event.objects.all(),required=False)
    employee=forms.ModelChoiceField(queryset=StaffProfile.objects.all(),required=False)
    start=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}))
    role=forms.ModelChoiceField(queryset=StaffRole.objects.all(),required=False)
    def clean(self):
        d=super().clean()
        if d.get('start') and d.get('end') and d['end']<d['start']:self.add_error('end','End date must follow start date.')
        return d

def querysets(filters):
    days=Workday.objects.select_related('event','staff','role','pay_rate','per_diem_rate')
    trips=MileageTrip.objects.select_related('event','staff','rate')
    for key,field in [('event','event'),('employee','staff')]:
        if filters.get(key):days=days.filter(**{field:filters[key]});trips=trips.filter(**{field:filters[key]})
    for key,op in [('start','gte'),('end','lte')]:
        if filters.get(key):days=days.filter(**{'work_date__'+op:filters[key]});trips=trips.filter(**{'claim_date__'+op:filters[key]})
    if filters.get('role'):
        days=days.filter(role=filters['role'])
        matches=Workday.objects.filter(event_id=OuterRef('event_id'),staff_id=OuterRef('staff_id'),role=filters['role'],is_active=True)
        trips=trips.filter(Exists(matches))
    return days,trips

def totals(days,trips):
    pay=days.filter(is_active=True,approval='approved').aggregate(pay=Sum('pay_amount'),per_diem=Sum('per_diem_amount'))
    return dict(pay=pay['pay'] or Decimal('0'),per_diem=pay['per_diem'] or Decimal('0'),mileage=trips.filter(status='approved').aggregate(total=Sum('amount'))['total'] or Decimal('0'))

def safe(value):
    text=str(value) if value is not None else ''
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text

def csv_export(days,trips):
    response=HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition']='attachment; filename="staff-workdays-and-trips.csv"'
    response['Cache-Control']='private, no-store'
    writer=csv.writer(response)
    writer.writerow(['Record type','Employee','Event','Work/trip date','Daily role','Status','Source','Drove','Daily pay USD','Per diem eligibility','Per diem USD','Trip number','One-way miles','Round-trip miles','USD per mile','Trip reimbursement USD','Submission timestamp','Effective check-in','Approved at'])
    for d in days.iterator():
        writer.writerow(map(safe,['workday',d.staff,d.event.name,d.work_date,d.role_name,d.approval if d.is_active else 'void',d.get_source_display(),d.drove,d.pay_amount if d.is_active else None,d.per_diem,d.per_diem_amount if d.is_active else None,'','','','','',d.submitted_at,d.effective_checkin_at,d.approved_at]))
    for t in trips.iterator():
        writer.writerow(map(safe,['mileage_trip',t.staff,t.event.name,t.claim_date,'',t.status,t.get_source_display(),'','','','',t.number,t.one_way_miles,t.round_trip_miles,t.rate_amount,t.amount if t.status!='void' else None,'','',t.approved_at]))
    return response
