"""Read-only historical classification inventory; deliberately has no apply option."""
from collections import Counter, defaultdict
from .identifier_definitions import mapped_identifier
from .models import Participant, RegistrationSubmission, IntakeFieldState
from .identity import fresh_identity_context, canonical_id
from .intake_contracts import canonical_answers
from .intake_lifecycle import verify_published
from django.core.exceptions import ValidationError
from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, CharField, Value, BooleanField
from django.db.models.functions import Cast
from apps.core.models import AuditLog


def historical_dry_run():
    candidates, ambiguous, invalid = defaultdict(set), set(), 0
    unprojected = 0
    content_type = ContentType.objects.filter(app_label='participants', model='registrationsubmission').first()
    accepted = Exists(AuditLog.objects.filter(content_type=content_type, action='intake.profile_projected',
        object_id=Cast(OuterRef('pk'), output_field=CharField()))) if content_type else Value(False, output_field=BooleanField())
    with fresh_identity_context():
        for submission in RegistrationSubmission.objects.filter(status='submitted', participant__isnull=False,
                intake_operation_id__isnull=False).select_related('form_version').annotate(_accepted_projection=accepted).iterator(chunk_size=250):
            person_id = canonical_id(submission.participant_id)
            if not submission._accepted_projection:
                unprojected += 1
                ambiguous.add(person_id)
                continue
            try:
                contract = submission.form_version.snapshot
                verify_published(submission.form_version, allow_retired=True)
                definition = next(q for q in contract['questions'] if q.get('destination') == 'current_situation')
                answered = submission.intake_metadata.get('answer_states', {}).get(definition['key']) == 'answered'
                value = mapped_identifier(contract['schema'], canonical_answers(contract, submission.answers).get('current_situation')) if answered else ''
            except (ValidationError, KeyError, TypeError, StopIteration):
                invalid += 1
                ambiguous.add(person_id)
                continue
            if value: candidates[person_id].add(value)
            else: ambiguous.add(person_id)
        protected = set(IntakeFieldState.objects.filter(destination='primary_identifier', is_protected=True).values_list('participant_id', flat=True))
        counts = Counter(); conflicts = 0; eligible = 0
        for person in Participant.objects.filter(pk__in=candidates, kind='person', archived_at__isnull=True).iterator(chunk_size=250):
            values = candidates[person.pk]
            if len(values) != 1 or person.pk in protected or person.primary_identifier and person.primary_identifier not in values:
                conflicts += 1
            elif person.pk in ambiguous:
                continue
            elif not person.primary_identifier:
                eligible += 1
                counts[next(iter(values))] += 1
    return {'mode': 'dry_run_only', 'writes': 0, 'reliable_unset_candidates': eligible,
        'candidate_counts': dict(counts), 'ambiguous_people': len(ambiguous),
        'conflicts_or_staff_protected': conflicts, 'invalid_contract_submissions': invalid,
        'unprojected_or_review_pending_submissions': unprojected,
        'note': 'Historical evidence only; no classifications, assignments, decisions or audit records written.'}
