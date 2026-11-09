"""Current travel answers using existing field ownership and recorded preferences.

Reads never repair or replay historical records. An explicit preference save wins
over older intake; a later manual field edit can supersede that recorded choice.
"""
from django.core.exceptions import ValidationError
from django.db.models import Case, When, Value, CharField, OuterRef, Subquery
from django.db.models.functions import Cast
from apps.core.models import AuditLog

NOT_LOADED = object()
ANCHOR = 'travel_preference_audit'


def preference_events():
    return AuditLog.objects.filter(action='recruiting.preferences.updated',
        content_type__app_label='recruiting', content_type__model='recruitingprofile',
        new_data__has_key='willing_to_travel').order_by('-timestamp', '-pk')


def recorded_preference(participant_id):
    from apps.recruiting.models import RecruitingProfile
    profiles = RecruitingProfile.objects.filter(participant_id=participant_id).annotate(
        object_key=Cast('pk', CharField())).values('object_key')
    event = preference_events().filter(object_id__in=profiles).values('pk', 'timestamp', 'new_data').first()
    if not event:
        return None
    value = event['new_data']['willing_to_travel']
    answer = 'maybe' if value == 'maybe' else 'yes' if value is True or value == 1 else 'no' if value is False or value == 0 else None
    return (str(event['pk']), event['timestamp'], answer)


def observed_preference_anchor(participant_id):
    event = recorded_preference(participant_id)
    return event[0] if event else 'none'


def with_travel_preference(queryset):
    if '_travel_preference_id' in queryset.query.annotations:
        return queryset
    events = preference_events().filter(object_id=Cast(OuterRef('recruiting_profile__pk'), CharField()))
    answers = events.annotate(answer=Case(
        When(new_data__willing_to_travel=True, then=Value('yes')),
        When(new_data__willing_to_travel=False, then=Value('no')),
        When(new_data__willing_to_travel=1, then=Value('yes')),
        When(new_data__willing_to_travel=0, then=Value('no')),
        When(new_data__willing_to_travel='maybe', then=Value('maybe')),
        default=Value('unknown'), output_field=CharField()))
    return queryset.annotate(
        _travel_preference_id=Subquery(events.annotate(key=Cast('pk', CharField())).values('key')[:1]),
        _travel_preference_at=Subquery(events.values('timestamp')[:1]),
        _travel_preference_value=Subquery(answers.values('answer')[:1]))


def resolve_travel(career, state, preference=NOT_LOADED):
    marker = state.last_value if state and isinstance(state.last_value, dict) else {}
    manual = bool(state and state.is_protected and ('manual_typed_value' in marker or 'manual_legacy_field' in marker))
    if preference is NOT_LOADED:
        preference = recorded_preference(career.participant_id if career else state.participant_id) if career or state else None
    if preference:
        key, at, value = preference
        anchor = (state.provenance or {}).get(ANCHOR) if state else None
        newer_manual = manual and (anchor == key if anchor is not None else state.updated_at > at)
        if not newer_manual:
            return None if value == 'unknown' else value
    if career is None:
        return None
    if 'manual_typed_value' in marker:
        return career.intake_data.get('travel_willingness')
    if 'manual_legacy_field' in marker or career.willing_to_travel is not None:
        return {True: 'yes', False: 'no', None: None}[career.willing_to_travel]
    return career.intake_data.get('travel_willingness')


def set_current_travel(career, value):
    """Caller holds the person/career locks and records the attributable audit."""
    if value not in (None, 'yes', 'maybe', 'no'):
        raise ValidationError('Choose Yes, Maybe, No or Unknown for travel.')
    from .intake_model_guards import intake_profile_write
    from .intake_projection import protect_manual_career
    career.intake_data = {**career.intake_data, 'travel_willingness': value}
    career.willing_to_travel = {'yes': True, 'no': False}.get(value)
    career.full_clean()
    # Write only willingness; a boolean mirror must not take ownership of range.
    with intake_profile_write():
        career.save(update_fields=['intake_data', 'willing_to_travel', 'updated_at'])
    protect_manual_career(career, {'travel_willingness'})
