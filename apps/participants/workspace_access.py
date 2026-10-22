"""Participant workspace boundaries use the existing Django staff/permission system."""
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from .models import Participant


def staff_permission(user, *permissions):
    return bool(user.is_authenticated and user.is_active and user.is_staff and user.has_perms(permissions))


def require_people(user, *permissions):
    if not staff_permission(user, "participants.view_participant", *permissions):
        raise PermissionDenied


def people_for(user):
    require_people(user)
    from .identity import resolved_identity_map
    return Participant.objects.filter(kind=Participant.Kind.PERSON).exclude(pk__in=resolved_identity_map())


def can_analytics(user):
    return staff_permission(user, "participants.view_participant", "participants.view_participant_analytics")


def can_notes(user):
    return staff_permission(user, "participants.view_participant", "participants.view_participantnote")


def can_edit_note(user, note):
    return can_notes(user) and user.has_perm("participants.change_participantnote") and (user.is_superuser or note.author_id == user.pk)


def can_create_customer_note(user, **kwargs):
    # No customer membership authority exists yet. Neither staff privilege nor
    # a supplied organization label may stand in for Build 14 customer authorship.
    return False


FILTER_PERMISSIONS = {
    "event": ("events.view_eventregistration",),
    "participation": ("events.view_eventregistration",),
    "qualification": ("participants.view_participantqualification",),
    "qualification_kind": ("participants.view_participantqualification",),
    "skill": ("participants.view_participantqualification",),
    "qualification_status": ("participants.view_participantqualification",),
    "document_status": ("participants.view_participantdocument",),
    "assessment": (),
    "assessment_category": ("assessments.view_quizattempt",),
}
ASSESSMENT_PERMISSIONS = {"quiz": "assessments.view_quizattempt", "simulator": "simulators.view_simulatorcapture", "game": "games.view_gamesession"}


def validate_filter_permissions(user, params):
    require_people(user)
    for key, perms in FILTER_PERMISSIONS.items():
        if params.get(key) and not user.has_perms(perms):
            raise PermissionDenied("This filter requires access to its underlying records.")
    kind = params.get("assessment")
    if kind in ASSESSMENT_PERMISSIONS and not user.has_perm(ASSESSMENT_PERMISSIONS[kind]):
        raise PermissionDenied("This assessment filter is unavailable to your account.")
    if (params.get("score_min") not in (None, "") or params.get("score_max") not in (None, "")) and kind not in ASSESSMENT_PERMISSIONS:
        # The form reports a useful validation error for an unspecified score unit.
        return
