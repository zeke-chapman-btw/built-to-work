"""Exact-path return context; destinations retain their independent authorization."""
from urllib.parse import urlencode, urlsplit, parse_qsl

from django.urls import reverse

from apps.internal_ui.record_editors import local_return

QUERY_KEYS = {"section", "from", "page", "crm_view", "crm_q", "crm_type", "crm_from",
              "crm_to", "crm_archived", "crm_btw_page", "crm_employer_page",
              "crm_tasks_page", "crm_undated_page"}


def origin(request, *, participant=None, organization=None, fallback):
    value = request.GET.get("return_to", "")
    if participant:
        from apps.internal_ui.people_editors import people_return, SECTIONS
        profile = reverse("participants:staff-profile", args=[participant.pk])
        checked = local_return(value, fallback="", allowed={profile: QUERY_KEYS | {"archived"}}, max_length=16384, max_fields=60)
        if not checked or any(k == "section" and v not in SECTIONS for k, v in parse_qsl(urlsplit(checked).query)):
            return fallback
        return people_return(request, participant, fallback)
    allowed = {}
    if organization:
        for tab in ("overview", "crm"):
            allowed[reverse("customers:workspace", args=[organization.pk, tab])] = QUERY_KEYS
    return local_return(value, fallback=fallback, allowed=allowed, max_length=16384, max_fields=60)


def with_origin(url, destination):
    return url + ("&" if "?" in url else "?") + urlencode({"return_to": destination})
