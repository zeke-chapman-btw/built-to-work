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
    return page(request,'staffing/general_event.html',page_title=event.name,general_event=event_projection(event),own_request=own,assignment=assigned,can_request=can_request,form=form,checkin_url=reverse('workdays:checkin',args=[event.pk]) if assignment and has_role(request.user,'event_staff') else None)
