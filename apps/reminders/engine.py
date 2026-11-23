"""Decide which reminders are due, who gets them, and send them once.

Runs every few minutes inside the message worker (apps/messaging/runtime.py),
only while Reminders are turned on in Settings → Reminders. Every send is
claimed in ReminderSend first, so two workers can never send the same one.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import sources
from .models import ReminderNotice, ReminderPreference, ReminderRole, ReminderRule, ReminderSend, ReminderSetup, ReminderWatch

log = logging.getLogger(__name__)
TEXT_HOURS = (time(8, 0), time(21, 0))
STALE_DAYS = 3


# ------------------------------------------------------------------ timing

def add_days(day, n, business):
    if not business:
        return day + timedelta(days=n)
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        day += timedelta(days=step)
        if day.weekday() < 5:
            remaining -= 1
    return day


def occurrence(rule, item, now_local, turned_on_at=None):
    """The key for the reminder due now for this item, or None if nothing is due."""
    today = now_local.date()
    if rule.weekdays_only and today.weekday() >= 5:
        return None
    early = now_local.time() < rule.send_time
    days = rule.day_list()
    if rule.mode == ReminderRule.Mode.BEFORE:
        if not item.anchor or today > item.anchor:
            return None
        best = None
        for d in days or [0]:
            fire = add_days(item.anchor, -d, rule.business_days)
            if fire <= today and (best is None or fire > best[1]):
                best = (d, fire)
        if not best or (best[1] == today and early):
            return None
        return f"b{best[0]}"
    if rule.mode == ReminderRule.Mode.AFTER:
        if not item.anchor:
            return None
        base = add_days(item.anchor, min(days) if days else 0, rule.business_days)
        if today < base:
            return None
        n = (today - base).days // rule.repeat_every if rule.repeat_every else 0
        fire = base + timedelta(days=n * rule.repeat_every)
        if (today - fire).days > STALE_DAYS or (fire == today and early):
            return None
        return f"a{n}"
    if rule.mode == ReminderRule.Mode.NEW:
        created = item.created or (datetime.combine(item.anchor, time(0), sources.EASTERN) if item.anchor else None)
        if not created:
            return None
        created_day = timezone.localtime(created, sources.EASTERN).date()
        n = (today - created_day).days // rule.repeat_every if rule.repeat_every else 0
        if n == 0:
            if (today - created_day).days > STALE_DAYS or (turned_on_at and created < turned_on_at - timedelta(minutes=5)
                                                            and not rule.repeat_every):
                return None
            return "new"
        fire = created_day + timedelta(days=n * rule.repeat_every)
        if fire == today and early:
            return None
        return f"n{n}"
    return None


# -------------------------------------------------------------- recipients

def role_targets(role, cache=None):
    if cache is not None and role.key in cache:
        return cache[role.key]
    people = [t for t in (sources.user_target(u) for u in role.holders.filter(is_active=True)) if t]
    if not people and role.waiting_for.strip():
        found = waiting_user(role.waiting_for)
        if found:
            people = [sources.user_target(found)]
    people = people or sources.admins()
    if cache is not None:
        cache[role.key] = people
    return people


def waiting_user(name):
    """An active staff user whose full name matches someone we were waiting for."""
    parts = name.strip().split()
    if len(parts) < 2:
        return None
    matches = list(get_user_model().objects.filter(is_active=True, is_staff=True, first_name__iexact=parts[0],
                                                   last_name__iexact=parts[-1])[:2])
    return matches[0] if len(matches) == 1 else None


def targets_for(rule, item, roles=None, cache=None):
    spec = rule.recipients or {}
    found = []
    for key in spec.get("related", []):
        found += item.related.get(key, []) if item else []
    roles = roles if roles is not None else {r.key: r for r in ReminderRole.objects.all()}
    for key in spec.get("roles", []):
        if key in roles:
            found += role_targets(roles[key], cache)
    if spec.get("people"):
        users = get_user_model().objects.filter(pk__in=spec["people"], is_active=True).select_related("staff_profile")
        found += [t for t in (sources.user_target(u) for u in users) if t]
    if not found and rule.backup == ReminderRule.Backup.ADMINS:
        found = sources.admins()
    unique = {}
    for t in found:
        if t and t.key not in unique:
            unique[t.key] = t
    return list(unique.values())


def describe_recipients(rule, roles=None):
    """Plain words for the settings list: "Recruiting lead (Jason Collins), the recruiter"."""
    spec = rule.recipients or {}
    source = sources.SOURCES.get(rule.source)
    words = []
    roles = roles if roles is not None else {r.key: r for r in ReminderRole.objects.prefetch_related("holders")}
    for key in spec.get("related", []):
        if source and key in source.related:
            words.append(source.related[key])
    for key in spec.get("roles", []):
        role = roles.get(key)
        if role:
            names = [u.get_full_name() or u.get_username() for u in role.holders.all() if u.is_active]
            words.append(f"{role.name} ({', '.join(names) or role.waiting_for or 'not set'})")
    if spec.get("people"):
        users = get_user_model().objects.filter(pk__in=spec["people"])
        words += [u.get_full_name() or u.get_username() for u in users]
    return words


# ----------------------------------------------------------------- wording

class _Blank(dict):
    def __missing__(self, key):
        return ""


def fill(text, values):
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), "")), text or "")


def compose(rule, items, target):
    """Title and body for one person; several items become one list."""
    base = {"first_name": target.first_name}
    if len(items) == 1:
        item = items[0]
        values = _Blank(base, line=item.line, **item.fields)
        if target.first_name and "first_name" not in item.fields:
            values["first_name"] = target.first_name
        title = fill(rule.subject, values).strip() or item.line
        body = fill(rule.message, values).strip() or item.line
        return title, body, item.link
    title = f"{rule.name} ({len(items)})"
    body = "\n".join("• " + i.line for i in items)
    return title, body, items[0].link if len({i.link for i in items}) == 1 else ""


def absolute(link):
    base = (getattr(settings, "PARTICIPANT_PORTAL_BASE_URL", "") or "").rstrip("/")
    return (base + link) if (base and link.startswith("/")) else link


# ---------------------------------------------------------------- delivery

def deliver_bell(rule, target, title, body, link):
    if not target.user:
        raise ValueError("No app account")
    from apps.operations.models import TaskNotification
    message = title if not body or body.strip() == title.strip() else f"{title} — {body}"
    TaskNotification.objects.create(recipient=target.user, kind=TaskNotification.Kind.REMINDER,
                                    message=" ".join(message.split())[:240], link=(link or "")[:300])


def deliver_email(rule, target, title, body, link):
    from django.core.mail import send_mail
    from apps.messaging import bounces, store
    if not target.email:
        raise ValueError("No email address")
    if bounces.bounced(target.email):
        raise ValueError("Email address bounced")
    config = store.email_settings()
    # customers and participants never get staff-only links; customers get their portal page instead
    url = absolute(target.portal_url) if target.portal_url else (absolute(link) if target.user else "")
    send_mail(title, body + (f"\n\nOpen: {url}" if url else ""), config.get("from_address") or None, [target.email],
              fail_silently=False, connection=store.smtp_connection())


def deliver_text(rule, target, title, body, link):
    from apps.events import sms_providers
    from apps.messaging import texting
    from apps.messaging.models import TextMessage
    key = texting.phone_key(target.phone)
    if not key:
        raise ValueError("No mobile number")
    if texting.opted_out(key):
        raise ValueError("Replied STOP")
    if not texting.ready():
        raise ValueError("Texting isn't set up")
    row = TextMessage.objects.create(direction=TextMessage.Direction.OUT, phone=key, participant=target.participant,
                                     body=body[:1600], sent_by=None, replies_to=texting.default_owner(),
                                     state=TextMessage.State.SENT, operation_id=uuid.uuid4())
    try:
        reference = sms_providers.send_sms(key, body, idempotency_key=f"reminder-text-{row.pk}")
    except Exception as exc:
        row.state, row.error = TextMessage.State.FAILED, str(exc)[:240]
        row.save(update_fields=["state", "error"])
        raise
    row.provider_reference = (reference or "")[:160]
    row.save(update_fields=["provider_reference"])


DELIVER = {"bell": deliver_bell, "email": deliver_email, "text": deliver_text}


def _claim(rule, item, occ, target, channel):
    try:
        with transaction.atomic():
            return ReminderSend.objects.create(rule=rule, ref=item.ref[:120], occurrence=occ[:60], target=target.key,
                                               channel=channel)
    except IntegrityError:
        return None


def _send(rule, target, channel, batch):
    if channel == 'bell' and rule.source == 'new_position':
        from apps.operations.reminder_coexistence import send_job_bell
        return send_job_bell(rule, target, batch,
            lambda remaining: _send_claimed(rule, target, channel, remaining))
    return _send_claimed(rule, target, channel, batch)


def _send_claimed(rule, target, channel, batch):
    """batch: list of (item, occurrence). Claims each, then sends what was claimed."""
    claimed = [(item, row) for item, occ in batch if (row := _claim(rule, item, occ, target, channel))]
    if not claimed:
        return 0
    groups = [[c] for c in claimed] if channel == "text" else [claimed]
    sent = 0
    for group in groups:
        title, body, link = compose(rule, [item for item, _ in group], target)
        try:
            DELIVER[channel](rule, target, title, body, link)
        except Exception as exc:
            ReminderSend.objects.filter(pk__in=[row.pk for _, row in group]).update(status="failed", error=str(exc)[:240])
        else:
            sent += 1
    return sent


def text_hours(now_local):
    return TEXT_HOURS[0] <= now_local.time() < TEXT_HOURS[1]


def run_rule(rule, now=None, setup=None, roles=None, cache=None):
    now = now or timezone.now()
    now_local = timezone.localtime(now, sources.EASTERN)
    source = sources.SOURCES.get(rule.source)
    if not source:
        return 0
    setup = setup or ReminderSetup.current()
    roles = roles if roles is not None else {r.key: r for r in ReminderRole.objects.all()}
    cache = cache if cache is not None else {}
    channels = rule.channels()
    chosen = {p.user_id: p.channels() for p in ReminderPreference.objects.filter(rule=rule)}
    texts_ok = text_hours(now_local)
    plan = {}
    for item in source.finder(now_local.date(), now):
        if not sources.matches(rule.conditions, item):
            continue
        if rule.mode == ReminderRule.Mode.CHANGED:
            occ = _changed(rule, item, ready=texts_ok or "text" not in channels)
        else:
            occ = occurrence(rule, item, now_local, setup.turned_on_at)
        if not occ:
            continue
        for target in targets_for(rule, item, roles, cache):
            mine = chosen.get(target.user.pk, channels) if target.user else channels
            for channel in mine:
                if (channel == "text" and not texts_ok) or (channel == "bell" and not target.user) \
                        or (channel == "email" and not target.email) or (channel == "text" and not target.phone):
                    continue
                plan.setdefault((target, channel), []).append((item, occ))
    return sum(_send(rule, target, channel, batch) for (target, channel), batch in plan.items())


def _changed(rule, item, ready):
    if not item.fingerprint:
        return None
    watch = ReminderWatch.objects.filter(rule=rule, ref=item.ref[:120]).first()
    if not watch:
        try:
            ReminderWatch.objects.create(rule=rule, ref=item.ref[:120], fingerprint=item.fingerprint)
        except IntegrityError:
            pass
        return None
    if watch.fingerprint == item.fingerprint or not ready:
        return None
    watch.fingerprint = item.fingerprint
    watch.save(update_fields=["fingerprint", "updated_at"])
    return "c" + item.fingerprint[:16]


def run_pass(now=None):
    setup = ReminderSetup.current()
    if not setup.is_on:
        return {"enabled": False, "sent": 0}
    roles = {r.key: r for r in ReminderRole.objects.all()}
    cache, sent = {}, 0
    for rule in ReminderRule.objects.filter(enabled=True, built_in="").exclude(source=""):
        try:
            sent += run_rule(rule, now=now, setup=setup, roles=roles, cache=cache)
        except Exception:
            log.exception("REMINDER_RULE_FAILED %s", rule.key)
    return {"enabled": True, "sent": sent}


def preview(rule, now=None, limit=6):
    """What this reminder would be about right now (ignores timing), for the editor."""
    now = now or timezone.now()
    source = sources.SOURCES.get(rule.source)
    if not source:
        return {"count": 0, "items": []}
    try:
        items = [i for i in source.finder(timezone.localtime(now, sources.EASTERN).date(), now)
                 if sources.matches(rule.conditions, i)]
    except Exception:
        log.exception("REMINDER_PREVIEW_FAILED %s", rule.source)
        return {"count": 0, "items": [], "error": True}
    return {"count": len(items), "items": items[:limit]}
