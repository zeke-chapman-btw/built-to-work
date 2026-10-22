"""Explicit Build 11B dual-contact policy. Existing matching helpers are unchanged."""
from dataclasses import dataclass
from datetime import date
import hashlib
import unicodedata

from django.core.exceptions import ValidationError
from django.db import transaction
from django.core.validators import validate_email

from .models import Participant, IntakeContactLock
from .normalization import normalize_email, normalize_phone
from .identity import canonical_id, resolved_identity_map
from .intake_contracts import age_on


@dataclass(frozen=True)
class IntakeResolution:
    participant: Participant
    created: bool
    review_required: bool
    candidates: tuple[int, ...] = ()
    reasons: tuple[str, ...] = ()


def name_key(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def _lock_contacts(email, phone):
    # Separate sorted keys serialize overlapping contact pairs without making
    # contact columns unique (families may legitimately share them).
    keys = sorted(hashlib.sha256((kind + ":" + value).encode()).hexdigest()
                  for kind, value in [("email", email), ("phone", phone)])
    for key in keys:
        IntakeContactLock.objects.get_or_create(key=key)
        IntakeContactLock.objects.select_for_update(of=("self",)).get(key=key)


@transaction.atomic
def resolve_intake_identity(*, answers, occurred_on, provisional_hint=None, new_identity=None):
    email, phone = normalize_email(answers.get("email")), normalize_phone(answers.get("phone"))
    if not email or not phone:
        raise ValidationError("Both email and phone are required for every intake.")
    validate_email(email)
    if not 7 <= len(phone) <= 15:
        raise ValidationError("Enter a valid phone number.")
    try:
        dob = date.fromisoformat(answers["dob"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationError("A valid date of birth is required.") from exc
    if dob > occurred_on or age_on(dob, occurred_on) < 14:
        raise ValidationError("Participants must be at least 14 at the time of intake.")
    _lock_contacts(email, phone)
    people = Participant.objects.filter(kind="person")
    email_ids = set(people.filter(contact_email__iexact=email).values_list("pk", flat=True))
    phone_ids = set(people.filter(contact_phone=phone).values_list("pk", flat=True))
    candidates = tuple(sorted(email_ids | phone_ids))
    reasons = []
    if candidates:
        if len(email_ids) == 1 and email_ids == phone_ids:
            person = people.select_for_update(of=("self",)).get(pk=next(iter(email_ids)))
            if person.archived_at:
                reasons.append("archived_identity")
            mapping = resolved_identity_map()
            if person.source_identity_links.filter(is_active=True).exists() and person.pk not in mapping:
                reasons.append("identity_link_conflict")
            if person.pk in mapping:
                person = people.select_for_update(of=("self",)).get(pk=mapping[person.pk])
                candidates = tuple(sorted(set(candidates) | {person.pk}))
                if person.archived_at:
                    reasons.append("archived_identity")
            if (name_key(person.first_name) != name_key(answers.get("first_name"))
                    or name_key(person.last_name) != name_key(answers.get("last_name"))):
                reasons.append("name_conflict")
            if person.date_of_birth and person.date_of_birth != dob:
                reasons.append("dob_conflict")
            if not reasons:
                if provisional_hint and canonical_id(provisional_hint.pk) != person.pk:
                    reasons.append("stable_identity_contact_conflict")
                else:
                    return IntakeResolution(person, False, False)
        elif len(email_ids) > 1 or len(phone_ids) > 1:
            reasons.append("multiple_records")
        elif email_ids and phone_ids:
            reasons.append("contacts_different_people")
        else:
            reasons.append("one_contact_only")
    # A review case gets its own retained source identity, rather than blocking
    # intake or attaching evidence to a potentially unrelated existing person.
    if provisional_hint is not None:
        if provisional_hint.kind != "person":
            raise ValidationError("The retained source identity is not a person.")
        return IntakeResolution(provisional_hint, False, True, candidates,
            tuple(reasons or ["stable_identity_contact_conflict"]))
    person = Participant(
        **({"identity_uuid": new_identity} if new_identity else {}),
        first_name=(answers.get("first_name") or "").strip(),
        last_name=(answers.get("last_name") or "").strip(),
        preferred_name=(answers.get("preferred_name") or "").strip(),
        contact_email=email, contact_phone=phone, date_of_birth=dob,
        age_classification="minor" if age_on(dob, occurred_on) < 18 else "adult")
    person.full_clean()
    person.save()
    return IntakeResolution(person, True, bool(reasons), candidates, tuple(reasons))
