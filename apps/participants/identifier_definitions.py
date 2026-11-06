"""Stable participant classifications. Labels may evolve; stored meanings are versioned."""
from django.db import models

class PrimaryIdentifier(models.TextChoices):
    STUDENT = 'student', 'Student'
    EMPLOYEE = 'employee', 'Employee'
    TEACHER = 'teacher_instructor', 'Teacher / Instructor'
    JOB_SEEKER = 'job_seeker', 'Job Seeker'
    OTHER = 'other', 'Other'

DESTINATION = 'primary_identifier'
IDENTIFIER_SCHEMA = 'btw-intake-identifiers-v2'
MAPPING_POLICY = 'participant-identifier-v1'
ORIGIN_LABELS = {'staff': 'Staff correction (protected)', 'staff_cleared': 'Staff cleared (protected)',
                 'intake': 'Versioned intake', 'unclassified': 'Unclassified'}
LEGACY_SCHEMA = 'btw-intake-11b-v1'
SITUATION_IDENTIFIERS = {
    'high_school': PrimaryIdentifier.STUDENT, 'college': PrimaryIdentifier.STUDENT,
    'trade_school': PrimaryIdentifier.STUDENT, 'employed_trade': PrimaryIdentifier.EMPLOYEE,
    'employed_other': PrimaryIdentifier.EMPLOYEE,
}
NEW_SITUATION_IDENTIFIERS = {'teacher_instructor': PrimaryIdentifier.TEACHER,
    'job_seeker': PrimaryIdentifier.JOB_SEEKER, 'other': PrimaryIdentifier.OTHER}

def mapped_identifier(schema, situation):
    if schema not in {LEGACY_SCHEMA, IDENTIFIER_SCHEMA}:
        return ''
    if schema == IDENTIFIER_SCHEMA and situation in NEW_SITUATION_IDENTIFIERS:
        return str(NEW_SITUATION_IDENTIFIERS[situation])
    return str(SITUATION_IDENTIFIERS.get(situation, ''))
