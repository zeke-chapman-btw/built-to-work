"""One current-value contract; legacy text remains evidence, never a backfill.

Classification IDs are authoritative after the first explicit preference save.
Missing classifications remain unknown, including after an explicit clear.
Original submissions, intake ownership and historical text are never replayed.
"""
from django.db import transaction
from apps.participants.models import ParticipantCareerProfile
from .models import RecruitingProfile


def current_preferences(participant):
    row = RecruitingProfile.objects.select_related('employment_classification',
        'availability_classification').filter(participant=participant).first()
    career = ParticipantCareerProfile.objects.filter(participant=participant).first()
    confirmed = bool(row and row.revision)
    def classified(field, legacy):
        if field == 'employment_classification' and row and row.current_employment_status is not None:
            return {'value':dict(RecruitingProfile.CURRENT_EMPLOYMENT).get(row.current_employment_status,'Unknown'), 'source':'Current employment status', 'id':None}
        choice = getattr(row, field, None)
        if choice:
            return {'value': choice.label, 'source': 'Current classification' +
                (' · retired choice retained' if not choice.is_active else ''), 'id': str(choice.pk)}
        old = getattr(career, legacy, '') if career else ''
        if old and not confirmed:
            return {'value': old, 'source': 'Previously recorded profile · not confirmed', 'id': None}
        return {'value': 'Not recorded', 'source': 'Current preference', 'id': None}
    def yes_no(value):
        return 'Not recorded' if value is None else 'Yes' if value else 'No'
    from apps.participants.intake_discovery import effective_travel
    from apps.participants.models import IntakeFieldState
    travel_state = IntakeFieldState.objects.filter(participant=participant, destination='travel_willingness').first()
    return {'employment': classified('employment_classification', 'employment_status'),
        'availability': classified('availability_classification', 'work_availability'),
        'travel': {'yes': 'Yes', 'maybe': 'Maybe', 'no': 'No'}.get(effective_travel(career, travel_state), 'Not recorded'),
        'relocation': ({'yes':'Yes','maybe':'Maybe','no':'No'}.get(row.relocation_preference,'Not recorded') if row and row.relocation_preference is not None else yes_no(row.willing_to_relocate if row else None)),
        'interest': {'seeking':'Yes','open':'Maybe','not_seeking':'No'}.get(career.employment_interest if career else '', 'Not recorded'),
        'historical_employment': career.employment_status if career else '',
        'historical_availability': career.work_availability if career else ''}


@transaction.atomic
def initial_preferences(participant):
    from .foundation import profile_fingerprint
    from .access import person
    # All existing profile writers lock this parent; fields and fingerprint must
    # describe the same snapshot, including when legacy profile editors save.
    participant = person(participant.pk, lock=True)
    row = RecruitingProfile.objects.filter(participant=participant).first()
    career = ParticipantCareerProfile.objects.filter(participant=participant).first()
    def choice(value):
        return '' if value is None else 'yes' if value else 'no'
    from apps.participants.intake_discovery import effective_travel
    from apps.participants.models import IntakeFieldState
    from apps.participants.workspace_services import profile_revision
    travel_state = IntakeFieldState.objects.filter(participant=participant, destination='travel_willingness').first()
    return {'revision': row.revision if row else 0,
        'fingerprint': profile_fingerprint(participant),
        'profile_revision': profile_revision(participant),
        'employment_classification': row.employment_classification_id if row else None,
        'availability_classification': row.availability_classification_id if row else None,
        'current_employment_status': (row.current_employment_status if row and row.current_employment_status is not None else '__retained__' if (row and row.employment_classification_id) or (career and career.employment_status and not (row and row.revision)) else ''),
        'retained_employment_label': current_preferences(participant)['employment']['value'],
        'willing_to_relocate': row.relocation_preference if row and row.relocation_preference is not None else choice(row.willing_to_relocate if row else None),
        'willing_to_travel': effective_travel(career, travel_state) or '',
        'employment_interest': career.employment_interest if career else ''}
