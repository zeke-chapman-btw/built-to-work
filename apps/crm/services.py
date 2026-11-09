"""Explicit, transaction-safe CRM operations. No sends, rules, or automatic Tasks."""
import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.audit import record_audit
from apps.customers.access import require_manager
from apps.customers.models import Campaign, CustomerContact, CustomerOrganization, WorkforceNeed
from apps.participants.identity import canonical_id, identity_members
from apps.participants.models import Participant
from apps.recruiting.access import person, policy_transaction, reason
from apps.recruiting.employer import customer_grant, may_respond
from apps.recruiting.models import CandidateOpportunity, WorkforceRequest, WorkforceRequestPosition
from apps.recruiting.selection import opportunity_for

from . import access
from .models import ContactRelationship, Interaction, InteractionRevision, InteractionTaskLink

EDITABLE = ("activity_type", "direction", "summary", "notes", "outcome", "occurred_at", "next_step", "follow_up_on", "responsible_staff")
CONTACT_FIELDS = ("role", "is_primary", "email_preference", "sms_preference", "phone_preference", "preference_notes")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def snapshot(row):
    # Record authoritative identifiers rather than changing labels or account secrets.
    values = {field.attname: getattr(row, field.attname) for field in row._meta.fields}
    return json.loads(json.dumps(values, default=str))


def record_revision(row, actor, action, why):
    revision = InteractionRevision.objects.create(interaction=row, number=row.revision,
        actor=actor, action=action, reason=reason(why), snapshot=snapshot(row))
    record_audit(actor=actor, action="crm.interaction." + action, instance=row,
        source="relationship_management", old_data=None,
        new_data={"revision": row.revision, "revision_id": str(revision.pk), "audience": row.audience}, reason=why)
    return revision


def checked_fields(data, *, employer=False, current_responsible_id=None):
    # A caller cannot promote a manual observation into provider-confirmed delivery.
    if set(data) - set(EDITABLE):
        raise ValidationError("Only interaction details may be submitted; ownership and provenance are derived by BTW.")
    values = {key: value for key, value in data.items() if key in EDITABLE}
    for key in ("summary", "notes", "outcome", "next_step"):
        if key in values:
            values[key] = str(values[key] or "").strip()
    staff = values.get("responsible_staff")
    if employer and staff:
        raise PermissionDenied("Employer interactions cannot assign internal BTW staff.")
    if staff:
        from apps.operations.task_access import eligible_assignees
        if str(getattr(staff, "pk", staff)) != str(current_responsible_id) and not eligible_assignees().filter(pk=getattr(staff, "pk", staff)).exists():
            raise ValidationError({"responsible_staff": "Choose an active linked BTW staff member."})
        from apps.staffing.models import StaffProfile
        values["responsible_staff"] = StaffProfile.objects.get(pk=getattr(staff, "pk", staff))
    return values


def replay(row, actor, fingerprint):
    if row.created_by_id != actor.pk or row.payload_fingerprint != fingerprint:
        raise ValidationError("This submission identifier was used for different interaction details.")
    return row


def internal_associations(actor, *, participant_id=None, organization_id=None, contact_id=None,
                          engagement_id=None, candidate_id=None, need_id=None, campaign_id=None,
                          workforce_request_id=None):
    participant = person(participant_id, lock=True) if participant_id else None
    candidate = None
    if candidate_id:
        from apps.recruiting.workflow import candidate_for
        candidate = candidate_for(actor, candidate_id, write=True, lock=True)
        if not participant or candidate.participant_id != participant.pk:
            raise PermissionDenied("Choose a recruiting record for this Participant.")
        if engagement_id and str(engagement_id) != str(candidate.opportunity_id):
            raise ValidationError("The candidate and recruiting engagement must agree.")
        engagement_id = candidate.opportunity_id
    engagement = opportunity_for(actor, engagement_id, write=True, lock=True) if engagement_id else None
    if engagement:
        if need_id and str(need_id) != str(engagement.need_id):
            raise ValidationError("The engagement and Workforce Need must agree.")
        need_id = engagement.need_id
    need = WorkforceNeed.objects.select_for_update().get(pk=need_id) if need_id else None
    if need:
        if organization_id and str(organization_id) != str(need.organization_id):
            raise PermissionDenied("Choose a Workforce Need from this company.")
        organization_id = need.organization_id
    organization = CustomerOrganization.objects.select_for_update().get(pk=organization_id) if organization_id else None
    actor = access.internal_context(actor, participant=participant, organization=organization, engagement=engagement, write=True)
    if participant and participant.archived_at or organization and organization.status == "archived":
        raise ValidationError("Archived relationship records are read-only.")
    contact = CustomerContact.objects.get(pk=contact_id, organization=organization) if contact_id else None
    campaign = Campaign.objects.get(pk=campaign_id, organization=organization) if campaign_id else None
    request = WorkforceRequest.objects.get(pk=workforce_request_id, organization=organization, archived_at__isnull=True) if workforce_request_id else None
    if request and need and not WorkforceRequestPosition.objects.filter(request=request, need=need).exists():
        raise ValidationError("The Workforce Request must include this position.")
    return actor, {"participant": participant, "organization": organization, "contact": contact,
        "engagement": engagement, "candidate": candidate, "need": need, "campaign": campaign, "workforce_request": request}


