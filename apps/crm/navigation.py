"""Read-authorized, exact-route navigation; destinations retain their own guards."""
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.urls import reverse, resolve, Resolver404
from apps.internal_ui.record_editors import local_return

QUERY_KEYS = {"section", "from", "page", "archived", "recruiting_page", "crm_view", "crm_q", "crm_type", "crm_from", "crm_to", "crm_archived", "crm_btw_page", "crm_employer_page", "crm_tasks_page", "crm_undated_page", "return_to", "crm_return_to"}
RECENT_KEYS = {"q", "scope", "crm_type", "crm_from", "crm_to", "crm_btw_page", "crm_employer_page"}
PROFILE_ANCHORS = {"btw-interactions", "employer-interactions", "relationship-followups"} | {"profile-" + key for key in ("identity", "documents", "notes", "skills", "education", "credentials", "work", "preferences", "assessments", "events", "activity", "source", "recruiting")}


def safe_return(request, value, fallback="", depth=0):
    if (not isinstance(value, str) or not value or len(value) > 16384 or depth > 2
            or any(ord(c) < 32 for c in value) or "\\" in value):
        return fallback
    try:
        from . import access
        from apps.internal_ui.people_editors import people_lists, SECTIONS
        if not getattr(request, "user", None):
            return fallback
        access.fresh_staff(request.user)
        parts = urlsplit(value)
        if parts.scheme or parts.netloc or not parts.path.startswith("/") or parts.path.startswith("//"):
            return fallback
        match = resolve(parts.path); name = match.view_name; anchors = set()
        if name == "participants:staff-profile":
            from apps.participants.models import Participant
            from apps.participants.identity import canonical_id
            person = Participant.objects.get(pk=canonical_id(match.kwargs["participant_id"]), kind=Participant.Kind.PERSON)
            access.internal_context(request.user, participant=person)
            keys = QUERY_KEYS; anchors = PROFILE_ANCHORS
        elif name == "customers:workspace" and match.kwargs.get("tab") in {"overview", "crm"}:
            from apps.customers.models import CustomerOrganization
            organization = CustomerOrganization.objects.get(pk=match.kwargs["org_id"])
            access.internal_context(request.user, organization=organization)
            keys = QUERY_KEYS; anchors = {"btw-interactions", "employer-interactions", "relationship-followups", "relationship-contacts"}
        elif name == "crm:recent":
            keys = RECENT_KEYS; anchors = {"btw-recent", "employer-recent"}
        elif name == "operations:task":
            from apps.operations.models import Task
            from apps.operations.task_access import require_task_access
            require_task_access(request.user, Task.objects.get(pk=match.kwargs["task_id"]))
            keys = {"crm_return_to"}; anchors = {"task-history"}
        elif parts.path in people_lists():
            from apps.participants.workspace_access import require_people
            require_people(request.user); keys = people_lists()[parts.path]
        elif name == "customers:directory":
            from apps.customers.access import require_manager
            require_manager(request.user); keys = {"q", "status", "provisioned", "page"}
        else:
            return fallback
        if parts.fragment and parts.fragment not in anchors:
            return fallback
        checked = local_return(urlunsplit(("", "", parts.path, parts.query, "")), fallback="", allowed={parts.path: keys}, max_length=16384, max_fields=60)
        if not checked:
            return fallback
        pairs = []
        for key, item in parse_qsl(urlsplit(checked).query, keep_blank_values=True, max_num_fields=60):
            if key == "section" and item not in SECTIONS:
                return fallback
            if key == "from":
                item = local_return(item, fallback=reverse("participants:staff-directory"), allowed=people_lists(), max_length=16384, max_fields=60)
            if key in {"return_to", "crm_return_to"}:
                item = safe_return(request, item, "", depth + 1)
                if not item:
                    continue
            pairs.append((key, item))
        return urlunsplit(("", "", parts.path, urlencode(pairs), parts.fragment))
    except (ObjectDoesNotExist, PermissionDenied, Resolver404, ValueError, TypeError, KeyError):
        return fallback


def origin(request, *, participant=None, organization=None, fallback):
    return safe_return(request, request.GET.get("return_to", ""), fallback)


def previous(request):
    if not hasattr(request, "_crm_ux_previous"):
        request._crm_ux_previous = safe_return(request, request.GET.get("crm_return_to", ""))
    return request._crm_ux_previous


def with_context(url, destination, key="return_to"):
    parts = urlsplit(url); query = parse_qsl(parts.query, keep_blank_values=True)
    query.append((key, destination))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def with_origin(url, destination):
    return with_context(url, destination)
