"""Culture Index: one shared survey link staff send by text or email, plus a nightly copy of results.

Culture Index's API only reads results (it can't send surveys), so everyone takes the same survey link. Once an
administrator saves the API key in Settings → Connections, results are fetched once a night and matched to
participants by email address.
"""
import json
import logging
import socket
from datetime import datetime, time, timedelta
from urllib import error, parse, request
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from . import store

log = logging.getLogger(__name__)
KEY = "cultureindex"
DEFAULT_LINK = "https://surveys.cultureindex.com/s/0b93FI1emn/127318"
BASE = "https://apim-cindex-prod-01.azure-api.net"
API_VERSION = "2022-10-01"
DEFAULT_PATH = "/Surveys"
EASTERN = ZoneInfo("America/New_York")
SYNC_HOUR = 2  # 2 AM Eastern
PAGE_SIZE = 100
MAX_PAGES = 100


class CultureIndexError(Exception):
    def __init__(self, message, *, refused=False):
        super().__init__(message)
        self.refused = refused


def row():
    return store._row(KEY)


def survey_link():
    current = row()
    return ((current.values.get("survey_link") if current else "") or DEFAULT_LINK).strip()


def in_portal():
    current = row()
    return bool(survey_link()) and (current.values.get("portal", True) if current else True)


def api_key():
    return store.secret(row())


def call(path, *, key=None, query=None):
    key = (key or api_key() or "").strip()
    if not key:
        raise CultureIndexError("Culture Index isn't connected yet.")
    url = BASE + path + "?" + parse.urlencode(dict(query or {}, **{"api-version": API_VERSION}))
    # The API portal asks for the key twice: Azure's "Subscription key" and a "subscriptionkey" header.
    req = request.Request(url, headers={"Ocp-Apim-Subscription-Key": key, "subscriptionkey": key,
                                        "Accept": "application/json", "Cache-Control": "no-cache",
                                        "User-Agent": "BuiltToWork/1.0"})
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else []
    except error.HTTPError as exc:
        said = _their_words(exc)
        if exc.code in (401, 403):
            raise CultureIndexError(f"Culture Index refused the API key ({exc.code}{': ' + said if said else ''}).", refused=True) from None
        raise CultureIndexError(f"Culture Index answered with error {exc.code}{': ' + said if said else ''}.") from None
    except (OSError, ValueError, Exception):  # timeouts, dropped connections, bad replies
        raise CultureIndexError("Culture Index could not be reached.") from None


def _their_words(exc):
    """Culture Index's own short explanation of an error, so a wrong key or wrong setting is easy to tell apart."""
    try:
        body = exc.read(2000).decode("utf-8", "replace")
        data = json.loads(body)
        text = data.get("message") or data.get("detail") or data.get("title") or (data.get("error") or {}).get("message") or ""
    except Exception:
        text = ""
    text = " ".join(str(text).split())
    return text[:160]


def _items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for name in ("value", "items", "surveys", "data", "results"):
            if isinstance(data.get(name), list):
                return data[name]
    return []


