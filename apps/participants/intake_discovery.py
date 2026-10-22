"""Effective intake preferences alongside explicitly edited legacy profile fields."""
from django.db.models import Q, Exists, OuterRef, Case, When, Value, TextField
from django.db.models.functions import Coalesce, NullIf, Trim
from django.db.models.fields.json import KeyTextTransform
from .models import IntakeFieldState


def manual_field(destination, marker):
    return Exists(IntakeFieldState.objects.filter(
        participant_id=OuterRef("pk"), destination=destination, is_protected=True,
        **{"last_value__has_key": marker}))


def current_interest_expression():
    legacy = Coalesce("career_profile__career_interests", Value(""), output_field=TextField())
    return Case(
        When(manual_field("industry_interests", "manual_typed_value"),
             then=Coalesce(KeyTextTransform("industry_interests", "career_profile__intake_data"), Value(""))),
        When(manual_field("industry_interests", "manual_legacy_field"), then=legacy),
        default=Coalesce(NullIf(Trim(legacy), Value("")),
            KeyTextTransform("_industry_search", "career_profile__intake_data"), Value(""), output_field=TextField()),
        output_field=TextField())


def filter_travel(qs, value):
    qs = qs.annotate(_intake_travel_legacy=manual_field("travel_willingness", "manual_legacy_field"),
                     _intake_travel_typed=manual_field("travel_willingness", "manual_typed_value"))
    typed = Q(career_profile__intake_data__travel_willingness=value)
    legacy_unknown = Q(career_profile__willing_to_travel__isnull=True)
    missing = (~Q(career_profile__intake_data__has_key="travel_willingness") |
               Q(career_profile__intake_data__travel_willingness__isnull=True) |
               Q(career_profile__intake_data__travel_willingness=None))
    if value == "unknown":
        return qs.filter((Q(_intake_travel_typed=True) & missing) |
            (Q(_intake_travel_typed=False) & legacy_unknown & (Q(_intake_travel_legacy=True) | missing)) |
            Q(career_profile__isnull=True))
    condition = Q(_intake_travel_typed=True) & typed
    condition |= Q(_intake_travel_typed=False, _intake_travel_legacy=False) & legacy_unknown & typed
    if value in {"yes", "no"}:
        condition |= Q(_intake_travel_typed=False,
                       career_profile__willing_to_travel=value == "yes")
    return qs.filter(condition)


def effective_travel(career, state):
    if career is None:
        return None
    marker = state.last_value if state and isinstance(state.last_value, dict) else {}
    if "manual_typed_value" in marker:
        return career.intake_data.get("travel_willingness")
    if "manual_legacy_field" in marker or career.willing_to_travel is not None:
        return {True: "yes", False: "no", None: None}[career.willing_to_travel]
    return career.intake_data.get("travel_willingness")
