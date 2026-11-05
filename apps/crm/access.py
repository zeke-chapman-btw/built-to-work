"""Fresh domain permissions and existing disclosure policy on every read/write."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from apps.accounts.roles import manager
from apps.customers.access import require_manager
from apps.participants.workspace_access import can_notes, require_people
from apps.recruiting.selection import opportunity_for
from apps.recruiting.workflow import candidate_for


def fresh_staff(actor):
    if not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("Staff sign-in is required.")
    user = get_user_model().objects.filter(pk=actor.pk, is_active=True, is_staff=True).first()
    if (not user or user.get_session_auth_hash() != actor.get_session_auth_hash() or
            hasattr(user, "customer_identity") or hasattr(user, "participant_account")):
        raise PermissionDenied("Use your current BTW staff sign-in.")
    return user


def internal_context(actor, *, participant=None, organization=None, engagement=None, write=False):
    user = fresh_staff(actor)
    if participant:
        require_people(user)
        if not can_notes(user) or (write and not user.has_perm("participants.add_participantnote")):
            raise PermissionDenied("Your role does not allow this private interaction history.")
    if organization:
        if engagement:
            opportunity_for(user, engagement.pk, write=write)
        else:
            require_manager(user)
    if not participant and not organization:
        raise PermissionDenied("Open a Participant or Customer relationship first.")
    return user


def internal_record(actor, row, *, write=False):
    if row.audience != "btw":
        raise PermissionDenied("BTW and employer histories are separate.")
    user = internal_context(actor, participant=row.participant, organization=row.organization,
                            engagement=row.engagement, write=write)
    if row.candidate_id:
        candidate_for(user, row.candidate_id, write=write)
    if write and not (user.is_superuser or manager(user) or row.created_by_id == user.pk):
        raise PermissionDenied("Only the original author or a BTW Manager may correct this entry.")
    if write and row.participant and row.participant.archived_at:
        raise PermissionDenied("Archived participant histories are read-only.")
    if write and row.organization and row.organization.status == "archived":
        raise PermissionDenied("Archived customer histories are read-only.")
    return user


def employer_record(actor, organization_id, row, *, write=False):
    from apps.recruiting.employer import customer_grant
    if row.audience != "employer" or str(row.owner_organization_id) != str(organization_id):
        raise PermissionDenied("This employer interaction is unavailable.")
    member, grant = customer_grant(actor, organization_id, row.disclosure_id, write=write)
    if (grant.participant_id != row.participant_id or grant.candidate_id != row.candidate_id or
            grant.need_id != row.need_id or grant.candidate.opportunity_id != row.engagement_id):
        raise PermissionDenied("The candidate relationship requires BTW review.")
    if write and member.role != "lead" and row.created_by_id != member.identity.user_id:
        raise PermissionDenied("Only the author or an authorized company Lead may correct this interaction.")
    return member, grant


def staff_employer_record(actor, row):
    # Authorized BTW retains audit/history even after an employer release is revoked.
    user = fresh_staff(actor)
    candidate_for(user, row.candidate_id)
    return user
