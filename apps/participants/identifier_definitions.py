"""Stable participant classifications. Labels may evolve; stored meanings are versioned."""
from django.db import models

class PrimaryIdentifier(models.TextChoices):
    STUDENT = 'student', 'Student'
    WORKFORCE = 'workforce', 'Workforce'  # working or looking for work (was Employee and Job Seeker)
    TEACHER = 'teacher_instructor', 'Teacher / Instructor'
    OTHER = 'other', 'Other'

# Old stored values from before Workforce replaced Employee and Job Seeker.
RETIRED_IDENTIFIERS = {'employee': 'workforce', 'job_seeker': 'workforce'}
# What participants see on the sign-up question for this type.
WORKFORCE_PUBLIC_LABEL = 'Working or looking for work'

DESTINATION = 'primary_identifier'
IDENTIFIER_SCHEMA = 'btw-intake-identifiers-v2'
MAPPING_POLICY = 'participant-identifier-v1'
ORIGIN_LABELS = {'staff': 'Staff correction (protected)', 'staff_cleared': 'Staff cleared (protected)',
                 'intake': 'Versioned intake', 'unclassified': 'Unclassified'}
LEGACY_SCHEMA = 'btw-intake-11b-v1'
SITUATION_IDENTIFIERS = {
    'high_school': PrimaryIdentifier.STUDENT, 'college': PrimaryIdentifier.STUDENT,
    'trade_school': PrimaryIdentifier.STUDENT, 'employed_trade': PrimaryIdentifier.WORKFORCE,
    'employed_other': PrimaryIdentifier.WORKFORCE,
}
NEW_SITUATION_IDENTIFIERS = {'teacher_instructor': PrimaryIdentifier.TEACHER,
    'job_seeker': PrimaryIdentifier.WORKFORCE, 'other': PrimaryIdentifier.OTHER}

def mapped_identifier(schema, situation):
    if schema not in {LEGACY_SCHEMA, IDENTIFIER_SCHEMA}:
        return ''
    if schema == IDENTIFIER_SCHEMA and situation in NEW_SITUATION_IDENTIFIERS:
        return str(NEW_SITUATION_IDENTIFIERS[situation])
    return str(SITUATION_IDENTIFIERS.get(situation, ''))
