"""Admin-only permanent BTW Test Participant controls."""
import base64
from io import BytesIO

import qrcode
from qrcode.image.svg import SvgPathImage
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.events.models import Event
from apps.events.services import audit_action
from .models import Participant, TestParticipantRun
from .system_test import get_or_create_test_participant, reset_test_participant_run, start_or_resume_test_run


@never_cache
@require_http_methods(["GET", "POST"])
def test_participant_admin(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_superuser:
        raise PermissionDenied
    person = Participant.objects.filter(kind=Participant.Kind.SYSTEM_TEST).first()
    events = Event.objects.filter(status=Event.Status.UPCOMING,
        start_at__lte=timezone.now(), end_at__gte=timezone.now()).order_by("start_at")
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "provision":
                person = get_or_create_test_participant()
                audit_action(actor=request.user, action="testing.participant.provisioned",
                    instance=person, source="testing_admin")
            elif action in ("start", "reset"):
                if person is None:
                    raise ValidationError("Provision the permanent test identity first.")
                if action == "start":
                    event = get_object_or_404(events, pk=request.POST.get("event_id"))
                    run = start_or_resume_test_run(participant=person, event=event, actor=request.user)
                    audit_action(actor=request.user, action="testing.run.started",
                        instance=run, source="testing_admin", new_data={"event_id": str(event.pk)})
                else:
                    run = get_object_or_404(TestParticipantRun, pk=request.POST.get("run_id"),
                        participant=person, reset_at__isnull=True)
                    reason = request.POST.get("reason", "").strip()
                    if not reason:
                        raise ValidationError("Enter a reason for resetting this test experience.")
                    replacement = reset_test_participant_run(run=run, actor=request.user, reason=reason)
                    audit_action(actor=request.user, action="testing.run.reset", instance=run,
                        source="testing_admin", reason=reason,
                        new_data={"replacement_run_id": str(replacement.pk)})
            else:
                raise ValidationError("Choose a supported Test Participant action.")
            return redirect("participants:testing")
        except ValidationError as exc:
            error = "; ".join(exc.messages)
    qr_data = ""
    if person and person.test_qr_token:
        out = BytesIO()
        qrcode.make(str(person.test_qr_token), image_factory=SvgPathImage).save(out)
        qr_data = "data:image/svg+xml;base64," + base64.b64encode(out.getvalue()).decode("ascii")
    recent = (TestParticipantRun.objects.filter(participant=person).select_related("event", "experience_session")
              .order_by("-started_at")[:12]) if person else []
    return render(request, "internal/test_participant.html", {"page_title": "Test Participant",
        "person": person, "qr_data": qr_data, "events": events, "recent": recent, "error": error,
        "simulator_identifier": settings.SIMULATOR_TEST_IDENTIFIER})
