import json
import os
import uuid
from functools import wraps
from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import CentralSyncReceipt, Event, OfflineEventPreparation, SyncOutboxItem, TrailerInstance
from .views import staff_required


def _token(request):
    value = request.headers.get("Authorization", "")
    return value[7:].strip() if value.lower().startswith("bearer ") else ""


def _configured_token():
    return getattr(settings, "BTW_SYNC_API_TOKEN", "") or os.environ.get("BTW_SYNC_API_TOKEN", "")


@require_POST
@csrf_exempt
def sync_ingest(request):
    configured = _configured_token()
    if not configured:
        return JsonResponse({"detail": "Synchronization is not configured"}, status=503)
    if _token(request) != configured:
        return JsonResponse({"detail": "Invalid synchronization credentials"}, status=401)
    try:
        body = json.loads(request.body or "{}")
        operation_key = str(body["operation_key"]).strip()
        trailer_identity = str(body["trailer_identity"]).strip()
        event_id = uuid.UUID(str(body["event_id"]))
        payload = body.get("payload") or {}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"detail": "Invalid synchronization envelope"}, status=400)
    if len(operation_key) > 200 or not trailer_identity or not isinstance(payload, dict):
        return JsonResponse({"detail": "Invalid synchronization envelope"}, status=400)
    trailer = TrailerInstance.objects.filter(identity=trailer_identity, is_active=True).first()
    event = Event.objects.filter(pk=event_id).first()
    if not trailer or not event:
        return JsonResponse({"detail": "Unknown trailer or event"}, status=403)
    preparation_id = body.get("preparation_id")
    preparation = None
    if preparation_id:
        try:
            preparation = OfflineEventPreparation.objects.filter(pk=uuid.UUID(str(preparation_id)), event=event, trailer=trailer).first()
        except ValueError:
            preparation = None
        if preparation is None:
            return JsonResponse({"detail": "Trailer/event scope mismatch"}, status=403)
    participant_identity = payload.get("participant_identity")
    try:
        participant_identity = uuid.UUID(str(participant_identity)) if participant_identity else None
    except ValueError:
        return JsonResponse({"detail": "Invalid participant identity"}, status=400)
    with transaction.atomic():
        existing = CentralSyncReceipt.objects.select_for_update().filter(operation_key=operation_key).first()
        if existing:
            return JsonResponse({"status": existing.status, "operation_key": operation_key, "duplicate": True}, status=200)
        status = CentralSyncReceipt.Status.ACCEPTED
        last_error = ""
        if participant_identity:
            from apps.participants.models import Participant
            matches = Participant.objects.filter(identity_uuid=participant_identity, archived_at__isnull=True)
            if not matches.exists():
                status = CentralSyncReceipt.Status.REVIEW
                last_error = "Participant identity requires central reconciliation"
        receipt = CentralSyncReceipt.objects.create(
            operation_key=operation_key, trailer_identity=trailer_identity, event_id=event_id,
            preparation_id=preparation.id if preparation else None, participant_identity=participant_identity,
            ticket_number=str(payload.get("ticket_number", ""))[:10], payload=body, status=status,
            acknowledged_at=None if status == CentralSyncReceipt.Status.REVIEW else __import__("django.utils.timezone", fromlist=["now"]).now(), last_error=last_error,
        )
    return JsonResponse({"status": receipt.status, "operation_key": operation_key, "duplicate": False}, status=202 if status == CentralSyncReceipt.Status.REVIEW else 200)


@staff_required
def sync_status(request):
    pending = SyncOutboxItem.objects.filter(status__in=[SyncOutboxItem.Status.PENDING, SyncOutboxItem.Status.IN_FLIGHT, SyncOutboxItem.Status.FAILED]).count()
    failed = SyncOutboxItem.objects.filter(status=SyncOutboxItem.Status.FAILED).count()
    sent = SyncOutboxItem.objects.filter(status=SyncOutboxItem.Status.SENT).count()
    review = CentralSyncReceipt.objects.filter(status=CentralSyncReceipt.Status.REVIEW).count()
    return JsonResponse({"pending": pending, "failed": failed, "sent": sent, "central_review": review})
