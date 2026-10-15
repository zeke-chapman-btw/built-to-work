from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import reverse

from apps.events.models import Event, EventRegistration


@login_required(login_url="/admin/login/")
def home(request):
    if not request.user.is_active or not request.user.is_staff:
        raise PermissionDenied
    can_view_events = request.user.has_perm("events.view_event")
    can_view_registrations = request.user.has_perm("events.view_eventregistration")
    actions = []
    if can_view_events:
        actions.append({"title": "Events", "description": "View event schedules, configuration, and existing operations.", "url": reverse("events:event_list"), "section": "Events"})
    if request.user.has_perm("participants.view_participantqualification"):
        actions.append({"title": "Participant qualifications", "description": "Review and maintain recorded qualifications.", "url": reverse("participants:staff-qualifications"), "section": "Participants"})
    if request.user.has_perm("participants.view_participantdocument"):
        actions.append({"title": "Participant documents", "description": "Access the authorized document-management queue.", "url": reverse("participants:staff-document-directory"), "section": "Participants"})
    if request.user.has_perm("participants.view_qualificationapprovals") or request.user.has_perm("participants.review_qualificationapprovals"):
        actions.append({"title": "Verification approvals", "description": "Review pending qualification verification decisions.", "url": reverse("participants:staff-qualification-approvals"), "section": "Participants"})
    if request.user.has_perm("events.view_syncoutboxitem"):
        actions.append({"title": "Offline operations", "description": "Review synchronization and trailer operations.", "url": reverse("events:operations-dashboard"), "section": "Operations"})
    if request.user.has_perm("stations.view_station"):
        actions.append({"title": "Stations", "description": "Open the existing station operations workspace.", "url": reverse("stations:list"), "section": "Operations"})
    if request.user.has_perm("assessments.view_question"):
        actions.append({"title": "Quizzes and tests", "description": "Manage assessment content using existing workflows.", "url": "/quizzes/", "section": "Assessments"})
    return render(request, "internal/dashboard.html", {
        "page_title": "Dashboard", "page_description": "A clear starting point for your authorized BTW work.",
        "can_view_events": can_view_events, "can_view_registrations": can_view_registrations,
        "event_count": Event.objects.count() if can_view_events else None,
        "active_registration_count": EventRegistration.objects.filter(status=EventRegistration.Status.ACTIVE).count() if can_view_registrations else None,
        "dashboard_actions": actions,
    })