def create_internal(*, actor, data, operation_id, **associations):
    with policy_transaction():
        actor = access.fresh_staff(actor)
        actor, links = internal_associations(actor, **associations)
        values = checked_fields(data)
        fingerprint = digest({"audience": "btw", "links": {k: str(v.pk) if v else None for k, v in links.items()},
                              "data": {k: str(getattr(v, "pk", v)) for k, v in values.items()}})
        existing = Interaction.objects.filter(operation_id=operation_id).first()
        if existing:
            access.internal_record(actor, existing)
            return replay(existing, actor, fingerprint)
        row = Interaction(audience="btw", created_by=actor, operation_id=operation_id,
                          payload_fingerprint=fingerprint, **links, **values)
        row.full_clean()
        row.save()
        record_revision(row, actor, "created", "Manually recorded BTW interaction; no message sent or provider delivery asserted.")
        return row


def create_employer(*, actor, organization_id, disclosure_id, data, operation_id):
    with policy_transaction():
        member, grant = customer_grant(actor, organization_id, disclosure_id, write=True)
        values = checked_fields(data, employer=True)
        fingerprint = digest({"grant": str(grant.pk), "member": str(member.pk), "data": values})
        existing = Interaction.objects.filter(operation_id=operation_id).first()
        if existing:
            access.employer_record(actor, organization_id, existing, write=True)
            return replay(existing, member.identity.user, fingerprint)
        row = Interaction(audience="employer", owner_organization=grant.organization, organization=grant.organization,
            participant=grant.participant, engagement=grant.candidate.opportunity, candidate=grant.candidate,
            need=grant.need, workforce_request=grant.workforce_request, disclosure=grant, membership=member,
            created_by=member.identity.user, operation_id=operation_id, payload_fingerprint=fingerprint, **values)
        row.full_clean()
        row.save()
        record_revision(row, member.identity.user, "created", "Employer manually recorded an external interaction; no recruiting status or release changed.")
        return row


def correct(*, actor, interaction_id, expected_revision, data, why, organization_id=None):
    with policy_transaction():
        row = Interaction.objects.select_for_update().get(pk=interaction_id)
        if organization_id is None:
            actor = access.internal_record(actor, row, write=True)
        else:
            member, _ = access.employer_record(actor, organization_id, row, write=True)
            actor = member.identity.user
        reason(why)
        if row.revision != expected_revision:
            raise ValidationError("This interaction changed. Reload and review the latest entry before correcting it.")
        if row.archived_at:
            raise ValidationError("Restore an archived entry before correcting its details.")
        for key, value in checked_fields(data, employer=organization_id is not None,
                                         current_responsible_id=row.responsible_staff_id).items():
            setattr(row, key, value)
        row.revision += 1
        row.full_clean()
        row.save()
        record_revision(row, actor, "corrected", why)
        return row


