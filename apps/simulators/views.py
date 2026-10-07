import json
import secrets
from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .services import CaptureConflict, ingest_capture


@csrf_exempt
@require_POST
def ingest_capture_view(request):
    """Machine endpoint for the configured trailer LAN capture helper."""
    expected_token = settings.SIMULATOR_INGESTION_TOKEN
    if not expected_token:
        return JsonResponse({"error": "simulator_ingestion_unavailable"}, status=503)
    scheme, separator, supplied_token = request.headers.get("Authorization", "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not secrets.compare_digest(supplied_token, expected_token):
        return JsonResponse({"error": "unauthorized"}, status=401)
    if request.content_type != "application/json":
        return JsonResponse({"error": "json_required"}, status=415)
    if len(request.body) > 16384:
        return JsonResponse({"error": "request_too_large"}, status=413)
    try:
        data = json.loads(request.body)
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({"error": "invalid_json"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "object_required"}, status=400)
    required = ("submission_id", "event_id", "station_code", "profile_id", "profile_version",
                "raw_identifier", "raw_total_score", "captured_at")
    if any(key not in data for key in required):
        return JsonResponse({"error": "missing_required_field"}, status=400)
    try:
        captured_at = datetime.fromisoformat(data["captured_at"])
    except (TypeError, ValueError):
        return JsonResponse({"error": "invalid_capture_timestamp"}, status=400)
    try:
        capture, replay = ingest_capture(
            submission_id=data["submission_id"], event_id=data["event_id"],
            station_code=data["station_code"], profile_id=data["profile_id"],
            profile_version=data["profile_version"], raw_identifier=data["raw_identifier"],
            raw_total_score=data["raw_total_score"], captured_at=captured_at,
            diagnostic_metadata=data.get("diagnostic_metadata"),
        )
    except CaptureConflict:
        return JsonResponse({"error": "submission_id_conflict"}, status=409)
    except ValidationError as exc:
        return JsonResponse({"error": "invalid_capture", "detail": exc.messages[0]}, status=422)
    return JsonResponse({
        "accepted": True, "outcome": capture.status, "capture_id": str(capture.pk),
        "activity_completed": capture.leaderboard_eligible,
        "idempotent_replay": replay,
    }, status=200 if replay else 201)
