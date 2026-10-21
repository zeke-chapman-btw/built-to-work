"""Read-only discovery foundation. No readiness policy, checks or approvals are inferred."""
from .template_services import can_view_setup
from .template_models import EventOperationsApplication

# Historical phase names only; no required checks, active phase or transitions are assigned.
PLANNING_PHASES = (
    "Pre-event preparation", "Arrival and setup", "Live event operations", "Closeout and pack-up",
)

def health_context(user, event):
    if not can_view_setup(user):
        return {}
    application = EventOperationsApplication.objects.select_related("revision").filter(event=event).first()
    return {"health_foundation_visible": True, "health_planning_phases": PLANNING_PHASES,
            "health_event_status": event.get_status_display(), "health_event_changed_at": event.updated_at,
            "health_application": application}