def archive(*, actor, interaction_id, expected_revision, why, restore=False, organization_id=None):
    with policy_transaction():
        row = Interaction.objects.select_for_update().get(pk=interaction_id)
        if organization_id is None:
            actor = access.internal_record(actor, row, write=True)
        else:
            member, _ = access.employer_record(actor, organization_id, row, write=True)
            actor = member.identity.user
        reason(why)
        if row.revision != expected_revision:
            raise ValidationError("This interaction changed. Reload before continuing.")
        if bool(row.archived_at) != restore:
            raise ValidationError("This interaction is already in the requested state.")
        row.archived_at = None if restore else timezone.now()
        row.archived_by = None if restore else actor
        row.revision += 1
        row.full_clean()
        row.save()
        record_revision(row, actor, "restored" if restore else "archived", why)
        return row


def staff_record(actor, interaction_id):
    with policy_transaction():
        row = Interaction.objects.select_related("participant", "organization", "engagement", "created_by", "created_by__staff_profile", "membership__identity").get(pk=interaction_id)
        (access.internal_record if row.audience == "btw" else access.staff_employer_record)(actor, row)
        return row


def staff_history(actor, *, audience, participant_id=None, organization_id=None, include_archived=False):
    with policy_transaction():
        actor = access.fresh_staff(actor)
        if audience not in Interaction.Audience.values:
            raise PermissionDenied
        person_ids = None
        if participant_id:
            # Archived profiles retain their authorized read-only history.
            # Recruiting's write/selection helper intentionally excludes them.
            item = Participant.objects.get(pk=canonical_id(participant_id), kind=Participant.Kind.PERSON)
            if audience == "btw":
                access.internal_context(actor, participant=item)
            else:
                from apps.recruiting.access import fresh_staff
                fresh_staff(actor)
            person_ids = identity_members(item.pk)
        if organization_id:
            require_manager(actor)
        query = Interaction.objects.filter(audience=audience).select_related("participant", "organization", "engagement", "created_by", "created_by__staff_profile", "membership__identity")
        if person_ids:
            query = query.filter(participant_id__in=person_ids)
        if organization_id:
            query = query.filter(organization_id=organization_id)
        if not include_archived:
            query = query.filter(archived_at__isnull=True)
        rows = []
        for row in query.iterator():
            try:
                (access.internal_record if audience == "btw" else access.staff_employer_record)(actor, row)
            except PermissionDenied:
                continue
            rows.append(row)
        return rows


def employer_projection(row):
    # Never pass an unrestricted Participant, grant, audit snapshot, or Task to Customer templates.
    identity = row.membership.identity
    return {"id": row.pk, "type": row.get_activity_type_display(), "activity_type": row.activity_type,
        "direction": row.get_direction_display(), "summary": row.summary, "notes": row.notes, "outcome": row.outcome,
        "occurred_at": row.occurred_at, "created_at": row.created_at, "next_step": row.next_step,
        "follow_up_on": row.follow_up_on, "author": (identity.first_name + " " + identity.last_name).strip(),
        "source": "Manually recorded · external action", "revision": row.revision, "archived": bool(row.archived_at)}


def employer_history(actor, organization_id, disclosure_id, *, include_archived=False, limit=None):
    with policy_transaction():
        member, grant = customer_grant(actor, organization_id, disclosure_id)
        query = Interaction.objects.filter(audience="employer", owner_organization_id=organization_id,
            disclosure=grant).select_related("membership__identity")
        if not include_archived:
            query = query.filter(archived_at__isnull=True)
        if limit is not None:
            query = query[:max(1, min(int(limit), 50))]
        rows = []
        for row in query:
            access.employer_record(actor, organization_id, row)
            projection = employer_projection(row)
            projection["can_correct"] = bool(member.role == "lead" or row.created_by_id == actor.pk) and may_respond(member, grant)
            rows.append(projection)
        return rows


