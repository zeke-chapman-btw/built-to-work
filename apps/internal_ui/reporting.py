"""Navigation only: destinations retain their existing access policies."""
from django.urls import reverse
from apps.operations.task_access import staff_user


def report_destinations(user):
    if not staff_user(user):
        return []
    from apps.event_reports.services import can_report
    from apps.workdays.services import manager
    destinations = []
    if can_report(user):
        destinations.append({"label": "Event Reports & Analytics", "url": reverse("event_reports:index"),
            "description": "Event costs, expenses, incentives and verified performance."})
    if manager(user):
        destinations.append({"label": "Workday & Reimbursement Reports", "url": reverse("workdays:reports"),
            "description": "Recorded workdays, daily compensation, mileage and reimbursement exports."})
    return destinations
