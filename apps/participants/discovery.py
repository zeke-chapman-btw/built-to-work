"""One validated, permission-filtered population for directory, saved views and charts."""
from decimal import Decimal
import json
from urllib.parse import urlencode
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, F, Count, Exists, OuterRef, Case, When, Value, IntegerField, Prefetch, TextField
from django.db.models.functions import Coalesce, Lower, Trim, NullIf
from django.db.models.fields.json import KeyTextTransform
from .models import Participant, ParticipantCareerProfile
from .workspace_models import ParticipantAcquisition, ParticipantWorkExperience
from .workspace_forms import DiscoveryForm
from .workspace_access import people_for, validate_filter_permissions
from .qualification_models import ParticipantQualification
from .participant_document_models import ParticipantDocument
from .verification_models import ParticipantVerificationRecord
from .verification_services import qualification_verification_summary, document_verification_summary
from apps.event_reports.activity import activity_sources
from .identity import member_ids, canonical_records, canonical_id, resolved_identity_map

SORTS = {"": ("last_name", "first_name", "pk"), "name_desc": ("-last_name", "-first_name", "-pk"),
    "newest": ("-created_at", "-pk"), "oldest": ("created_at", "pk"),
    "acquired": (F("acquisition__acquired_at").desc(nulls_last=True), "pk"),
    "experience": (F("career_profile__years_of_experience").desc(nulls_last=True), "pk")}
FACETS = ("channel", "state", "employment", "experience", "interest", "qualification",
          "document", "verification", "participation", "assessment", "completeness", "acquisition_month")
PROFILE_GROUPS = ("Contact", "Location", "Trade interests", "Reported skills", "Employment preference", "Experience")


def plain_criteria(cleaned):
    result = {}
    for key, value in cleaned.items():
        if value is None or value == "" or value == []:
            continue
        result[key] = json.dumps(value, separators=(",", ":")) if key == "segments" else str(value.pk) if hasattr(value, "pk") else value.isoformat() if hasattr(value, "isoformat") else str(value)
    return result


def query_string(criteria, **updates):
    result = dict(criteria)
    result.update(updates)
    return urlencode([(k, v) for k, v in result.items() if v is not None and v != ""])


from .intake_discovery import current_interest_expression, filter_travel


def with_coverage(qs):
    qs = qs.annotate(_has_work=Exists(canonical_records(ParticipantWorkExperience.objects.filter(archived_at__isnull=True)).filter(_canonical_person=OuterRef("pk"))))
    conditions = [
        ~Q(contact_email="", contact_phone=""),
        ~Q(city="", state=""),
        Q(career_profile__career_interests__gt=""),
        Q(career_profile__skills_interests__gt=""),
        Q(career_profile__employment_interest__gt=""),
        Q(career_profile__years_of_experience__isnull=False) | Q(_has_work=True),
    ]
    score = Value(0, output_field=IntegerField())
    for condition in conditions:
        score = score + Case(When(condition, then=Value(1)), default=Value(0), output_field=IntegerField())
    return qs.annotate(profile_groups=score)


def targets_with_review(kind, participant_qs):
    if kind == "qualification":
        qs = ParticipantQualification.objects.filter(participant_id__in=member_ids(participant_qs), is_active=True).select_related("catalog_entry")
    else:
        qs = ParticipantDocument.objects.filter(participant_id__in=member_ids(participant_qs), state="active").select_related("category")
    reviews = ParticipantVerificationRecord.objects.select_related("reviewer", "evidence_document", "document").order_by("-created_at", "-pk")
    return qs.prefetch_related(Prefetch("verification_records", queryset=reviews, to_attr="_workspace_verifications"))


def effective_people(kind, status, population):
    if status == "none":
        model = ParticipantQualification if kind == "qualification" else ParticipantDocument
        kw = {"is_active": True} if kind == "qualification" else {"state": "active"}
        return population.filter(~Exists(canonical_records(model.objects.filter(**kw)).filter(_canonical_person=OuterRef("pk"))))
    if kind == "document" and status == "present":
        return population.filter(Exists(canonical_records(ParticipantDocument.objects.filter(state="active")).filter(_canonical_person=OuterRef("pk"))))
    summarize = qualification_verification_summary if kind == "qualification" else document_verification_summary
    # Bounded prefetch batches retain the existing effective-verification definition.
    # This avoids a query per credential and excludes stale "verified" snapshots.
    mapping = resolved_identity_map()
    ids = {mapping.get(target.participant_id, target.participant_id) for target in targets_with_review(kind, population).iterator(chunk_size=250)
           if summarize(target)["status"] == status}
    return population.filter(pk__in=ids)


def assessment_queryset(kind, category=None):
    from apps.assessments.models import AssessmentSection
    source = activity_sources()
    if kind == "quiz":
        qs = source["finished_quizzes"]
        if category:
            qs = qs.filter(Exists(AssessmentSection.objects.filter(attempt_id=OuterRef("pk"), category=category)))
        return qs.annotate(stored_score=Count("sections__responses", filter=Q(sections__responses__is_correct=True))), "participant_id"
    if kind == "simulator":
        return source["captures"].annotate(stored_score=F("total_score")), "participant_id"
    return source["games"].annotate(stored_score=F("raw_score")), "experience_session__participant_id"


