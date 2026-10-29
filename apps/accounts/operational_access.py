"""Role boundaries layered over the existing record-specific capabilities."""
from .roles import active_staff,has_role,manager,managed

def limited_staff(user):
    return active_staff(user) and managed(user) and not manager(user)

def general_recruiter(user):
    return has_role(user,'recruiter') and not manager(user)

def safe_event_worker(user):
    return limited_staff(user) and not user.has_perm('events.view_event')

def protected_configuration(user,permission):
    # Legacy direct grants remain compatible until a reviewed role transition.
    return active_staff(user) and (user.is_superuser or (not managed(user) and user.has_perm(permission)))
