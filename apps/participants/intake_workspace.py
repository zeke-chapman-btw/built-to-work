"""Read projections for the same canonical participant workspace; no answer replay."""
from datetime import date
from django.utils import timezone
from .models import IntakeFieldState, ParticipantHandsOnExperience, RegistrationSubmission
from .intake_defaults import default_contract
from .intake_contracts import visible, age_on
from .intake_admin_guards import full_dob_allowed
from .intake_discovery import effective_travel
from apps.events.models import ParticipantMatchReview


def _label(definition, value):
    if value in (None, "", []):
        return "Not answered"
    options = {v["value"]: v["label"] for v in definition.get("options", [])}
    if isinstance(value, list):
        return ", ".join(options.get(v, str(v)) for v in value)
    return options.get(value, str(value))


def workspace_intake_context(user, person, members, career):
    states = {s.destination: s for s in IntakeFieldState.objects.filter(participant=person).select_related(
        "source_submission__form_version")}
    defaults = {q["destination"]: q for q in default_contract()["questions"]}
    data = dict(career.intake_data) if career else {}
    travel = effective_travel(career, states.get("travel_willingness"))
    answers = dict(data, dob=person.date_of_birth, current_situation=person.life_stage, travel_willingness=travel)
    rows = []
    excluded = {"dob", "consent", "first_name", "last_name", "preferred_name", "email", "phone",
                "city", "state", "postal_code", "street_address", "experiences"}
    for destination, value in data.items():
        if destination.startswith("_") or destination in excluded:
            continue
        state = states.get(destination)
        source = state.source_submission if state else None
        definition = next((q for q in source.form_version.snapshot["questions"]
                           if q["destination"] == destination), None) if source else defaults.get(destination)
        if not definition:
            continue
        marker = state.last_value if state and isinstance(state.last_value, dict) else {}
        if "manual_legacy_field" in marker and destination != "travel_willingness":
            continue
        scoped_answers = dict(answers)
        if source:
            scoped_answers.update({q["key"]: answers.get(q["destination"])
                                   for q in source.form_version.snapshot["questions"]})
        if not visible(definition.get("rule"), scoped_answers, occurred_on=timezone.localdate()):
            continue
        if destination == "travel_willingness":
            value = travel
            if marker or (career and career.willing_to_travel is not None):
                source = None
        if destination == "travel_range" and career and career.willing_to_travel is False:
            continue
        rows.append({"label": definition["label"], "value": _label(definition, value),
                     "protected": bool(state and state.is_protected),
                     "source": source.intake_operation_id if source else None,
                     "reported_at": source.submitted_at if source else None})
    experiences = ParticipantHandsOnExperience.objects.filter(participant_id__in=members).select_related(
        "source_submission").order_by("-source_submission__submitted_at", "pk")[:100]
    entries = []
    for entry in experiences:
        definitions = {q["key"]: q for q in entry.definition_snapshot["children"]}
        entries.append({"area": _label(definitions["area"], entry.area),
            "source": _label(definitions["source"], entry.experience_source),
            "paid": entry.get_paid_display(), "duration": _label(definitions["duration"], entry.duration),
            "skills": _label(definitions["skills"], entry.practiced_skills),
            "proficiency": _label(definitions["proficiency"], entry.proficiency),
            "description": entry.description, "reported_at": entry.source_submission.submitted_at,
            "operation": entry.source_submission.intake_operation_id})
    pending = ParticipantMatchReview.objects.filter(source_submission__isnull=False, status__in=["open", "later"]).filter(
        source_participant_id__in=members)
    candidate_reviews = ParticipantMatchReview.objects.filter(source_submission__isnull=False, status__in=["open", "later"],
                                                             candidate_participant_id=person.pk)
    interest_state = states.get("industry_interests")
    interest_marker = interest_state.last_value if interest_state and isinstance(interest_state.last_value, dict) else {}
    interest = ((career.career_interests if "manual_legacy_field" in interest_marker else
                 career.career_interests or data.get("_industry_search", "")) if career else "")
    if "manual_typed_value" in interest_marker:
        interest = _label(defaults["industry_interests"], data.get("industry_interests"))
    result = {"current_interests": interest,
              "current_travel": {"yes": "Yes", "no": "No", "maybe": "Maybe"}.get(travel, "Unknown"),
              "intake_profile_rows": rows, "hands_on_entries": entries,
              "intake_history": RegistrationSubmission.objects.filter(participant_id__in=members,
                  intake_operation_id__isnull=False).select_related("form_version__form", "event").order_by("-submitted_at")[:50],
              "intake_pending_count": pending.count() + candidate_reviews.exclude(pk__in=pending.values("pk")).count(),
              "can_review_intake": user.has_perms(("participants.view_participant", "events.view_participantmatchreview")),
              "minor_status": ("Minor" if age_on(person.date_of_birth, timezone.localdate()) < 18 else "Adult")
                              if person.date_of_birth else person.age_classification or "Not recorded"}
    if full_dob_allowed(user):
        result["authorized_dob"] = person.date_of_birth
    return result
