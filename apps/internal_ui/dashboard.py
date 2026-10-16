"""Predefined staff shortcuts, filtered by current Django permissions."""
from django.urls import reverse

# Administrators grant/revoke these existing permissions on users or groups.
# No user-supplied URL is stored or rendered. Settings is available to all staff.
QUICK_ACTIONS = (
    {"id": "create-event", "label": "Create event", "route": "events:event_create", "permissions": ("events.add_event",), "default": True},
    {"id": "participant-directory", "label": "Participant directory", "route": "participants:staff-directory", "permissions": ("participants.view_participant",), "default": True},
    {"id": "events", "label": "Events", "route": "events:event_list", "permissions": ("events.view_event",), "default": True},
    {"id": "qualifications", "label": "Qualifications", "route": "participants:staff-qualifications", "permissions": ("participants.view_participantqualification",), "default": True},
    {"id": "documents", "label": "Documents", "route": "participants:staff-document-directory", "permissions": ("participants.view_participantdocument",), "default": True},
    {"id": "operations", "label": "Operations dashboard", "route": "events:operations-dashboard", "permissions": ("events.view_syncoutboxitem",), "default": True},
    {"id": "quizzes", "label": "Quizzes and tests", "route": "assessments:dashboard", "permissions": ("assessments.view_assessmentcategory", "assessments.add_assessmentcategory"), "default": True},
    {"id": "settings", "label": "Personal settings", "route": "internal:settings", "permissions": (), "default": True},
    {"id": "stations", "label": "Stations", "route": "stations:list", "permissions": ("stations.view_station",), "default": False},
    {"id": "offline-preparation", "label": "Offline event preparation", "route": "events:offline-preparations", "permissions": ("events.view_offlineeventpreparation",), "default": False},
)
MAX_QUICK_ACTIONS = 9
ANALYTICS_LABELS = ("Total participants", "Positions open", "Events", "Qualifications", "Documents", "Approvals")

def available_quick_actions(user):
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return []
    return [dict(action, url=reverse(action["route"])) for action in QUICK_ACTIONS
            if not action["permissions"] or any(user.has_perm(p) for p in action["permissions"])]

def quick_actions_for(user):
    available = available_quick_actions(user)
    saved = user.quick_action_ids
    if saved is None:
        return [action for action in available if action["default"]][:MAX_QUICK_ACTIONS]
    if not isinstance(saved, list):
        return []
    by_id = {action["id"]: action for action in available}
    selected = []
    seen = set()
    for action_id in saved:
        if isinstance(action_id, str) and action_id in by_id and action_id not in seen:
            selected.append(by_id[action_id])
            seen.add(action_id)
    return selected[:MAX_QUICK_ACTIONS]
