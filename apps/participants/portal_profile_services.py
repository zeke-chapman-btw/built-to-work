"""Participant-owned additions; immutable intake and staff evidence stay separate."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from apps.core.audit import record_audit
from .models import Participant, ParticipantQualification
from .workspace_models import ParticipantWorkExperience
from .portal_services import own_account
from .identity import identity_members

WORK_FIELDS = ('role', 'employer', 'location', 'started_on', 'ended_on', 'is_current', 'description')
QUAL_FIELDS = ('custom_name', 'reported_provider', 'experience_details', 'years_of_experience', 'issue_date', 'expiration_date')
KINDS = {'skills': 'skill', 'training': 'training', 'credentials': 'credential'}


def qualification_section(row):
    if row.reported_kind:
        return {'skill': 'skills', 'training': 'training', 'credential': 'credentials'}[row.reported_kind]
    return 'skills' if row.catalog_entry_id and row.catalog_entry.kind == 'skill' else 'credentials'


def can_edit_work(row, user, person):
    return bool(row.participant_id == person.pk and not row.archived_at
                and row.source in ('participant', 'intake')
                and row.updated_by_id in (None, user.pk)
                and (row.source == 'intake' or row.created_by_id == user.pk))


def can_edit_qualification(row, user, person):
    return bool(row.participant_id == person.pk and row.is_active and row.is_self_reported
                and row.source in ('participant', 'registration') and not row.catalog_entry_id
                and not row.notes.strip() and row.updated_by_id in (None, user.pk)
                and (row.source == 'registration' or row.created_by_id == user.pk)
                and not row.verification_records.exists() and not row.document_links.exists())


def _lock_person(user):
    account = own_account(user)
    return Participant.objects.select_for_update().get(pk=account.participant_id, kind='person', archived_at__isnull=True)


def _audit(user, row, action, before, fields):
    record_audit(actor=user, action='portal.' + action, instance=row, source='participant_portal',
                 old_data=before or {}, new_data={key: str(getattr(row, key)) for key in fields})


@transaction.atomic
def save_entry(user, section, values, object_id=None):
    person = _lock_person(user)
    if section not in ('work', *KINDS):
        raise PermissionDenied
    model = ParticipantWorkExperience if section == 'work' else ParticipantQualification
    fields = WORK_FIELDS if section == 'work' else tuple(key for key in QUAL_FIELDS if key in values)
    if set(values) - set(fields) - {'revision', 'request_key'}:
        raise PermissionDenied('Only participant-reported fields may be changed.')
    row = None
    if object_id is not None:
        row = model.objects.select_for_update(of=('self',)).get(pk=object_id, participant=person)
        allowed = can_edit_work(row, user, person) if section == 'work' else (
            qualification_section(row) == section and can_edit_qualification(row, user, person))
        if not allowed:
            raise PermissionDenied('This information is retained by BTW. Add a new entry or contact BTW for a correction.')
        if values.get('revision') != row.updated_at.isoformat():
            raise ValidationError('This entry changed while you were editing. Reload before saving.')
    else:
        key = values['request_key']
        lookup = {'source_entry_id': key, 'source': 'participant', 'participant': person} if section == 'work' else {'portal_request_key': key}
        # Qualification ordering traverses an optional catalog entry. Lock only
        # the owned record, avoiding PostgreSQL locks on nullable joined rows.
        prior = model.objects.select_for_update(of=('self',)).filter(**lookup).order_by('pk').first()
        if prior:
            if prior.participant_id != person.pk or prior.created_by_id != user.pk:
                raise PermissionDenied
            if (section != 'work' and qualification_section(prior) != section) or any(getattr(prior, f) != values[f] for f in fields):
                raise ValidationError('This Add request was already saved. Open its Edit action to make changes.')
            return prior
        row = model(participant=person, source='participant', created_by=user)
        if section == 'work':
            row.source_entry_id = key
        else:
            row.portal_request_key, row.reported_kind, row.is_self_reported = key, KINDS[section], True
    before = {key: str(getattr(row, key)) for key in fields} if object_id else None
    for key in fields:
        setattr(row, key, values[key])
    row.updated_by = user
    # Anonymous public intake legitimately has no historical creator. Keep that
    # unknown attribution instead of pretending the later editor created it.
    row.full_clean(exclude=('created_by',) if section == 'work' and row.source == 'intake' and row.created_by_id is None else None)
    row.save()
    _audit(user, row, 'profile_entry_updated' if object_id else 'profile_entry_added', before, fields)
    return row


@transaction.atomic
def archive_entry(user, section, object_id, revision):
    person = _lock_person(user)
    model = ParticipantWorkExperience if section == 'work' else ParticipantQualification
    row = model.objects.select_for_update(of=('self',)).get(pk=object_id, participant=person)
    allowed = can_edit_work(row, user, person) if section == 'work' else (
        section in KINDS and qualification_section(row) == section and can_edit_qualification(row, user, person))
    if not allowed or row.source != 'participant' or row.created_by_id != user.pk:
        raise PermissionDenied('Only your own unreviewed additions can be archived.')
    if revision != row.updated_at.isoformat():
        raise ValidationError('This entry changed. Reload before archiving.')
    if section == 'work':
        row.archived_at = timezone.now()
    else:
        row.is_active = False
    row.updated_by = user
    row.save()
    _audit(user, row, 'profile_entry_archived', {}, ('archived_at',) if section == 'work' else ('is_active',))
    return row


def profile_sections(user, person, qualifications):
    rows = {'skills': [], 'training': [], 'credentials': []}
    for row in qualifications:
        row.portal_editable = can_edit_qualification(row, user, person)
        row.portal_archivable = row.portal_editable and row.source == 'participant' and row.created_by_id == user.pk
        row.portal_support_allowed = row.participant_id == person.pk
        rows[qualification_section(row)].append(row)
    work = list(ParticipantWorkExperience.objects.filter(participant_id__in=identity_members(person.pk), archived_at__isnull=True).order_by('-is_current', '-started_on', '-pk'))
    for row in work:
        row.portal_editable = can_edit_work(row, user, person)
        row.portal_archivable = row.portal_editable and row.source == 'participant' and row.created_by_id == user.pk
    return {'work_entries': work, 'skill_entries': rows['skills'], 'training_entries': rows['training'], 'credential_entries': rows['credentials']}
