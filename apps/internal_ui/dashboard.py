"""Predefined staff shortcuts, filtered by current Django permissions."""
from django.urls import reverse

# Administrators grant/revoke these existing permissions on users or groups.
# No user-supplied URL is stored or rendered. Settings is available to all staff.
QUICK_ACTIONS = (
    {"id": "find-person", "label": "Find a person", "route": "participants:staff-directory", "permissions": ("participants.view_participant",), "default": True, "search": True},
    {"id": "create-task", "label": "+ Task", "route": "operations:quick-create", "permissions": (), "default": True, "create": True},
    {"id": "create-event", "label": "+ Event", "route": "events:event_create", "permissions": ("events.add_event",), "default": True, "create": True},
    {"id": "add-participant", "label": "+ Participant", "route": "admin:participants_participant_add", "permissions": ("participants.add_participant",), "default": True, "create": True},
    {"id": "log-touchpoint", "label": "+ Touchpoint", "route": "crm:start", "permissions": (), "check": "touchpoint", "default": True, "create": True, "editor": True},
    {"id": "add-customer", "label": "+ Customer", "route": "customers:new", "permissions": (), "check": "manager", "default": True, "create": True, "editor": True},
    {"id": "add-position", "label": "+ Position", "route": "crm:start-for", "args": ("position",), "permissions": (), "check": "manager", "default": True, "create": True, "editor": True},
    {"id": "add-note", "label": "+ Note", "route": "crm:start-for", "args": ("note",), "permissions": (), "check": "note", "default": True, "create": True, "editor": True},
    {"id": "participant-directory", "label": "Participants", "route": "participants:staff-directory", "permissions": ("participants.view_participant",), "default": False},
    {"id": "events", "label": "Events", "route": "events:event_list", "permissions": ("events.view_event",), "default": True},
    {"id": "qualifications", "label": "Qualifications", "route": "participants:staff-qualifications", "permissions": ("participants.view_participantqualification",), "default": False},
    {"id": "documents", "label": "Documents", "route": "participants:staff-document-directory", "permissions": ("participants.view_participantdocument",), "default": False},
    {"id": "operations", "label": "Platform monitoring", "route": "events:operations-dashboard", "permissions": ("events.view_syncoutboxitem",), "default": False},
    {"id": "quizzes", "label": "Quizzes and tests", "route": "assessments:dashboard", "permissions": ("assessments.view_assessmentcategory", "assessments.add_assessmentcategory"), "default": False},
    {"id": "settings", "label": "Personal settings", "route": "internal:settings", "permissions": (), "default": False},
    {"id": "stations", "label": "Stations", "route": "stations:list", "permissions": ("stations.view_station",), "default": False},
    {"id": "offline-preparation", "label": "Offline event preparation", "route": "events:offline-preparations", "permissions": ("events.view_offlineeventpreparation",), "default": False},
)
MAX_QUICK_ACTIONS = 9

def _allowed(user, check):
    """Cheap display check using the signed-in user's cached permissions; each pop-up re-checks fully on open."""
    from apps.accounts.roles import manager
    from apps.participants.workspace_access import can_notes
    if check == "manager":
        return bool(manager(user) and (user.is_superuser or user.has_perm("customers.manage_customers")))
    if check in ("touchpoint", "note"):
        notes = can_notes(user) and user.has_perm("participants.add_participantnote")
        return bool(notes or (check == "touchpoint" and _allowed(user, "manager")))
    return False


def available_quick_actions(user):
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return []
    actions = []
    checked = {}
    for entry in QUICK_ACTIONS:
        if entry["permissions"] and not any(user.has_perm(p) for p in entry["permissions"]):
            continue
        if entry.get("check"):
            if entry["check"] not in checked:
                checked[entry["check"]] = _allowed(user, entry["check"])
            if not checked[entry["check"]]:
                continue
        action = dict(entry)
        if action["id"] == "add-participant":
            if user.has_perm("participants.view_participant"):
                action["route"] = "participants:staff-add"
            else:
                action["label"] = "+ Participant (admin)"
        action["url"] = reverse(action["route"], args=action.get("args", ()))
        actions.append(action)
    return actions

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