def _when(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if timezone.is_aware(parsed) else parsed.replace(tzinfo=EASTERN)


def save_results(items):
    """Store each survey once (by its id) and link it to the participant with that email."""
    from apps.participants.models import Participant
    from .models import CultureIndexResult
    added = matched = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        survey_id = str(item.get("surveyId") or item.get("id") or "").strip()
        if not survey_id:
            continue
        email = (item.get("email") or "").strip().lower()
        person = (Participant.objects.filter(contact_email__iexact=email, archived_at__isnull=True).order_by("pk").first()
                  if email else None)
        matches = sorted(({"name": str(m.get("positionName") or "")[:120], "percent": m.get("matchPercent")}
                          for m in (item.get("appliedToPositionsMatches") or []) if isinstance(m, dict)),
                         key=lambda m: -(m["percent"] or 0))[:10]
        values = {
            "participant": person, "email": email[:254],
            "name": " ".join(x for x in (item.get("firstName"), item.get("lastName")) if x)[:160],
            "taken_at": _when(item.get("surveyDate")), "pattern": str(item.get("traitPattern") or "")[:80],
            "report_link": str(item.get("surveyReportLink") or "")[:500],
            "pattern_link": str(item.get("traitPatternDotsLink") or "")[:500],
            "job_matches": matches, "traits": item.get("traitDots") if isinstance(item.get("traitDots"), dict) else {},
        }
        result, created = CultureIndexResult.objects.update_or_create(survey_id=survey_id[:80], defaults=values)
        added += int(created)
        matched += int(bool(person))
    return {"added": added, "matched": matched, "seen": len(items)}


def sync(*, key=None):
    current = row()
    path = ((current.values.get("list_path") if current else "") or DEFAULT_PATH)
    # Results come back a page at a time; keep asking (skipping what we have) until a page brings nothing new.
    items, seen_ids, skip = [], set(), 0
    for _ in range(MAX_PAGES):
        batch = [i for i in _items(call(path, key=key, query={"Top": PAGE_SIZE, "Skip": skip, "MaxPageSize": PAGE_SIZE}))
                 if isinstance(i, dict)]
        new = [i for i in batch if str(i.get("surveyId") or i.get("id") or "") not in seen_ids]
        if not new:
            break
        seen_ids.update(str(i.get("surveyId") or i.get("id") or "") for i in new)
        items.extend(new)
        skip += len(batch)
    outcome = save_results(items)
    current = row()
    if current:
        current.values = dict(current.values, last_sync=timezone.now().isoformat(), last_outcome=outcome, last_error="")
        current.last_test_ok, current.last_test_at = True, timezone.now()
        current.save(update_fields=["values", "last_test_ok", "last_test_at"])
    return outcome


def due(now=None):
    """Once a night, after 2 AM Eastern, when a key is saved."""
    current = row()
    if not (current and current.secret_encrypted):
        return False
    now = timezone.localtime(now or timezone.now(), EASTERN)
    if now.time() < time(SYNC_HOUR):
        return False
    last = _when(current.values.get("last_sync"))
    tried = _when(current.values.get("last_try"))
    today = datetime.combine(now.date(), time(SYNC_HOUR), EASTERN)
    if tried and tried >= today and tried > now - timedelta(hours=1) and not (last and last >= tried):
        return False  # failed in the last hour; wait before trying again
    return not (last and last >= today)


def run_nightly(now=None):
    if not due(now):
        return None
    current = row()
    with transaction.atomic():
        current.values = dict(current.values, last_try=timezone.now().isoformat())
        current.save(update_fields=["values"])
    try:
        return sync()
    except CultureIndexError as exc:
        current = row()
        current.values = dict(current.values, last_error=str(exc))
        current.last_test_ok, current.last_test_at = False, timezone.now()
        current.save(update_fields=["values", "last_test_ok", "last_test_at"])
        log.warning("CULTURE_INDEX_SYNC_FAILED %s", exc)
        return None


def for_participant(participant):
    """The latest result, and when the survey link was last sent, for the profile card."""
    from apps.participants.identity import identity_members
    from .models import CultureIndexResult, SentEmail, TextMessage
    ids = identity_members(participant.pk)
    result = CultureIndexResult.objects.filter(participant_id__in=ids).order_by("-taken_at", "-pk").first()
    link = survey_link()
    text = (TextMessage.objects.filter(participant_id__in=ids, direction="out", state="sent", body__contains=link)
            .order_by("-created_at").first() if link else None)
    email = SentEmail.objects.filter(participant_id__in=ids, status="sent", subject__icontains="culture index").order_by("-created_at").first()
    sent = max((x for x in (text, email) if x), key=lambda x: x.created_at, default=None)
    return {"result": result, "sent_at": sent.created_at if sent else None,
            "sent_by": "text" if sent is text and sent else ("email" if sent else "")}