def apply_facet(qs, dimension, bucket, user):
    if not dimension:
        return qs
    if dimension not in FACETS or not bucket:
        raise ValidationError("Choose a valid chart dimension and segment.")
    if dimension == "channel":
        if bucket not in ("event", "online", "unknown"):
            raise ValidationError("Unknown acquisition segment.")
        return qs.filter(Q(acquisition__isnull=True) | Q(acquisition__channel="unknown")) if bucket == "unknown" else qs.filter(acquisition__channel=bucket)
    if dimension == "state":
        return qs.annotate(_workspace_state_key=Lower(Trim("state"))).filter(_workspace_state_key="" if bucket == "__not_recorded__" else bucket.casefold())
    if dimension == "employment":
        if bucket not in ("unknown", "seeking", "open", "not_seeking"):
            raise ValidationError("Unknown employment segment.")
        return qs.filter(Q(career_profile__isnull=True) | Q(career_profile__employment_interest="")) if bucket == "unknown" else qs.filter(career_profile__employment_interest=bucket)
    if dimension == "experience":
        if bucket == "unknown":
            return qs.filter(career_profile__years_of_experience__isnull=True)
        bands = {"under_1": (0, 1), "1_4": (1, 5), "5_9": (5, 10), "10_plus": (10, None)}
        if bucket not in bands:
            raise ValidationError("Unknown experience segment.")
        lower, upper = bands[bucket]
        qs = qs.filter(career_profile__years_of_experience__gte=lower)
        return qs.filter(career_profile__years_of_experience__lt=upper) if upper else qs
    if dimension == "interest":
        return qs.annotate(_interest=Lower(Trim(current_interest_expression()))).filter(_interest="" if bucket == "__not_recorded__" else bucket.casefold())
    if dimension == "qualification":
        if not user.has_perm("participants.view_participantqualification"):
            raise PermissionDenied
        if not bucket.isdigit():
            raise ValidationError("Invalid qualification segment.")
        return qs.filter(Exists(canonical_records(ParticipantQualification.objects.filter(is_active=True, catalog_entry_id=int(bucket))).filter(_canonical_person=OuterRef("pk"))))
    if dimension in ("document", "verification"):
        required = ("participants.view_participantdocument",) if dimension == "document" else ("participants.view_participantqualification",)
        if not user.has_perms(required):
            raise PermissionDenied
        allowed = ("none", "present", "pending", "verified", "expired", "rejected") if dimension == "document" else ("none", "pending", "unverified", "verified", "expired", "rejected")
        if bucket not in allowed:
            raise ValidationError("Invalid evidence segment.")
        return effective_people("document" if dimension == "document" else "qualification", bucket, qs)
    if dimension == "participation":
        if not user.has_perm("events.view_eventregistration"):
            raise PermissionDenied
        source = activity_sources()
        ids = canonical_records(source["registrations"]).values("_canonical_person")
        if bucket == "registered":
            return qs.filter(pk__in=ids)
        if bucket == "none":
            return qs.exclude(pk__in=ids)
        if bucket == "activity":
            return qs.filter(Q(pk__in=canonical_records(source["sessions"]).values("_canonical_person")) | Q(pk__in=canonical_records(source["attendance"]).values("_canonical_person")))
        raise ValidationError("Invalid participation segment.")
    if dimension == "assessment":
        from .workspace_access import ASSESSMENT_PERMISSIONS
        if bucket not in ASSESSMENT_PERMISSIONS or not user.has_perm(ASSESSMENT_PERMISSIONS[bucket]):
            raise PermissionDenied
        records, field = assessment_queryset(bucket)
        return qs.filter(pk__in=canonical_records(records, field).values("_canonical_person"))
    if dimension == "completeness":
        if bucket not in ("complete", "partial"):
            raise ValidationError("Invalid profile coverage segment.")
        return qs.filter(profile_groups=6) if bucket == "complete" else qs.filter(profile_groups__lt=6)
    if dimension == "acquisition_month":
        from datetime import datetime
        try:
            value = datetime.strptime(bucket, "%Y-%m")
        except ValueError:
            raise ValidationError("Invalid acquisition month.")
        return qs.filter(acquisition__acquired_at__year=value.year, acquisition__acquired_at__month=value.month)
    return qs


