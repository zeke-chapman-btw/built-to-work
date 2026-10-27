"""Participant-only intelligence, linked to the same validated discovery pipeline."""
from django.db.models import Count, Avg, Max, Value, TextField
from django.db.models.functions import Lower, Trim, Coalesce, TruncMonth
from django.urls import reverse
from .discovery import discover, query_string, segment_criteria, assessment_queryset, effective_people, current_interest_expression, apply_facet
from .identity import member_ids, canonical_records
from .workspace_access import can_analytics, ASSESSMENT_PERMISSIONS
from apps.event_reports.activity import activity_sources

METRIC_DEFINITIONS = [
    ("Population", "Distinct canonical person records in the selected directory cohort. The reserved system-test identity is excluded. Existing active reviewed same-person links are grouped through their established candidate identity without changing any stored UUID or relationship. Ambiguous/cyclic links are not resolved automatically."),
    ("Original acquisition", "A recorded original Event or Online source, with an explicit unknown/unverified bucket for missing or unknown origins. Later registrations do not change it. Dated acquisition trends use the original acquired_at in UTC; unknown dates are not fabricated."),
    ("Charts and drill-downs", "Each distribution retains search, advanced filters and chart selections from other dimensions. Selecting a segment changes that dimension. Bar counts and destination lists use the same discovery rules. Multi-valued evidence/assessment buckets can overlap and must not be summed into population."),
    ("Registrations", "Active person-event registration records linked to the filtered people, across events. Counts of registrations and unique registered people are separate. Registration alone is not attendance."),
    ("Recorded participation", "Unique filtered people with valid official experience sessions or nonvoid retained legacy attendance whose participant, event and registration agree. No scan or physical attendance is invented."),
    ("Assessments", "Accepted completed nonvoid official quiz attempts and coherent matched/resolved official simulator captures reuse event-reporting source querysets. Completed official game sessions reuse stored scores and versions. Test, demo, void, unresolved and inconsistent evidence is excluded from accepted summaries."),
    ("Scores", "Quiz values count stored correct responses per accepted attempt; no historical question answer is rescored. Simulator values retain TOTAL SCORE percent; game values are raw points. Missing values are unavailable, never zero. Different assessment types are never averaged together; game average/highest are unavailable when the cohort includes more than one game/version/configuration method."),
    ("Evidence", "Qualification and document effective status uses the existing verification service, including stale qualification snapshots, replaced/expired evidence and current expiry. An uploaded or self-reported claim is not independently verified. No active record is not proof that a person lacks a credential."),
    ("Profile coverage", "Presence of six recorded groups: contact, location, trade interests, reported skills, employment preference and experience. This is a data-entry coverage measure, not a talent score, employability judgement, credential verification or matching rank."),
    ("Privacy and scope", "Underlying permissions control every filter, chart, list and profile. Saved views are private and recheck current permission. Internal/customer notes are never analytics dimensions or public responses. Tracker data, financial reporting, demographic targeting and full CRM are outside this build."),
]


def _chart(title, dimension, rows, detail):
    largest = max((row["count"] for row in rows), default=0)
    for row in rows:
        row["bar"] = round(row["count"] / largest * 100) if largest else 0
    return dict(title=title, dimension=dimension, rows=rows, detail=detail)


