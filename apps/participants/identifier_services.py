"""Audited classification using existing identity and intake ownership safeguards."""
import hashlib
import json
from datetime import UTC
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.core.audit import record_audit
from apps.core.models import AuditLog
from .identifier_definitions import DESTINATION, PrimaryIdentifier, MAPPING_POLICY, mapped_identifier
from .models import Participant, IntakeFieldState
from .intake_projection import _digest as digest
from apps.recruiting.access import serialize_identity_write


def revision(person, state=None):
    if state is None:
        state = IntakeFieldState.objects.filter(participant=person, destination=DESTINATION).first()
    data = [person.primary_identifier, person.updated_at.isoformat(),
            state.updated_at.isoformat() if state else None]
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()


@transaction.atomic
@serialize_identity_write
def save_identifier(*, actor, participant_id, value, expected_revision, reason):
    # Re-read account status/realm and permissions, including session auth hash.
    from apps.crm.access import fresh_staff
    from .workspace_access import require_people
    from .workspace_services import lock_person, require_reason
    actor = fresh_staff(actor)
    require_people(actor, 'participants.change_participant')
    reason = require_reason(reason, default="Updated by staff")
    if value not in {'', *PrimaryIdentifier.values}:
        raise ValidationError('Choose one of the five supported identifiers.')
    person = lock_person(participant_id)
    state = IntakeFieldState.objects.select_for_update().filter(participant=person, destination=DESTINATION).first()
    if expected_revision != revision(person, state):
        raise ValidationError('This identifier changed while you were editing. Reload before saving.')
    before = {'value': person.primary_identifier, 'origin': (state.provenance if state else {})}
    origin = {'origin': 'staff' if value else 'staff_cleared', 'actor_id': str(actor.pk),
              'mapping_policy': MAPPING_POLICY}
    person.primary_identifier = value
    person.save(update_fields=['primary_identifier', 'updated_at'])
    IntakeFieldState.objects.update_or_create(participant=person, destination=DESTINATION,
        defaults={'source_submission': None, 'last_value': value, 'value_hash': digest(value),
                  'is_protected': True, 'provenance': origin})
    record_audit(actor=actor, action='participants.identifier.changed', instance=person,
        source='staff_identifier', old_data=before,
        new_data={'value': value, 'origin': origin}, reason=reason)
    return person


def already_projected(submission):
    from django.contrib.contenttypes.models import ContentType
    return AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(submission),
        object_id=str(submission.pk), action='intake.profile_projected').exists()


def project_identifier(person, submission, *, actor=None):
    """Called inside the existing locked projection, only on its first accepted application.

    Unmapped answers advance the watermark without clearing a classification. This
    prevents an older delayed answer from replacing a more recent processed intake.
    Previously projected historical submissions are never backfilled by replay.
    """
    if already_projected(submission):
        return 'historical_or_replay'
    if submission.status != 'submitted':
        return 'not_accepted'
    from .intake_lifecycle import verify_published
    verify_published(submission.form_version, allow_retired=True)
    state = IntakeFieldState.objects.select_for_update().filter(participant=person, destination=DESTINATION).first()
    if state and (state.is_protected or state.value_hash != digest(person.primary_identifier)):
        if not state.is_protected:
            state.is_protected = True
            state.save(update_fields=['is_protected', 'updated_at'])
        return 'protected'
    if not state and person.primary_identifier:
        return 'protected'
    stamp = [submission.submitted_at.astimezone(UTC).isoformat(), submission.pk]
    prior = (state.provenance or {}).get('watermark') if state else None
    if prior and tuple(prior) >= tuple(stamp):
        return 'older_or_replay'
    contract = submission.form_version.snapshot
    from .intake_contracts import canonical_answers
    answers = canonical_answers(contract, submission.answers)
    # Honor the frozen validation result as well as the frozen destination map.
    definition = next((q for q in contract['questions'] if q.get('destination') == 'current_situation'), None)
    answered = definition and submission.intake_metadata.get('answer_states', {}).get(definition['key']) == 'answered'
    value = mapped_identifier(contract.get('schema'), answers.get('current_situation')) if answered else ''
    before = person.primary_identifier
    if value:
        person.primary_identifier = value
        person.save(update_fields=['primary_identifier', 'updated_at'])
    provenance = {'origin': 'intake' if value else ((state.provenance or {}).get('origin', 'unclassified') if state else 'unclassified'),
        'mapping_policy': MAPPING_POLICY, 'watermark': stamp,
        'last_processed_schema': contract.get('schema')}
    if value:
        provenance.update(value_source_id=submission.pk, value_schema=contract.get('schema'))
    elif state:
        for key in ('value_source_id', 'value_schema'):
            if key in state.provenance: provenance[key] = state.provenance[key]
    IntakeFieldState.objects.update_or_create(participant=person, destination=DESTINATION,
        defaults={'source_submission': submission, 'last_value': person.primary_identifier,
                  'value_hash': digest(person.primary_identifier), 'is_protected': False, 'provenance': provenance})
    if value:
        record_audit(actor=actor, action='participants.identifier.projected', instance=person,
            source='versioned_intake', old_data={'value': before},
            new_data={'value': value, 'origin': provenance}, reason='Eligible first intake projection using its frozen contract.')
    return 'mapped' if value else 'unmapped'


def preserve_reviewed_links(links):
    """Do not silently hide a differing classification or staff-cleared ownership."""
    for link in links:
        source, target = link.source_participant, link.candidate_participant
        states = {s.participant_id: s for s in IntakeFieldState.objects.select_for_update().filter(
            participant_id__in=[source.pk, target.pk], destination=DESTINATION)}
        source_state, target_state = states.get(source.pk), states.get(target.pk)
        if (source.primary_identifier and source.primary_identifier != target.primary_identifier
            or source_state and source_state.is_protected and not (target_state and target_state.is_protected
                and source.primary_identifier == target.primary_identifier)):
            raise ValidationError('Participant identifiers or staff protection differ. Review the classifications on both profiles before linking these identities. No identity or history was discarded.')

def protect_identifier_reversal(member_ids):
    """A reviewed reversal must not orphan an intake-derived classification."""
    from .models import RegistrationSubmission
    # Match the established sorted-people-before-source-state lock order.
    list(Participant.objects.select_for_update().filter(pk__in=member_ids).order_by('pk').values_list('pk', flat=True))
    states = list(IntakeFieldState.objects.select_for_update(of=('self',)).filter(
        participant_id__in=member_ids, destination=DESTINATION, is_protected=False).exclude(participant__primary_identifier=''))
    source_ids = {state.provenance.get('value_source_id') for state in states if state.provenance.get('value_source_id')}
    owners = dict(RegistrationSubmission.objects.filter(pk__in=source_ids).values_list('pk', 'participant_id'))
    if any(owners.get(state.provenance.get('value_source_id'), state.participant_id) != state.participant_id for state in states):
        raise ValidationError('An intake-owned participant identifier depends on a linked identity. Review the identifier on the canonical profile with an audit reason before reversing this link. No classification or history was discarded.')
