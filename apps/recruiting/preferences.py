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
    return {'employment': classified('employment_classification', 'employment_status'),
        'availability': classified('availability_classification', 'work_availability'),
        'travel': yes_no(career.willing_to_travel if career else None),
        'relocation': yes_no(row.willing_to_relocate if row else None),
        'interest': career.get_employment_interest_display() if career and career.employment_interest else 'Not recorded',
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
    return {'revision': row.revision if row else 0,
        'fingerprint': profile_fingerprint(participant),
        'employment_classification': row.employment_classification_id if row else None,
        'availability_classification': row.availability_classification_id if row else None,
        'willing_to_relocate': choice(row.willing_to_relocate if row else None),
        'willing_to_travel': choice(career.willing_to_travel if career else None),
        'employment_interest': career.employment_interest if career else ''}
