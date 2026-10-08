"""Trusted Participant matching and permanent profile updates."""
from dataclasses import dataclass
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .models import Participant, ParticipantCareerProfile
from .normalization import normalize_email, normalize_phone


@dataclass(frozen=True)
class ParticipantResolution:
    status: str
    participant: Participant | None = None
    candidate_ids: tuple[int, ...] = ()


def participant_candidates(*, email="", phone=""):
    """Contact values are hints, never unique person identifiers."""
    email, phone = normalize_email(email), normalize_phone(phone)
    query = Q()
    if email:
        query |= Q(contact_email__iexact=email)
    if phone:
        query |= Q(contact_phone=phone)
    base = Participant.objects.filter(kind=Participant.Kind.PERSON, archived_at__isnull=True)
    return base.filter(query).distinct() if query else base.none()


def match_participant(*, identity_uuid=None, email="", phone=""):
    if identity_uuid is not None:
        try:
            identity_uuid = uuid.UUID(str(identity_uuid))
        except (TypeError, ValueError, AttributeError):
            return ParticipantResolution("invalid_identifier")
        participant = Participant.objects.filter(
            identity_uuid=identity_uuid, kind=Participant.Kind.PERSON, archived_at__isnull=True
        ).first()
        return ParticipantResolution("matched", participant) if participant else ParticipantResolution("not_found")
    ids = tuple(participant_candidates(email=email, phone=phone).values_list("pk", flat=True))
    if len(ids) > 1:
        return ParticipantResolution("ambiguous", candidate_ids=ids)
    if ids:
        return ParticipantResolution("matched", Participant.objects.get(pk=ids[0]))
    return ParticipantResolution("no_match")


@transaction.atomic
def find_or_create_participant(*, first_name, last_name, email="", phone="", identity_uuid=None):
    """Return a reviewable outcome; never merge or rewrite an existing person."""
    match = match_participant(identity_uuid=identity_uuid, email=email, phone=phone)
    if match.status != "no_match":
        return match
    participant = Participant(
        first_name=(first_name or "").strip(), last_name=(last_name or "").strip(),
        contact_email=normalize_email(email), contact_phone=normalize_phone(phone),
    )
    participant.full_clean()
    participant.save()
    return ParticipantResolution("created", participant)


PROFILE_FIELDS = {"first_name", "last_name", "preferred_name", "contact_email", "contact_phone", "city", "state", "postal_code"}
CAREER_FIELDS = {"employment_status", "career_interests", "work_availability", "willing_to_travel", "education_training", "skills_interests"}


@transaction.atomic
def update_participant_profile(*, participant, updates):
    """Update the permanent person record; Event registrations/results remain unchanged."""
    unknown = set(updates) - PROFILE_FIELDS - CAREER_FIELDS
    if unknown:
        raise ValidationError("Unsupported profile fields: " + ", ".join(sorted(unknown)))
    person = Participant.objects.select_for_update().get(pk=participant.pk, kind=Participant.Kind.PERSON, archived_at__isnull=True)
    for field in PROFILE_FIELDS & updates.keys():
        value = updates[field]
        if field == "contact_email":
            value = normalize_email(value)
        elif field == "contact_phone":
            value = normalize_phone(value)
        setattr(person, field, value)
    person.full_clean()
    person.save()
    if CAREER_FIELDS & updates.keys():
        career, _ = ParticipantCareerProfile.objects.get_or_create(participant=person)
        for field in CAREER_FIELDS & updates.keys():
            setattr(career, field, updates[field])
        career.full_clean()
        career.save()
    return person
