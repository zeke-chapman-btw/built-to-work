"""Explicit compatible draft preparation; never publishes or changes assignments."""
from copy import deepcopy
from django.core.exceptions import ValidationError
from django.db import transaction
from .identifier_definitions import IDENTIFIER_SCHEMA, LEGACY_SCHEMA, PrimaryIdentifier
from .intake_contracts import validate_contract


def identifier_contract(snapshot):
    validate_contract(snapshot)
    if snapshot['schema'] != LEGACY_SCHEMA:
        raise ValidationError('This version already supports all five identifiers.')
    contract = deepcopy(snapshot)
    contract['schema'] = IDENTIFIER_SCHEMA
    question = next(q for q in contract['questions'] if q['destination'] == 'current_situation')
    question['options'].extend([{'value': 'teacher_instructor', 'label': str(PrimaryIdentifier.TEACHER.label)},
                                {'value': 'job_seeker', 'label': str(PrimaryIdentifier.JOB_SEEKER.label)}])
    return validate_contract(contract)


@transaction.atomic
def prepare_identifier_draft(*, actor, form_id, expected_revision):
    from .intake_lifecycle import require_admin, save_draft, revision
    from .models import RegistrationForm
    require_admin(actor)
    form = RegistrationForm.objects.select_for_update().get(pk=form_id, is_smart_intake=True, is_active=True)
    latest = form.versions.order_by('-version').first()
    if not latest or revision(latest) != expected_revision:
        raise ValidationError('This form changed. Reload before preparing its draft.')
    return save_draft(actor=actor, form_id=form.pk, expected_revision=expected_revision,
                      contract=identifier_contract(latest.snapshot))
