"""A form's Results: how many finished, where people stop, how long it takes, every submission, and a download.

Finished sign-ups are RegistrationSubmission rows for the form's versions. Starts come from IntakeSession,
which records the last step reached; trailer tablets save offline, so their sign-ups count as finished but
can't show where people stopped.
"""
import csv
import statistics
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from .intake_models import IntakeSession
from .models import RegistrationSubmission

EASTERN = ZoneInfo("America/New_York")
SOURCES = {"online": "Website", "event": "Event sign-up", "staff": "Staff tablet", "trailer": "Trailer tablet"}
IMPORTED = "imported"  # people brought in from spreadsheets: shown only when asked for
# The sign-up's saved steps: personal information and consent share the first one.
STEPS = {1: "Personal information", 2: "Education and employment", 3: "Trade interests", 4: "Career preferences"}


def _day(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def filters(request):
    return {"start": _day(request.GET.get("from")), "end": _day(request.GET.get("to")),
            "event": request.GET.get("event", ""), "source": request.GET.get("source", "")}


def _window(qs, field, f):
    if f["start"]:
        qs = qs.filter(**{field + "__gte": datetime.combine(f["start"], time.min, EASTERN)})
    if f["end"]:
        qs = qs.filter(**{field + "__lt": datetime.combine(f["end"] + timedelta(days=1), time.min, EASTERN)})
    return qs


def submissions(form, f):
    qs = RegistrationSubmission.objects.filter(form_version__form=form, status="submitted").select_related(
        "participant", "event", "form_version")
    qs = _window(qs, "submitted_at", f)
    if f["event"].isdigit():
        qs = qs.filter(event_id=int(f["event"]))
    if f["source"] == IMPORTED:
        qs = qs.filter(source="historical_import")
    else:
        qs = qs.exclude(source="historical_import")
        if f["source"] in SOURCES:
            qs = qs.filter(intake_metadata__channel=f["source"])
    return qs.order_by("-submitted_at")


def sessions(form, f):
    qs = IntakeSession.objects.filter(form_version__form=form)
    qs = _window(qs, "created_at", f)
    if f["event"].isdigit():
        qs = qs.filter(event_id=int(f["event"]))
    if f["source"] in SOURCES:
        qs = qs.filter(channel=f["source"])
    return qs


def summary(form, f):
    done = submissions(form, f)
    now = timezone.localtime(timezone.now(), EASTERN)
    today = datetime.combine(now.date(), time.min, EASTERN)
    week = today - timedelta(days=now.weekday())
    started = list(sessions(form, f).values("current_step", "state", "created_at", "submission__submitted_at"))
    funnel = []
    if started:
        total = len(started)
        for step, name in STEPS.items():
            reached = sum(1 for s in started if s["state"] == "submitted" or s["current_step"] >= step)
            funnel.append({"label": "Started" if step == 1 else f"Reached {name}", "count": reached, "percent": round(100 * reached / total)})
        finished = sum(1 for s in started if s["state"] == "submitted")
        funnel.append({"label": "Finished", "count": finished, "percent": round(100 * finished / total), "finish": True})
    minutes = [(s["submission__submitted_at"] - s["created_at"]).total_seconds() / 60 for s in started
               if s["state"] == "submitted" and s["submission__submitted_at"]]
    minutes = [m for m in minutes if 0 < m < 180]  # ignore people who left it open for hours
    typical = statistics.median(minutes) if minutes else None
    return {"total": done.count(), "this_week": done.filter(submitted_at__gte=week).count(),
            "today": done.filter(submitted_at__gte=today).count(), "started": len(started), "funnel": funnel,
            "typical_minutes": round(typical) if typical is not None and typical >= 1 else (1 if typical else None),
            "timed": len(minutes)}


def _questions(form):
    """Every question asked on any version of the form, in the latest version's order."""
    seen, order = set(), []
    for version in form.versions.order_by("-version"):
        for q in (version.snapshot or {}).get("questions", []):
            items = [q] + [dict(c, label=q["label"] + " · " + c["label"]) for c in q.get("children", [])] if q["type"] == "repeat_group" else [q]
            for item in items:
                if item["type"] in {"repeat_group", "consent"} or item["key"] in seen:
                    continue
                seen.add(item["key"])
                order.append(item)
    return order


def _show(value, options):
    labels = {o["value"]: o["label"] for o in options or []}
    if isinstance(value, list):
        return "; ".join(str(labels.get(v, v)) for v in value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(labels.get(value, value)) if value not in (None, "") else ""


def _row_answers(submission, questions):
    answers = submission.answers or {}
    own = {q["key"]: q for q in (submission.answer_snapshot or {}).get("contract", {}).get("questions", [])}
    out = []
    for q in questions:
        options = (own.get(q["key"]) or q).get("options", [])
        value = answers.get(q["key"])
        if value is None:  # a question inside the work experience list
            rows = answers.get("experiences") or []
            if isinstance(rows, list) and rows and isinstance(rows[0], dict) and q["key"] in rows[0]:
                value = " | ".join(_show(r.get(q["key"]), q.get("options")) for r in rows)
                out.append(value)
                continue
        out.append(_show(value, options))
    return out


def source_label(submission):
    if submission.source == "historical_import":
        return "Imported"
    return SOURCES.get((submission.intake_metadata or {}).get("channel"), "")


@never_cache
def results(request, form_id):
    from apps.events.models import Event
    from .intake_config_views import _form, require_admin
    require_admin(request.user)
    form = _form(form_id)
    f = filters(request)
    qs = submissions(form, f)
    if request.GET.get("download") == "1":
        return download(form, qs)
    rows = [{"submission": s, "source": source_label(s), "when": timezone.localtime(s.submitted_at, EASTERN)} for s in qs[:200]]
    event_ids = RegistrationSubmission.objects.filter(form_version__form=form).exclude(event=None).values("event_id")
    events = Event.objects.filter(pk__in=event_ids).order_by("-start_at")
    return render(request, "participants/form_results.html", {
        "intake_form": form, "page_title": form.name + " · Results", "stats": summary(form, f), "rows": rows,
        "shown": len(rows), "f": f, "events": events, "sources": list(SOURCES.items()) + [(IMPORTED, "Imported from spreadsheets")],
        "query": request.GET.urlencode(), "filtered": any((f["start"], f["end"], f["event"], f["source"]))})


def download(form, qs):
    questions = _questions(form)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    name = "".join(ch if ch.isalnum() else "-" for ch in form.name.lower()).strip("-") or "form"
    response["Content-Disposition"] = f'attachment; filename="{name}-submissions.csv"'
    response["Cache-Control"] = "no-store"
    response.write("﻿")  # opens cleanly in Excel
    writer = csv.writer(response)
    writer.writerow(["Submitted (Eastern)", "Participant ID", "Where", "Event", "Form version"] + [q["label"] for q in questions])
    for s in qs.iterator(chunk_size=500):
        when = timezone.localtime(s.submitted_at, EASTERN).strftime("%Y-%m-%d %I:%M %p") if s.submitted_at else ""
        cells = [when, s.participant_id or "", source_label(s), s.event.name if s.event else "", s.form_version.version]
        writer.writerow([_safe(c) for c in cells + _row_answers(s, questions)])
    return response


def _safe(value):
    """Spreadsheet apps run text starting with = + - @ as a formula; keep it as plain text."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text
