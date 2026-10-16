"""Presentation-only Home dashboard actions; no saved preferences or new metrics."""
from django.urls import reverse

# These links reuse existing workflows and their existing permission boundaries.
QUICK_ACTIONS = (
    ("Create event", "events:event_create", ("events.add_event",)),
    ("Participant directory", "participants:staff-directory", ("participants.view_participant",)),
    ("Create customer", None, ()),
    ("Events", "events:event_list", ("events.view_event",)),
    ("Qualifications", "participants:staff-qualifications", ("participants.view_participantqualification",)),
    ("Documents", "participants:staff-document-directory", ("participants.view_participantdocument",)),
    ("Operations dashboard", "events:operations-dashboard", ("events.view_syncoperation", "events.view_operationalincident", "events.view_ticketdelivery")),
    ("Quizzes and tests", "assessments:dashboard", ("assessments.view_assessmentcategory", "assessments.add_assessmentcategory")),
    ("Personal settings", "internal:settings", ()),
)
ANALYTICS_LABELS = ("Total participants", "Positions open", "Events", "Qualifications", "Documents", "Approvals")


def quick_actions_for(user):
    return [
        {"label": label, "url": reverse(route) if route else None}
        for label, route, permissions in QUICK_ACTIONS
        if not permissions or any(user.has_perm(permission) for permission in permissions)
    ]
