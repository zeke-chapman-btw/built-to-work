"""Admin-confirmed form discovery/download on a trailer; no automatic activation."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from apps.events.models import OfflineEventPreparation
from .models import IntakeFormAssignment, TrailerIntakeActivation, RegistrationFormVersion
from .intake_lifecycle import verify_published
from .intake_config_views import admin_page
from .intake_trailer import portable_version, fetch_cloud, install_version
from .workspace_views import context


def cloud_response(preparation, stable_id=None):
    assignment = IntakeFormAssignment.objects.filter(
        channel="trailer", event=preparation.event, trailer=preparation.trailer, is_active=True).first()
    if not assignment:
        return JsonResponse({"detail": "No approved form is assigned for this trailer/event"}, status=404)
    versions = RegistrationFormVersion.objects.filter(
        Q(form=assignment.form) | Q(submissions__event=preparation.event),
        form__is_smart_intake=True, status__in=["published", "retired"]).select_related("form").distinct().order_by("-version")
    result = {"event_id": str(preparation.event_id), "trailer_identity": preparation.trailer.identity}
    if stable_id:
        version = get_object_or_404(versions, stable_id=stable_id)
        verify_published(version, allow_retired=True)
        result["package"] = portable_version(version)
    else:
        result["versions"] = [{"stable_id": str(v.stable_id), "form_name": v.form.name,
            "version": v.version, "hash": v.content_hash, "status": v.status,
            "assigned": v.pk == assignment.active_version_id,
            "activation_allowed": v.form_id == assignment.form_id and assignment.form.is_active and v.status == "published"} for v in versions]
    response = JsonResponse(result)
    response["Cache-Control"] = "no-store"
    return response


@admin_page
@never_cache
@require_http_methods(["GET", "POST"])
def manage(request, preparation_id):
    preparation = OfflineEventPreparation.objects.select_related("event", "trailer").filter(pk=preparation_id).first()
    if preparation is None:
        from apps.events.models import CentralEventDownload
        from apps.events.downsync import apply_configuration
        from django.db import transaction
        download = get_object_or_404(CentralEventDownload, preparation_id=preparation_id)
        error = ""
        if request.method == "POST":
            try:
                if request.POST.get("action") != "prepare" or request.POST.get("confirmed") != "yes":
                    raise ValidationError("Confirm preparation of the downloaded event information.")
                if not download.config or download.config.get("preparation_id") != str(preparation_id):
                    raise ValidationError("The staged event configuration is incomplete.")
                with transaction.atomic():
                    apply_configuration(download, download.config)
            except ValidationError as exc:
                error = "; ".join(exc.messages)
            else:
                return redirect("participants:intake-trailer-manage", preparation_id=preparation_id)
        return render(request, "participants/intake_trailer_prepare.html", context(request,
            page_title="Prepare trailer intake configuration", download=download, error=error))
    activation = TrailerIntakeActivation.objects.select_related("active_version__form").filter(
        trailer=preparation.trailer, event=preparation.event).first()
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("action") == "check":
                result = fetch_cloud(actor=request.user, preparation=preparation)
                versions = result.get("versions")
                if not isinstance(versions, list):
                    raise ValidationError("The central form inventory is invalid.")
                # Inventory never installs or changes a form.
                request.session["intake_available_" + str(preparation.pk)] = versions
                if activation:
                    activation.available_updates, activation.last_checked_at = versions, timezone.now()
                    activation.last_error = ""
                    activation.save(update_fields=["available_updates", "last_checked_at", "last_error", "updated_at"])
            elif request.POST.get("action") == "download":
                if request.POST.get("confirmed") != "yes":
                    raise ValidationError("Confirm the selected download and activation before proceeding.")
                chosen = request.POST.get("version_id")
                offered = request.session.get("intake_available_" + str(preparation.pk), [])
                if not any(v.get("stable_id") == chosen for v in offered):
                    raise ValidationError("Check available versions and choose one from that inventory.")
                package = fetch_cloud(actor=request.user, preparation=preparation, version_id=chosen).get("package")
                expected = next(v for v in offered if v["stable_id"] == chosen)
                if not package or package.get("content_hash") != expected["hash"]:
                    raise ValidationError("The selected version changed. Check again before downloading.")
                install_version(actor=request.user, preparation=preparation, package=package,
                                confirmed=True, activate=bool(expected.get("activation_allowed")))
                messages.success(request, "Approved version installed. In-progress intakes keep their original version.")
            else:
                raise ValidationError("Choose a supported synchronization action.")
        except ValidationError as exc:
            error = "; ".join(exc.messages)
            if activation:
                activation.last_error = error[:500]
                activation.last_checked_at = timezone.now()
                activation.save(update_fields=["last_error", "last_checked_at", "updated_at"])
        else:
            return redirect("participants:intake-trailer-manage", preparation_id=preparation.pk)
    offered = request.session.get("intake_available_" + str(preparation.pk),
                                 activation.available_updates if activation else [])
    return render(request, "participants/intake_trailer.html", context(request,
        page_title="Trailer intake forms", preparation=preparation, activation=activation,
        versions=offered, error=error))