def discover(user, params, *, strict=False):
    validate_filter_permissions(user, params)
    form = DiscoveryForm(params, user=user)
    known = set(form.fields) | {"page", "view", "saved", "name", "csrfmiddlewaretoken", "operation"}
    if strict and (set(params) - known):
        raise ValidationError("Saved view contains unsupported criteria.")
    qs = with_coverage(people_for(user)).select_related("career_profile", "acquisition", "acquisition__event")
    if not form.is_valid():
        return form, qs.none(), {}
    d = form.cleaned_data
    if d.get("archived") == "only":
        qs = qs.filter(archived_at__isnull=False)
    elif d.get("archived") != "all":
        qs = qs.filter(archived_at__isnull=True)
    q = d.get("q")
    if q:
        from django.db.models.functions import Replace
        terms = q.split()
        lookup = Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(preferred_name__icontains=q) | Q(contact_email__icontains=q) | Q(contact_phone__icontains=q)
        if len(terms) > 1:
            lookup |= Q(first_name__icontains=terms[0], last_name__icontains=" ".join(terms[1:]))
            lookup |= Q(first_name__icontains=" ".join(terms[:-1]), last_name__icontains=terms[-1])
        digits = "".join(c for c in q if c.isdigit())
        if digits:
            phone = F("contact_phone")
            for char in (" ", "-", "(", ")", ".", "+"):
                phone = Replace(phone, Value(char), Value(""))
            qs = qs.annotate(_phone=phone)
            lookup |= Q(_phone__contains=digits)
        qs = qs.filter(lookup)
    qs = qs.annotate(_current_interest=current_interest_expression())
    for key, lookup in (("city", "city__icontains"), ("state", "state__icontains"),
                        ("trade_interest", "_current_interest__icontains"),
                        ("employment", "career_profile__employment_interest"),
                        ("availability", "career_profile__work_availability__icontains"),
                        ("preferred_location", "career_profile__preferred_locations__icontains"),
                        ("work_type", "career_profile__preferred_work_type")):
        if d.get(key):
            qs = qs.filter(**{lookup: d[key]})
    if d.get("experience_min") is not None:
        qs = qs.filter(career_profile__years_of_experience__gte=d["experience_min"])
    if d.get("experience_data"):
        qs = qs.filter(career_profile__years_of_experience__isnull=d["experience_data"] == "unknown")
    if d.get("travel"):
        qs = filter_travel(qs, d["travel"])
    if d.get("channel"):
        qs = apply_facet(qs, "channel", d["channel"], user)
    if d.get("acquired_from"):
        qs = qs.filter(acquisition__acquired_at__date__gte=d["acquired_from"])
    if d.get("acquired_to"):
        qs = qs.filter(acquisition__acquired_at__date__lte=d["acquired_to"])
    source = activity_sources()
    if d.get("event"):
        qs = qs.filter(pk__in=canonical_records(source["registrations"].filter(event=d["event"])).values("_canonical_person"))
    if d.get("participation"):
        qs = apply_facet(qs, "participation", d["participation"], user)
    qualification = canonical_records(ParticipantQualification.objects.filter(is_active=True)).filter(_canonical_person=OuterRef("pk"))
    if d.get("qualification"):
        qualification = qualification.filter(catalog_entry=d["qualification"])
    if d.get("qualification_kind"):
        qualification = qualification.filter(catalog_entry__kind=d["qualification_kind"])
    if d.get("skill"):
        qualification = qualification.filter(Q(catalog_entry__name__icontains=d["skill"]) | Q(custom_name__icontains=d["skill"]))
    if any(d.get(k) for k in ("qualification", "qualification_kind", "skill")):
        qs = qs.filter(Q(career_profile__skills_interests__icontains=d["skill"]) | Exists(qualification)) if d.get("skill") and not (d.get("qualification") or d.get("qualification_kind")) else qs.filter(Exists(qualification))
    if d.get("qualification_status"):
        qs = effective_people("qualification", d["qualification_status"], qs)
    if d.get("document_status"):
        qs = effective_people("document", d["document_status"], qs)
    if d.get("assessment") or d.get("assessment_category"):
        kind = d.get("assessment") or "quiz"
        records, field = assessment_queryset(kind, d.get("assessment_category"))
        if d.get("score_min") is not None:
            records = records.filter(stored_score__gte=d["score_min"])
        if d.get("score_max") is not None:
            records = records.filter(stored_score__lte=d["score_max"])
        qs = qs.filter(pk__in=canonical_records(records, field).values("_canonical_person"))
    if d.get("completeness"):
        qs = qs.filter(profile_groups=6) if d["completeness"] == "complete" else qs.filter(profile_groups__lt=6)
    try:
        segments = list(d.get("segments") or [])
        if d.get("facet"):
            segments = [pair for pair in segments if pair[0] != d["facet"]] + [[d["facet"], d.get("bucket") or ""]]
        for dimension, bucket in segments:
            qs = apply_facet(qs, dimension, bucket, user)
        d["segments"] = segments
        d.pop("facet", None)
        d.pop("bucket", None)
    except ValidationError as exc:
        form.add_error(None, exc)
        return form, qs.none(), {}
    return form, qs.order_by(*SORTS[d.get("sort", "")]).distinct(), plain_criteria(d)

def segment_criteria(criteria, dimension, bucket=None):
    result = dict(criteria)
    segments = json.loads(result.get("segments") or "[]")
    segments = [pair for pair in segments if pair[0] != dimension]
    if bucket is not None:
        segments.append([dimension, str(bucket)])
    result.pop("facet", None)
    result.pop("bucket", None)
    if segments:
        result["segments"] = json.dumps(segments, separators=(",", ":"))
    else:
        result.pop("segments", None)
    return result