def create_followup(*, actor, interaction_id, data, operation_id):
    from apps.operations.models import Task, TaskParticipantLink
    from apps.operations.services import save_task
    from apps.operations.task_access import require_task_access
    with policy_transaction():
        row = Interaction.objects.select_for_update().get(pk=interaction_id)
        actor = access.internal_record(actor, row)
        if row.archived_at or (row.participant and row.participant.archived_at) or (row.organization and row.organization.status == "archived"):
            raise ValidationError("Restore the relationship and interaction before creating a follow-up.")
        fingerprint = digest({"interaction": str(row.pk), "actor": str(actor.pk), "data": data})
        existing = Task.objects.filter(pk=operation_id).first()
        if existing:
            require_task_access(actor, existing)
            link = InteractionTaskLink.objects.filter(interaction=row, task=existing, added_by=actor,
                payload_fingerprint=fingerprint).first()
            if not link:
                raise ValidationError("This follow-up submission identifier was already used for different details.")
            return existing, None
        task, form = save_task(actor=actor, data=data, creation_id=operation_id)
        if task is None:
            return None, form
        if row.participant_id:
            TaskParticipantLink.objects.get_or_create(task=task, participant=row.participant)
        InteractionTaskLink.objects.create(interaction=row, task=task, added_by=actor, payload_fingerprint=fingerprint)
        if row.engagement_id:
            from apps.recruiting.workflow import attach_task
            attach_task(actor=actor, opportunity_id=row.engagement_id, task_id=task.pk, candidate_id=row.candidate_id)
        row.revision += 1
        row.save(update_fields=["revision", "updated_at"])
        record_revision(row, actor, "task_linked", "BTW staff explicitly created a follow-up in the existing Tasks system.")
        return task, form


def link_task(*, actor, interaction_id, task_id):
    from apps.operations.models import Task, TaskParticipantLink
    from apps.operations.task_access import require_task_access
    with policy_transaction():
        row = Interaction.objects.select_for_update().get(pk=interaction_id)
        actor = access.internal_record(actor, row)
        if row.archived_at or (row.participant and row.participant.archived_at) or (row.organization and row.organization.status == "archived"):
            raise ValidationError("Restore the relationship and interaction before linking a Task.")
        task = Task.objects.select_for_update().get(pk=task_id)
        require_task_access(actor, task)
        link, created = InteractionTaskLink.objects.get_or_create(interaction=row, task=task, defaults={"added_by": actor})
        if created:
            if row.participant_id:
                TaskParticipantLink.objects.get_or_create(task=task, participant=row.participant)
            row.revision += 1
            row.save(update_fields=["revision", "updated_at"])
            record_revision(row, actor, "task_linked", "BTW staff explicitly linked an accessible existing Task.")
        return link


def save_contact_relationship(*, actor, contact_id, data, expected_revision, why):
    with policy_transaction():
        actor = require_manager(access.fresh_staff(actor))
        contact = CustomerContact.objects.select_for_update().get(pk=contact_id)
        organization = CustomerOrganization.objects.select_for_update().get(pk=contact.organization_id)
        if organization.status == "archived":
            raise ValidationError("Archived customer contact settings are read-only.")
        if set(data) - set(CONTACT_FIELDS):
            raise ValidationError("Contact relationship settings cannot create users or change company membership.")
        row = ContactRelationship.objects.select_for_update().filter(contact=contact).first()
        if (row.revision if row else 0) != expected_revision:
            raise ValidationError("These contact preferences changed. Reload before saving.")
        why = reason(why)
        before = snapshot(row) if row else None
        if "is_primary" in data:
            primary = bool(data["is_primary"])
            replacing = primary and organization.primary_contact_id != contact.pk
            clearing = not primary and organization.primary_contact_id == contact.pk
            if replacing and not contact.is_active:
                raise ValidationError("Choose an active contact as Primary.")
            if replacing or clearing:
                from apps.customers.services import save_organization
                previous_primary = organization.primary_contact_id
                organization = save_organization(actor=actor, org_id=organization.pk,
                    expected_revision=organization.revision, data={"primary_contact": contact if primary else None}, why=why)
                record_audit(actor=actor, action="crm.contact.primary_changed", instance=organization,
                    source="relationship_management", old_data={"primary_contact_id": str(previous_primary) if previous_primary else None},
                    new_data={"primary_contact_id": str(organization.primary_contact_id) if organization.primary_contact_id else None}, reason=why)
        row = row or ContactRelationship(contact=contact, organization=contact.organization)
        for key, value in data.items():
            if key != "is_primary":
                setattr(row, key, value)
        row.updated_by = actor
        if before:
            row.revision += 1
        row.full_clean()
        row.save()
        record_audit(actor=actor, action="crm.contact.relationship_saved", instance=row,
            source="relationship_management", old_data=before, new_data=snapshot(row), reason=why)
        return row
