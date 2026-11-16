"""Mailchimp: keep one audience in step with participants who said yes to marketing email.

The app is the source of truth. People with marketing email On are added or updated
(name, tags); anyone else is archived in Mailchimp so they are never emailed and do
not count toward the bill. Unsubscribes and bounces come back through a webhook.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import socket
from urllib import error, parse, request

from django.urls import reverse
from django.utils import timezone

from . import store
from .models import MarketingConsent

KEY = "mailchimp"


class MailchimpError(Exception):
    pass


def settings_row():
    from .models import ConnectionSetting
    return ConnectionSetting.objects.filter(key=KEY).first()


def credentials():
    """``(api_key, audience_id)`` or blanks."""
    row = settings_row()
    if not row or not row.secret_encrypted:
        return "", ""
    return store.secret(row), row.values.get("audience_id", "")


def connected():
    key, audience = credentials()
    return bool(key and audience)


def _datacenter(api_key):
    match = re.search(r"-([a-z]+[0-9]+)$", api_key or "")
    if not match:
        raise MailchimpError("That doesn't look like a Mailchimp API key (it should end in something like -us21).")
    return match.group(1)


def call(method, path, *, api_key=None, body=None, query=None):
    api_key = api_key or credentials()[0]
    if not api_key:
        raise MailchimpError("Mailchimp isn't connected.")
    url = f"https://{_datacenter(api_key)}.api.mailchimp.com/3.0{path}"
    if query:
        url += "?" + parse.urlencode(query)
    token = base64.b64encode(f"btw:{api_key}".encode()).decode("ascii")
    req = request.Request(url, method=method, headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
                          data=json.dumps(body).encode("utf-8") if body is not None else None)
    try:
        with request.urlopen(req, timeout=15) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}").get("detail", "")
        except ValueError:
            detail = ""
        if exc.code == 404 and method == "DELETE":
            return {}
        if exc.code in (401, 403):
            raise MailchimpError("Mailchimp refused the API key.") from None
        raise MailchimpError(f"Mailchimp error {exc.code}: {detail}"[:200]) from None
    except (error.URLError, socket.timeout, TimeoutError):
        raise MailchimpError("Mailchimp could not be reached.") from None


def audiences(api_key):
    data = call("GET", "/lists", api_key=api_key, query={"fields": "lists.id,lists.name,lists.stats.member_count", "count": 50})
    return [(item["id"], item["name"], item.get("stats", {}).get("member_count", 0)) for item in data.get("lists", [])]


def subscriber_hash(email):
    return hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()


def tags_for(participant):
    """Plain tags staff can pick in Mailchimp: trade, state, seeking work, travel, events."""
    tags = []
    if participant.state:
        tags.append(f"State: {participant.state.strip()}")
    profile = getattr(participant, "career_profile", None)
    if profile:
        for trade in re.split(r"[,;\n]+", profile.career_interests or ""):
            if trade.strip():
                tags.append(f"Trade: {trade.strip()}")
        if profile.employment_interest == "seeking":
            tags.append("Seeking work")
        elif profile.employment_interest == "open":
            tags.append("Open to opportunities")
        if profile.willing_to_travel:
            tags.append("Will travel")
    from apps.events.models import EventRegistration
    for name in (EventRegistration.objects.filter(participant=participant).exclude(status="void")
                 .values_list("event__name", flat=True).distinct()[:30]):
        tags.append(f"Event: {name}")
    return [tag[:100] for tag in tags]


def push(row):
    """Bring one person's Mailchimp record in line with their consent."""
    _key, audience = credentials()
    participant = row.participant
    email = (participant.contact_email or "").strip().lower()
    if row.synced_email and row.synced_email != email:
        call("DELETE", f"/lists/{audience}/members/{subscriber_hash(row.synced_email)}")
        row.synced_email = ""
    wanted = row.status == MarketingConsent.Status.ON and email and not participant.archived_at
    if wanted:
        member = subscriber_hash(email)
        call("PUT", f"/lists/{audience}/members/{member}", body={
            "email_address": email, "status_if_new": "subscribed", "status": "subscribed",
            "merge_fields": {"FNAME": participant.preferred_name or participant.first_name, "LNAME": participant.last_name}})
        call("POST", f"/lists/{audience}/members/{member}/tags",
             body={"tags": [{"name": tag, "status": "active"} for tag in tags_for(participant)]})
        row.synced_email = email
    elif row.synced_email:
        # Archive: never emailed again and not billed. The app keeps the real status.
        call("DELETE", f"/lists/{audience}/members/{subscriber_hash(row.synced_email)}")
        row.synced_email = ""
    row.needs_sync, row.synced_at, row.sync_error = False, timezone.now(), ""
    row.save(update_fields=["needs_sync", "synced_email", "synced_at", "sync_error"])


