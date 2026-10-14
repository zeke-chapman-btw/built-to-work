"""Authenticated central event download for prepared trailer instances."""
import uuid
import os
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from .downsync import eligible_registrations, event_configuration, registration_record, stamp
from .models import OfflineEventPreparation
from .sync import authenticate_trailer

PAGE_SIZE = 1 if settings.DEBUG and os.environ.get("BTW_7F_TEST_PAGE_SIZE") == "1" else 50


@require_GET
def event_download(request):
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    identity = request.headers.get("X-Trailer-Identity", "").strip()
    trailer = authenticate_trailer(identity, token) if identity and token else None
    if trailer is None:
        return JsonResponse({"detail": "Invalid trailer credentials"}, status=401)
    if not request.GET.get("event_id"):
        preparations = OfflineEventPreparation.objects.filter(trailer=trailer, status__in=("prepared", "active", "closed")).select_related("event")
        response = JsonResponse({"events": [{"event_id": str(item.event_id), "preparation_id": str(item.pk),
            "status": item.status, "central_event_updated_at": stamp(item.event.updated_at),
            "central_registration_count": item.event.registrations.filter(source__in=("online", "public"), status="active").count()}
            for item in preparations]})
        response["Cache-Control"] = "no-store"
        return response
    try:
        event_id = uuid.UUID(request.GET["event_id"])
    except (KeyError, ValueError):
        return JsonResponse({"detail": "Invalid event"}, status=400)
    preparation = OfflineEventPreparation.objects.select_related("event", "consent_document").filter(
        event_id=event_id, trailer=trailer, status__in=("prepared", "active", "closed")
    ).first()
    if not preparation:
        return JsonResponse({"detail": "Trailer is not authorized for this event"}, status=403)
    cursor = request.GET.get("cursor", "")
    try:
        if cursor:
            state = signing.loads(cursor, salt="btw-central-download", max_age=7 * 86400)
            if state["event_id"] != str(event_id) or state["trailer_id"] != str(trailer.pk):
                raise ValueError("Cursor scope mismatch")
            since = parse_datetime(state["since"]) if state["since"] else None
            as_of = parse_datetime(state["as_of"])
            after = state["after"]
        else:
            raw_since = request.GET.get("since", "")
            since = parse_datetime(raw_since) if raw_since else None
            if raw_since and (since is None or timezone.is_naive(since)):
                raise ValueError("Invalid checkpoint")
            as_of = timezone.now()
            after = ""
        if as_of is None or timezone.is_naive(as_of):
            raise ValueError("Invalid snapshot")
    except (signing.BadSignature, signing.SignatureExpired, KeyError, TypeError, ValueError):
        return JsonResponse({"detail": "Invalid or expired download cursor"}, status=400)
    try:
        config = event_configuration(preparation) if not cursor else None
        rows = eligible_registrations(preparation.event, since, as_of)
        page = [row for row in rows if str(row.pk) > after][:PAGE_SIZE]
        records = [registration_record(row) for row in page]
    except ValidationError as exc:
        return JsonResponse({"detail": "; ".join(exc.messages)}, status=409)
    next_cursor = ""
    if page and any(str(row.pk) > str(page[-1].pk) for row in rows):
        next_cursor = signing.dumps({"event_id": str(event_id), "trailer_id": str(trailer.pk),
                                     "since": stamp(since), "as_of": stamp(as_of), "after": str(page[-1].pk)},
                                    salt="btw-central-download")
    response = JsonResponse({"preparation_id": str(preparation.pk), "event_id": str(event_id),
                             "snapshot_at": stamp(as_of), "config": config, "records": records,
                             "next_cursor": next_cursor, "changed_count": len(rows)})
    response["Cache-Control"] = "no-store"
    return response
