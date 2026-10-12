from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .models import BackupRecord, OfflineTicketRange, ParticipantMatchReview, SyncOutboxItem, TicketDelivery


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
    ranges = []
    for item in OfflineTicketRange.objects.select_related("preparation"):
        ranges.append({"preparation": item.preparation_id, "kind": item.kind, "start": item.start_number,
                       "end": item.end_number, "next": item.next_number,
                       "available": max(0, int(item.end_number) - int(item.next_number) + 1)})
    return render(request, "events/operations_dashboard.html", {
        "central_configured": bool(getattr(settings, "BTW_CENTRAL_SYNC_URL", "")),
        "pending_sync": pending, "failed_sync": failed_sync, "latest_sync": latest_sync,
        "open_reviews": open_reviews, "latest_backup": latest_backup,
        "queued_delivery": queued_delivery, "failed_delivery": failed_delivery, "ticket_ranges": ranges,
    })
