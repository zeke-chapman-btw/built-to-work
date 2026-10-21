"""Configuration summaries only; no physical readiness or result mutations."""
from django.urls import reverse
from apps.assessments.services import eligible_event_categories
from apps.games.models import EventGameConfiguration
from apps.simulators.models import EventSimulatorConfiguration
from .template_models import OperationsTemplateRevision, EventOperationsApplication
from .template_services import can_view_setup, can_apply

def setup_context(user, event):
    if not can_view_setup(user): return {}
    application = EventOperationsApplication.objects.select_related("revision__template", "applied_by").filter(event=event).first()
    excluded = application.snapshot.get("not_applicable", []) if application else []
    eligible = eligible_event_categories(event)
    stations = event.station_assignments.filter(enabled=True, station__is_active=True)
    game = EventGameConfiguration.objects.filter(event=event, game__active=True).first()
    simulator = EventSimulatorConfiguration.objects.filter(event=event, is_active=True, station__is_active=True, profile__is_active=True)
    registration = event.group_mode != "disabled" or event.registration_capacity is not None or event.public_registration_status != "disabled" or bool(application and any(r["kind"] == "registration" for r in application.snapshot["resources"]))
    configured = {"assessment": len(eligible)>=2, "station": stations.exists(), "game": game is not None, "registration": registration}
    details = {"assessment": f"{len(eligible)} eligible quiz categories; existing kiosk flow needs at least two.",
        "station": f"{stations.count()} enabled assignments. Physical station event context must be activated separately.",
        "game": game.game.display_name if game else "No active game configuration.",
        "registration": f"Grouping: {event.get_group_mode_display()}; capacity: {event.registration_capacity if event.registration_capacity is not None else 'not set'}. Public registration is unchanged by template application."}
    rows = []
    from .template_models import KINDS
    for kind, label in KINDS:
        rows.append({"label": label, "state": "Configured" if configured[kind] else "Not Applicable" if kind in excluded else "Not Configured", "detail": details[kind]})
    rows.append({"label": "Simulator", "state": "Configured" if simulator.exists() else "Not Configured", "detail": "Existing event/station/profile configurations only. Device calibration, XML evidence and reusable calibration templates are not part of Build 10B."})
    available = OperationsTemplateRevision.objects.filter(status="published", template__retired_at__isnull=True).select_related("template").order_by("template__name", "-number") if can_apply(user) and not application else []
    return {"setup_visible": True, "setup_application": application, "setup_rows": rows,
        "setup_can_apply": can_apply(user), "setup_revisions": available,
        "setup_templates_url": reverse("operations:templates") if user.has_perm("operations.view_operationstemplate") else None}
