from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from apps.accounts.roles import has_role

def staff(actor):
    if not getattr(actor,"is_authenticated",False):raise PermissionDenied
    fresh=get_user_model().objects.filter(pk=actor.pk,is_active=True,is_staff=True).first()
    if not fresh or fresh.get_session_auth_hash()!=actor.get_session_auth_hash():raise PermissionDenied("Your staff access changed. Sign in again.")
    if hasattr(fresh,"participant_account") or hasattr(fresh,"customer_identity"):raise PermissionDenied
    return fresh

def allowed_tabs(actor):
    actor=staff(actor)
    from apps.event_reports.services import can_report
    from apps.workdays.services import manager
    from apps.participants.workspace_access import can_analytics
    from apps.customers.access import is_manager
    tabs=["highlights"]
    if can_report(actor):tabs.append("events")
    if manager(actor):tabs.append("payroll")
    if can_analytics(actor):tabs.append("participants")
    if is_manager(actor):tabs.append("customers")
    try:
        from apps.recruiting.access import fresh_staff
        fresh_staff(actor);tabs.extend(["opportunities","hires"])
    except PermissionDenied:pass
    return tabs

def own_pay_profile(actor):
    """Read eligibility afresh for every page, statement and export request."""
    from apps.staffing.models import StaffProfile
    actor = staff(actor)
    profile = StaffProfile.objects.filter(user=actor, my_pay_enabled=True).first()
    if profile is None:
        raise PermissionDenied("My Pay is not enabled for your staff profile.")
    return profile

def require_tab(actor,tab):
    actor=staff(actor)
    if tab not in allowed_tabs(actor):raise PermissionDenied("This report requires its source-record permissions.")
    return actor

def can_finalize(actor):
    actor=staff(actor)
    return bool(actor.can_finalize_event_reports and actor.has_perm("reporting.finalize_event_reports") and (has_role(actor,"administrator") or has_role(actor,"manager")) and "events" in allowed_tabs(actor))

def can_approve_payroll(actor):
    actor=staff(actor)
    return bool(actor.can_approve_weekly_payroll and actor.has_perm("reporting.approve_weekly_payroll") and has_role(actor,"administrator") and "payroll" in allowed_tabs(actor))

def require_finalizer(actor):
    actor=staff(actor)
    if not can_finalize(actor):raise PermissionDenied("An eligible account must explicitly have Can finalize event reports enabled.")
    return actor

def require_payroll_approver(actor):
    actor=staff(actor)
    if not can_approve_payroll(actor):raise PermissionDenied("A designated BTW Administrator must explicitly have weekly payroll approval enabled.")
    return actor
