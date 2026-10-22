"""Read-only event checklist summary. GET never initializes or changes event records."""
from django.urls import reverse
from .execution_models import EventExecution
from .execution_services import can_view,administrator,progress
PLANNING_PHASES=("Pre-event preparation","Arrival and setup","Live event operations","Closeout and pack-up")

def health_context(user,event):
    if not can_view(user,event):return {}
    execution=EventExecution.objects.filter(event=event).first()
    return dict(health_foundation_visible=True,health_execution=execution,
        health_indicators=progress(execution) if execution else [],
        health_manager_url=reverse("operations:execution-manager",args=[event.pk]) if administrator(user) else "",
        health_execution_url=reverse("operations:execution",args=[event.pk]),
        health_monitor_url=reverse("operations:health",args=[event.pk]),
        health_closeout_url=reverse("operations:closeout",args=[event.pk]))