def sync_pending(limit=100):
    if not connected():
        return 0
    done = 0
    for row in (MarketingConsent.objects.filter(needs_sync=True).select_related("participant", "participant__career_profile")
                .order_by("changed_at")[:limit]):
        try:
            push(row)
            done += 1
        except MailchimpError as exc:
            row.sync_error = str(exc)[:240]
            row.save(update_fields=["sync_error"])
            if "API key" in str(exc) or "reached" in str(exc):
                break  # Whole connection is down; try again next pass.
    if done:
        from .models import ConnectionSetting
        ConnectionSetting.objects.filter(key=KEY).update(last_test_ok=True, last_test_at=timezone.now(),
                                                          last_test_detail=f"Synced {done} contact(s)")
    return done


def mark_all_for_sync():
    return MarketingConsent.objects.filter(status=MarketingConsent.Status.ON).update(needs_sync=True)


def webhook_url(request, row):
    return request.build_absolute_uri(reverse("messaging:mailchimp-webhook", args=[row.values.get("webhook_secret", "")]))


def connect(*, api_key, audience_id, actor, request):
    """Save the key and audience, and ask Mailchimp to tell the app about unsubscribes and bounces."""
    names = {item[0]: item[1] for item in audiences(api_key)}
    if audience_id not in names:
        raise MailchimpError("Choose one of your Mailchimp audiences.")
    row = settings_row()
    secret_part = (row.values.get("webhook_secret") if row else "") or secrets.token_urlsafe(24)
    values = {"audience_id": audience_id, "audience_name": names[audience_id], "webhook_secret": secret_part}
    row = store.save(key=KEY, values=values, actor=actor, new_secret=api_key)
    url = webhook_url(request, row)
    try:
        existing = call("GET", f"/lists/{audience_id}/webhooks", api_key=api_key).get("webhooks", [])
        if not any(hook.get("url") == url for hook in existing):
            call("POST", f"/lists/{audience_id}/webhooks", api_key=api_key, body={
                "url": url, "events": {"unsubscribe": True, "cleaned": True, "upemail": True, "subscribe": False,
                                       "profile": False, "campaign": False},
                "sources": {"user": True, "admin": True, "api": False}})
        row.values = dict(row.values, webhook_ok=True)
    except MailchimpError as exc:
        row.values = dict(row.values, webhook_ok=False, webhook_error=str(exc)[:200])
    row.save(update_fields=["values"])
    mark_all_for_sync()
    return row


def handle_webhook(data):
    """Apply an unsubscribe, bounce or email change sent by Mailchimp."""
    from . import marketing
    kind = data.get("type", "")
    email = (data.get("data[email]") or data.get("data[old_email]") or "").strip().lower()
    if not email:
        return 0
    rows = MarketingConsent.objects.filter(synced_email__iexact=email).select_related("participant")
    changed = 0
    for row in rows:
        if kind == "unsubscribe":
            marketing.set_status(participant=row.participant, status=MarketingConsent.Status.UNSUBSCRIBED,
                                 source=MarketingConsent.Source.MAILCHIMP, note="Unsubscribed in Mailchimp.")
            changed += 1
        elif kind == "cleaned":
            marketing.set_status(participant=row.participant, status=MarketingConsent.Status.BOUNCED,
                                 source=MarketingConsent.Source.MAILCHIMP, note="Mailchimp removed this address (bounced).")
            changed += 1
        elif kind == "upemail":
            # The person changed their address inside Mailchimp. Keep the app's address as the
            # record; resync so Mailchimp matches the app again.
            row.needs_sync = True
            row.save(update_fields=["needs_sync"])
            changed += 1
    return changed