def build_intelligence(user, population, criteria, *, current_group=False):
    if not can_analytics(user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    charts = []
    directory = reverse("participants:staff-directory")
    def segment(dimension, bucket, label):
        if current_group:
            # Reuse the existing facet predicate inside the already authorized
            # selected population. These composition counts are not drill-downs.
            return dict(label=label, count=apply_facet(population, dimension, bucket, user).count(), url=None, bucket=bucket)
        candidate = segment_criteria(criteria, dimension, bucket)
        form, people, _ = discover(user, candidate, strict=True)
        return dict(label=label, count=people.count() if form.is_valid() else 0,
                    url=directory + "?" + query_string(candidate), bucket=bucket)
    def base(dimension):
        if current_group:
            return population.order_by()
        return discover(user, segment_criteria(criteria, dimension), strict=True)[1]
    charts.append(_chart("Original acquisition", "channel", [segment("channel", b, label) for b, label in
        [("event", "Event"), ("online", "Online"), ("unknown", "Unknown / unverified")]], "Original source remains separate from later activity."))
    geographic = list(base("state").annotate(key=Lower(Trim("state"))).values("key").annotate(n=Count("pk", distinct=True)).order_by("-n", "key")[:10])
    charts.append(_chart("Participant geography", "state", [segment("state", row["key"] or "__not_recorded__", row["key"].upper() if row["key"] else "Unknown / not recorded") for row in geographic],
        "Top ten recorded state/region groups. Text is preserved; no geocoding or inferred radius."))
    charts.append(_chart("Employment interest", "employment", [segment("employment", b, label) for b, label in
        [("seeking", "Seeking work"), ("open", "Open to opportunities"), ("not_seeking", "Not seeking work"), ("unknown", "Not recorded")]], "Explicit current profile preference; no inference from assessment scores."))
    charts.append(_chart("Reported overall experience", "experience", [segment("experience", b, label) for b, label in
        [("under_1", "Under 1 year"), ("1_4", "1–4.9 years"), ("5_9", "5–9.9 years"), ("10_plus", "10+ years"), ("unknown", "Unknown")]], "Reported overall years, not a sum of overlapping jobs or a guess from age."))
    interests = list(base("interest").annotate(key=Lower(Trim(current_interest_expression()))).values("key").annotate(n=Count("pk", distinct=True)).order_by("-n", "key")[:12])
    rows = [segment("interest", row["key"] or "__not_recorded__", row["key"] or "Not recorded") for row in interests if len(row["key"]) <= 240][:8]
    charts.append(_chart("Reported trade and career interests", "interest", rows,
        "Top eight recorded response groups, as entered. A response may contain multiple interests. Long responses remain searchable in the directory."))
    months = list(base("acquisition_month").filter(acquisition__acquired_at__isnull=False).annotate(month=TruncMonth("acquisition__acquired_at")).values("month").annotate(n=Count("pk", distinct=True)).order_by("-month")[:24])
    charts.append(_chart("Original acquisition over time", "acquisition_month", [segment("acquisition_month", row["month"].strftime("%Y-%m"), row["month"].strftime("%b %Y")) for row in reversed(months)],
        "Most recent 24 recorded acquisition months (UTC). Missing dates are excluded, not inferred from record creation."))
    charts.append(_chart("Profile data coverage", "completeness", [segment("completeness", b, label) for b, label in
        [("complete", "Six groups recorded"), ("partial", "Some groups unknown")]], "Six defined data-entry groups; this is not a qualification or suitability score."))
    if user.has_perm("participants.view_participantqualification"):
        from .qualification_models import ParticipantQualification
        qualified = canonical_records(ParticipantQualification.objects.filter(participant_id__in=member_ids(base("qualification")), is_active=True, catalog_entry__isnull=False))
        rows = list(qualified.values("catalog_entry_id", "catalog_entry__name").annotate(n=Count("_canonical_person", distinct=True)).order_by("-n", "catalog_entry__name")[:10])
        charts.append(_chart("Recorded skills and qualifications", "qualification", [segment("qualification", str(row["catalog_entry_id"]), row["catalog_entry__name"]) for row in rows],
            "Top ten catalog records. Claims/evidence/verification remain distinct; people can appear in several groups."))
        charts.append(_chart("Qualification verification", "verification", [segment("verification", b, label) for b, label in
            [("verified", "Currently verified"), ("pending", "Pending review"), ("unverified", "Unverified claims"), ("expired", "Expired"), ("rejected", "Rejected"), ("none", "No active records")]],
            "Effective status from the existing verification engine. A person may have different records in several states."))
    if user.has_perm("participants.view_participantdocument"):
        charts.append(_chart("Document status", "document", [segment("document", b, label) for b, label in
            [("present", "Active documents recorded"), ("verified", "Verified document"), ("pending", "Pending review"), ("expired", "Expired document"), ("none", "No active documents recorded")]],
            "No required-credential policy is inferred. Multiple document states may apply to one person."))
    if current_group:
        from .intake_discovery import filter_travel
        charts.append(_chart("Willingness to travel", "travel", [
            dict(label=label, count=filter_travel(population, value).count(), url=None, bucket=value)
            for value, label in [("yes", "Yes"), ("maybe", "Maybe"), ("no", "No"), ("unknown", "Unknown")]
        ], "Current effective reported preference; unknown is not No."))
    sources = activity_sources()
    members = member_ids(population)
    metrics = [dict(label="Participants", value=population.count(), detail="Unique canonical people in this filtered cohort")]
    if user.has_perm("events.view_eventregistration"):
        regs = sources["registrations"].filter(participant_id__in=members)
        metrics += [dict(label="Active registrations", value=regs.count(), detail="Person-event records across all events"),
            dict(label="Registered people", value=canonical_records(regs).values("_canonical_person").distinct().count(), detail="Unique people; registration does not prove attendance")]
        session_ids = canonical_records(sources["sessions"].filter(participant_id__in=members)).values_list("_canonical_person", flat=True)
        attendance_ids = canonical_records(sources["attendance"].filter(participant_id__in=members)).values_list("_canonical_person", flat=True)
        metrics.append(dict(label="Recorded participants", value=len(set(session_ids) | set(attendance_ids)), detail="Valid official activity or retained legacy attendance"))
        charts.append(_chart("Event participation", "participation", [segment("participation", b, label) for b, label in
            [("registered", "Active event registration"), ("activity", "Recorded official activity"), ("none", "No active event registration")]], "Registrations and recorded participation are different facts; these groups overlap."))
    assessment_rows = []
    for kind, label in [("quiz", "Quiz"), ("simulator", "Simulator"), ("game", "Game")]:
        if not user.has_perm(ASSESSMENT_PERMISSIONS[kind]):
            continue
        records, field = assessment_queryset(kind)
        records = records.filter(**{field + "__in": members})
        score = records.aggregate(average=Avg("stored_score"), highest=Max("stored_score"))
        mixed_game_methods = kind == "game" and len(list(records.order_by().values("game_id", "game_version", "configuration_version").distinct()[:2])) > 1
        if mixed_game_methods:
            score = {"average": None, "highest": None}
        people_count = canonical_records(records, field).values("_canonical_person").distinct().count()
        assessment_rows.append(dict(label=label, count=records.count(), people=people_count, average=score["average"],
            highest=score["highest"], unit=("raw points; multiple game versions — scores kept separate" if mixed_game_methods else {"quiz": "correct answers", "simulator": "percent", "game": "raw points"}[kind]),
            url=directory + "?" + query_string(segment_criteria(criteria, "assessment", kind))))
    if assessment_rows:
        charts.append(_chart("People with accepted assessments", "assessment",
            [segment("assessment", kind, label) for kind, label in [("quiz", "Quiz"), ("simulator", "Simulator"), ("game", "Game")] if user.has_perm(ASSESSMENT_PERMISSIONS[kind])],
            "Unique people, not attempts. A person can have more than one assessment type."))
    # Score distributions deliberately count people with at least one qualifying
    # stored result. Their links use the same discovery pipeline and score units.
    for kind, label, bands in [
        ("quiz", "Quiz correct-answer bands", [("0–9", "0", "9"), ("10–19", "10", "19"), ("20+", "20", "")]),
        ("simulator", "Simulator score bands", [("0–24.999999%", "0", "24.999999"), ("25–49.999999%", "25", "49.999999"), ("50–74.999999%", "50", "74.999999"), ("75–100%", "75", "100")]),
        ("game", "Game raw-point bands", [("0–99", "0", "99"), ("100–499", "100", "499"), ("500+", "500", "")])]:
        if not user.has_perm(ASSESSMENT_PERMISSIONS[kind]):
            continue
        rows = []
        for title, lower, upper in bands:
            candidate = dict(segment_criteria(criteria, "assessment"), assessment=kind, score_min=lower, score_max=upper)
            form, people, _ = discover(user, candidate, strict=True)
            if current_group:
                people = people.filter(pk__in=population.values("pk"))
            rows.append(dict(label=title, count=people.count() if form.is_valid() else 0,
                             url=None if current_group else directory + "?" + query_string(candidate)))
        charts.append(_chart(label, "score_" + kind, rows, "People with at least one accepted result in the band. Repeated results can place a person in several bands; score filters are replaced by the selected band."))
    return dict(metrics=metrics, charts=charts, assessment_rows=assessment_rows, metric_definitions=METRIC_DEFINITIONS)
