"""Extra columns and the small Insights panel for the participants list.

Every read here is bounded (one query per column or chart, never per row) and
respects the same permissions the profile uses for that kind of record.
"""
from django.db.models import Count, Q

INSIGHT_LIMIT = 5


def _qualification_names(rows):
    from .qualification_models import ParticipantQualification
    names = {}
    records = (ParticipantQualification.objects
               .filter(participant_id__in=[p.pk for p in rows], is_active=True)
               .filter(Q(catalog_entry__kind__in=("certification", "license")) | Q(reported_kind__in=("certification", "license")))
               .select_related("catalog_entry").order_by("participant_id", "-created_at"))
    for record in records:
        label = record.catalog_entry.name if record.catalog_entry_id else record.custom_name
        if label:
            bucket = names.setdefault(record.participant_id, [])
            if label not in bucket:
                bucket.append(label)
    return names


def _last_interactions(rows):
    from apps.crm.models import Interaction
    latest = {}
    records = (Interaction.objects.filter(participant_id__in=[p.pk for p in rows], audience="btw", archived_at__isnull=True)
               .select_related("created_by", "responsible_staff").order_by("participant_id", "-occurred_at", "-pk"))
    for record in records:
        latest.setdefault(record.participant_id, record)
    return latest


def initials(person):
    first = (getattr(person, "preferred_name", "") or getattr(person, "first_name", "") or "").strip()
    last = (getattr(person, "last_name", "") or "").strip()
    letters = (first[:1] + last[:1]).upper()
    return letters or "#"


def decorate_rows(user, rows):
    from .workspace_access import can_notes
    show_certs = user.has_perm("participants.view_participantqualification")
    show_contact = can_notes(user)
    certs = _qualification_names(rows) if show_certs and rows else {}
    contact = _last_interactions(rows) if show_contact and rows else {}
    for person in rows:
        person.list_initials = initials(person)
        person.list_certs = certs.get(person.pk, [])
        person.list_last = contact.get(person.pk)
    return {"list_show_certs": show_certs, "list_show_contact": show_contact}


def _bars(rows, total):
    for row in rows:
        row["percent"] = round(100 * row["count"] / total) if total else 0
    return rows


def insights(user, population, groups):
    """Four small charts for the people currently matching the filters."""
    base = population.order_by()
    total = base.count()
    if not total:
        return []
    option_urls = {g["key"]: {o["value"]: o["url"] for o in g["options"]} for g in groups}
    charts = []
    interest = [dict(label=r["_current_interest"].strip()[:40], count=r["n"]) for r in
                base.exclude(_current_interest__isnull=True).exclude(_current_interest="")
                    .values("_current_interest").annotate(n=Count("pk", distinct=True)).order_by("-n", "_current_interest")[:INSIGHT_LIMIT]]
    if interest:
        charts.append(dict(title="Trade interest", rows=_bars(interest, total)))
    labels = {"seeking": "Yes", "open": "Maybe", "not_seeking": "No"}
    seeking = {r["career_profile__employment_interest"]: r["n"] for r in
               base.values("career_profile__employment_interest").annotate(n=Count("pk", distinct=True))}
    rows = [dict(label=label, count=seeking.get(key, 0), url=option_urls.get("employment", {}).get(key, ""))
            for key, label in labels.items() if seeking.get(key)]
    if rows:
        charts.append(dict(title="Seeking work", rows=_bars(rows, total)))
    if user.has_perm("participants.view_participantqualification"):
        from .qualification_models import ParticipantQualification
        records = ParticipantQualification.objects.filter(participant__in=base.values("pk"), is_active=True, catalog_entry__kind__in=("certification", "license"))
        rows = [dict(label=r["catalog_entry__name"], count=r["n"]) for r in
                records.values("catalog_entry__name").annotate(n=Count("participant", distinct=True)).order_by("-n", "catalog_entry__name")[:INSIGHT_LIMIT - 1]]
        holders = records.values("participant").distinct().count()
        if total - holders:
            rows.append(dict(label="None recorded", count=total - holders))
        charts.append(dict(title="Certifications", rows=_bars(rows, total)))
    if user.has_perm("participants.view_registrationsubmission") or user.is_superuser:
        channels = {r["acquisition__channel"]: r["n"] for r in base.values("acquisition__channel").annotate(n=Count("pk", distinct=True))}
        names = {"event": "At an event", "online": "Online form"}
        rows = [dict(label=label, count=channels.get(key, 0), url=option_urls.get("channel", {}).get(key, ""))
                for key, label in names.items() if channels.get(key)]
        other = total - sum(r["count"] for r in rows)
        if other > 0:
            rows.append(dict(label="Not recorded", count=other))
        charts.append(dict(title="How they found us", rows=_bars(rows, total)))
    return charts
