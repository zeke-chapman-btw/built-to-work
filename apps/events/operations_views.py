from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponseBadRequest
from django.views.decorators.http import require_POST
from urllib import error, request as urlrequest
from urllib.parse import urlsplit

from .models import BackupRecord, OfflineTicketRange, ParticipantMatchReview, SyncOutboxItem, TicketDelivery, OperationalIncident, OperationalNotificationDelivery
from .services import audit_action


def central_connectivity_status():
    """Live HTTP reachability, separate from historical sync success."""
    endpoint = getattr(settings, "BTW_CENTRAL_SYNC_URL", "")
    parsed = urlsplit(endpoint)
    if not endpoint or parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        return "Not configured"
    try:
        req = urlrequest.Request(endpoint, method="HEAD")
        with urlrequest.urlopen(req, timeout=2):
            return "Reachable now"
    except error.HTTPError as exc:
        return "Reachable now" if exc.code in (401, 403, 405) else "Server error"
    except (OSError, ValueError):
        return "Unavailable now"


@login_required
@require_POST
def retry_ticket_delivery(request, delivery_id):
    if not request.user.is_staff or not request.user.has_perm("events.change_ticketdelivery"):
        raise PermissionDenied
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        return HttpResponseBadRequest("A reason is required.")
    from django.db import transaction
    with transaction.atomic():
        delivery = get_object_or_404(TicketDelivery.objects.select_for_update(), pk=delivery_id)
        if delivery.status not in (TicketDelivery.Status.FAILED, TicketDelivery.Status.UNCERTAIN):
            return HttpResponseBadRequest("Only failed or uncertain deliveries can be retried.")
        before = delivery.status
        delivery.status = TicketDelivery.Status.QUEUED
        delivery.attempts = 0
        delivery.next_attempt_at = None
        delivery.save(update_fields=["status", "attempts", "next_attempt_at"])
        audit_action(actor=request.user, action="ticket.delivery.retry", instance=delivery,
                     old_data={"status": before}, new_data={"status": delivery.status}, reason=reason[:500])
    return redirect("events:operations-dashboard")


@login_required
def operations_dashboard(request):
    if not request.user.is_staff or not request.user.has_perm("events.view_syncoutboxitem"):
        raise PermissionDenied
    pending = SyncOutboxItem.objects.filter(status__in=[SyncOutboxItem.Status.PENDING, SyncOutboxItem.Status.IN_FLIGHT]).count()
    failed_sync = SyncOutboxItem.objects.filter(status=SyncOutboxItem.Status.FAILED).count()
    latest_sync = SyncOutboxItem.objects.filter(accepted_at__isnull=False).order_by("-accepted_at").first()
    open_reviews = ParticipantMatchReview.objects.filter(status=ParticipantMatchReview.Status.OPEN).count()
    latest_backup = BackupRecord.objects.order_by("-created_at").first()
    queued_delivery = TicketDelivery.objects.filter(status=TicketDelivery.Status.QUEUED).count()
    failed_delivery = TicketDelivery.objects.filter(status=TicketDelivery.Status.FAILED).count()
    uncertain_delivery = TicketDelivery.objects.filter(status=TicketDelivery.Status.UNCERTAIN).count()
    pending_alerts = OperationalNotificationDelivery.objects.filter(status=OperationalNotificationDelivery.Status.QUEUED).count()
    failed_alerts = OperationalNotificationDelivery.objects.filter(status__in=[OperationalNotificationDelivery.Status.FAILED, OperationalNotificationDelivery.Status.UNCERTAIN]).count()
    open_incidents = OperationalIncident.objects.filter(status=OperationalIncident.Status.OPEN).order_by("-last_seen_at")[:10]
    retryable = TicketDelivery.objects.filter(status__in=[TicketDelivery.Status.FAILED, TicketDelivery.Status.UNCERTAIN]).select_related("registration__event").order_by("-created_at")[:20]
    ranges = []
    for item in OfflineTicketRange.objects.select_related("preparation"):
        ranges.append({"preparation": item.preparation_id, "kind": item.kind, "start": item.start_number,
                       "end": item.end_number, "next": item.next_number,
                       "available": max(0, int(item.end_number) - int(item.next_number) + 1)})
    return render(request, "events/operations_dashboard.html", {
        "page_title": "Platform monitoring",
        "central_configured": bool(getattr(settings, "BTW_CENTRAL_SYNC_URL", "")),
        "pending_sync": pending, "failed_sync": failed_sync, "latest_sync": latest_sync,
        "open_reviews": open_reviews, "latest_backup": latest_backup,
                    "queued_delivery": queued_delivery, "failed_delivery": failed_delivery,
            "uncertain_delivery": uncertain_delivery, "pending_alerts": pending_alerts,
            "failed_alerts": failed_alerts, "open_incidents": open_incidents,
            "retryable_deliveries": retryable,
            "can_retry_delivery": request.user.has_perm("events.change_ticketdelivery"),
            "central_connectivity": central_connectivity_status(), "ticket_ranges": ranges,
    })
