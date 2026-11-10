"""Central registry for BTW staff workspace navigation and landing links.

Labels and order live here; existing feature URLs and backend permissions remain
owned by their applications. Hidden links are never an authorization boundary.
"""
from django.urls import reverse


WORKSPACES = (
    {"id": "home", "label": "HOME", "icon": "home", "url_name": "internal:home", "description": "Your authorized BTW work at a glance.", "permissions": ()},
    {"id": "events", "label": "EVENTS", "icon": "calendar", "url_name": "events:event_list", "description": "Events, registrations, and ticket workflows.", "permissions": ("events.view_event", "events.add_event", "events.view_eventregistration")},
    {"id": "participants", "label": "PARTICIPANTS", "icon": "people", "url_name": "internal:workspace", "url_kwargs": {"workspace_id": "participants"}, "description": "Existing participant records and review workflows.", "permissions": ("participants.view_participant", "participants.view_participantqualification", "participants.view_participantdocument", "participants.view_qualificationapprovals", "participants.view_historicalimportbatch", "participants.view_participantaccountrequest")},
    {"id": "operations", "label": "OPERATIONS", "icon": "operations", "url_name": "internal:workspace", "url_kwargs": {"workspace_id": "operations"}, "description": "Trailer, station, synchronization, and operational workflows.", "permissions": ("stations.view_station", "events.view_trailer", "events.view_syncoperation", "events.view_ticketdelivery", "events.view_matchreview")},
    {"id": "assessments", "label": "ASSESSMENTS", "icon": "assessments", "url_name": "internal:workspace", "url_kwargs": {"workspace_id": "assessments"}, "description": "Existing quizzes, assessment content, and results.", "permissions": ("assessments.view_assessmentcategory", "assessments.view_quizattempt", "assessments.add_assessmentcategory")},
    {"id": "administration", "label": "ADMINISTRATION", "icon": "settings", "url_name": "internal:workspace", "url_kwargs": {"workspace_id": "administration"}, "description": "Authorized BTW configuration and staff administration.", "permissions": ("events.change_event", "participants.change_registrationform", "auth.change_user")},
)

WORKSPACE_LINKS = {
    "participants": (
        {"label": "Qualifications", "description": "View and maintain recorded qualifications.", "url_name": "participants:staff-qualifications", "permissions": ("participants.view_participantqualification",)},
        {"label": "Documents", "description": "Open the authorized participant document queue.", "url_name": "participants:staff-document-directory", "permissions": ("participants.view_participantdocument",)},
        {"label": "Verification approvals", "description": "Review pending qualification verification decisions.", "url_name": "participants:staff-qualification-approvals", "permissions": ("participants.view_qualificationapprovals", "participants.review_qualificationapprovals")},
        {"label": "Historical records", "description": "Import or export authorized historical participant data.", "url_name": "participants:historical-index", "permissions": ("participants.view_historicalimportbatch", "participants.add_historicalimportbatch", "participants.export_historicalparticipant")},
        {"label": "Account requests", "description": "Review participant account requests.", "url_name": "participants:staff-requests", "permissions": ("participants.view_participantaccountrequest", "participants.change_participantaccountrequest")},
    ),
    "operations": (
        {"label": "Operations dashboard", "description": "Review synchronization, alerts, backups, and delivery status.", "url_name": "events:operations-dashboard", "permissions": ("events.view_syncoperation", "events.view_operationalincident", "events.view_ticketdelivery")},
        {"label": "Offline event preparation", "description": "Prepare and monitor available trailer events.", "url_name": "events:offline-preparations", "permissions": ("events.view_offlineeventpreparation", "events.add_offlineeventpreparation")},
        {"label": "Stations", "description": "Open the existing station operations workspace.", "url_name": "stations:list", "permissions": ("stations.view_station",)},
    ),
    "assessments": (
        {"label": "Quizzes and tests", "description": "Manage assessment content using existing workflows.", "url_name": "assessments:dashboard", "permissions": ("assessments.view_assessmentcategory", "assessments.add_assessmentcategory")},
    ),
    "administration": (
        {"label": "Django administration", "description": "Open authorized system configuration.", "url_name": "admin:index", "permissions": ("auth.view_user", "auth.change_user")},
        {"label": "Registration form builder", "description": "Configure the existing participant intake form.", "url_name": "participants:registration-forms", "permissions": ("participants.change_registrationform",)},
        {"label": "Test Participant", "description": "Manage the reserved nonofficial test participant.", "url_name": "participants:testing", "permissions": ("participants.view_testparticipant", "participants.change_testparticipant")},
    ),
}


def _can_see(user, permissions):
    return user.is_superuser or not permissions or any(user.has_perm(permission) for permission in permissions)


def _resolved(item):
    entry = dict(item)
    entry["url"] = reverse(entry["url_name"], kwargs=entry.get("url_kwargs", {}))
    return entry


def visible_workspaces(user, current_path=""):
    """Return ordered, URL-resolved workspaces visible to this staff user."""
    visible = []
    for item in WORKSPACES:
        if not _can_see(user, item["permissions"]):
            continue
        entry = _resolved(item)
        entry["active"] = (current_path == entry["url"] if item["id"] == "home" else current_path.startswith(entry["url"]))
        visible.append(entry)
    return visible


def visible_workspace_links(user, workspace_id):
    """Resolve only real, permission-authorized feature destinations."""
    return [_resolved(item) for item in WORKSPACE_LINKS.get(workspace_id, ()) if _can_see(user, item["permissions"]) ]
