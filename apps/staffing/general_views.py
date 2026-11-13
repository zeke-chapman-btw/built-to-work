"""Explicit general event projection: never hand private Event records to templates."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from apps.accounts.roles import has_role,manager
from apps.events.models import Event
from apps.internal_ui.views import _staff_context
from . import services
from .models import StaffingPlan
from .forms import AvailabilityForm
from .views import staff_view

def published_events():
    return Event.objects.exclude(status='draft').filter(Q(staff_published_at__isnull=False)|Q(status='upcoming',staffing_plan__is_published=True)).distinct()

def event_projection(event):
    return dict(id=event.pk,name=event.name,start_at=event.local_start_at,end_at=event.local_end_at,timezone_name=event.timezone_name,location_name=event.location_name,city=event.city,state=event.state,status=event.get_status_display(),url=reverse('staffing:general-event',args=[event.pk]))

def page(request,template,**values):
    response=render(request,template,_staff_context(request,**values))
    response['Cache-Control']='private, no-store';return response

def require_schedule(user):
    if not (has_role(user,'event_staff') or manager(user)):raise PermissionDenied

@staff_view
@require_http_methods(['GET'])
def schedule(request):
    require_schedule(request.user)
    events=published_events().order_by('-start_at','pk')
    view=request.GET.get('view','opportunities')
    if view not in ('opportunities','history','requests','assignments'):view='opportunities'
    if view=='opportunities':events=events.filter(status='upcoming',end_at__gt=timezone.now(),staffing_plan__is_published=True)
    page_events=Paginator(events,20).get_page(request.GET.get('page'))
    projected=[event_projection(event) for event in page_events]
    profile=services.own_profile(request.user)
    own_requests=[];own_assignments=[]
    if profile and view=='requests':
        own_requests=Paginator(profile.availability_requests.filter(event__in=published_events()).select_related('event').order_by('-created_at'),20).get_page(request.GET.get('page'))
        projected=[dict(event_projection(row.event),request_status=row.get_status_display(),dates=row.requested_dates,note=row.note) for row in own_requests]
        page_events=own_requests
    if profile and view=='assignments':
        own_assignments=Paginator(profile.assignments.filter(event__in=published_events()).select_related('event').prefetch_related('days').order_by('-created_at'),20).get_page(request.GET.get('page'))
        projected=[dict(event_projection(row.event),assignment_status=row.get_status_display(),checkin_url=reverse('workdays:checkin',args=[row.event_id]) if row.status=='confirmed' and has_role(request.user,'event_staff') else None,dates=[day.date for day in row.days.all()]) for row in own_assignments]
        page_events=own_assignments
    return page(request,'staffing/general_schedule.html',page_title='Events & my schedule',events=projected,page=page_events,selected_view=view,profile_linked=bool(profile))

@staff_view
@require_http_methods(['GET','POST'])
def detail(request,event_id):
    require_schedule(request.user)
    event=get_object_or_404(published_events(),pk=event_id)
    profile=services.own_profile(request.user)
    plan=StaffingPlan.objects.filter(event=event).first()
    own_request=event.staff_availability.filter(staff=profile).first() if profile else None
    assignment=event.staff_assignments.filter(staff=profile,status='confirmed').prefetch_related('days').first() if profile else None
    can_request=bool(has_role(request.user,'event_staff') and profile and services.opportunity_open(event,plan) and not assignment and not (own_request and own_request.status in ('pending','approved')))
    form=AvailabilityForm(event=event)
    if request.method=='POST':
        if not can_request:raise PermissionDenied
        try:
            saved,form=services.request_availability(actor=request.user,event_id=event.pk,data=request.POST)
            if saved:
                messages.success(request,'Request submitted as Pending. A Manager or Administrator must approve it before it becomes an assignment.')
                return redirect('staffing:general-event',event_id=event.pk)
        except ValidationError as exc:
            form=AvailabilityForm(request.POST,event=event);form.is_valid();form.add_error(None,exc)
    own=dict(status=own_request.get_status_display(),dates=own_request.requested_dates,note=own_request.note) if own_request else None
    assigned=dict(status=assignment.get_status_display(),days=[dict(date=day.date,arrival=day.arrival_at,start=day.starts_at,end=day.ends_at) for day in assignment.days.all()]) if assignment else None
    # Owner decision (Nov 12): staff assigned to an event see where to go, where they stay,
    # who to call and the travel/setup notes. Unassigned staff still get the summary only.
    logistics=None
    if assignment:
        from .my_views import _stay
        logistics=dict(address=", ".join(v for v in (event.address_line_1,event.address_line_2,event.city,event.state,event.postal_code) if v),
            setup_date=event.setup_date,arrival=event.staff_arrival_time,return_date=event.return_date,travel=event.logistics_instructions,
            housing=_stay(event),housing_notes=event.housing_information,
            contact=dict(name=event.contact_name,phone=event.contact_phone,email=event.contact_email))
    return page(request,'staffing/general_event.html',page_title=event.name,general_event=event_projection(event),own_request=own,assignment=assigned,logistics=logistics,can_request=can_request,form=form,checkin_url=reverse('workdays:checkin',args=[event.pk]) if assignment and has_role(request.user,'event_staff') else None)


@staff_view
@require_http_methods(['GET'])
def participants(request,event_id):
    """Assigned staff can look people up at their event to help with tickets and stations.

    Owner decision (Nov 12). Shows name, city, ticket number, sign-up time and station
    progress; no contact details, birth dates or notes. Each view is audited.
    """
    from apps.core.audit import record_audit
    from apps.events.models import EventRegistration
    event=get_object_or_404(Event,pk=event_id)
    profile=services.own_profile(request.user)
    assigned=bool(profile and event.staff_assignments.filter(staff=profile,status='confirmed').exists())
    if not (assigned or manager(request.user)) or event.status in ('draft','archived'):raise PermissionDenied
    q=request.GET.get('q','').strip()[:80]
    rows=(EventRegistration.objects.filter(event=event,status='active').select_related('participant')
          .prefetch_related('tickets','experience_session__activities').order_by('participant__last_name','participant__first_name'))
    if q:
        rows=rows.filter(Q(participant__first_name__icontains=q)|Q(participant__last_name__icontains=q)|Q(participant__preferred_name__icontains=q)|Q(tickets__ticket_number__icontains=q.replace(' ',''))).distinct()
    page_rows=Paginator(rows,50).get_page(request.GET.get('page'))
    people=[]
    for reg in page_rows:
        person=reg.participant
        ticket=next((t for t in reg.tickets.all() if t.is_current),None)
        session=getattr(reg,'experience_session',None)
        done={a.activity:a.status for a in session.activities.all()} if session else {}
        people.append(dict(name=f"{person.preferred_name or person.first_name} {person.last_name}".strip(),city=", ".join(v for v in (person.city,person.state) if v),
            ticket=ticket.ticket_number if ticket else '',ticket_ok=bool(ticket and not ticket.revoked_at),registered=reg.registered_at,source=reg.get_source_display(),
            steps=[(label,done.get(key,'')) for key,label in (('kiosk','Quiz'),('game','Game'),('simulator','Simulator')) if key in done]))
    record_audit(actor=request.user,action='event.participants.viewed',instance=event,source='staff_event_participants',new_data={'search':bool(q),'page':page_rows.number})
    return page(request,'staffing/event_participants.html',page_title=event.name,general_event=event_projection(event),people=people,page=page_rows,q=q,total=rows.count())
