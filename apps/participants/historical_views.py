"""Restricted historical data tools for BTW staff."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from apps.internal_ui.people_editors import people_render as render, people_saved
from django.views.decorators.http import require_http_methods, require_POST
from .historical import (EXPORT_FIELDS, IMPORT_FIELDS, _read_rows, create_batch, export_participants,
                         import_ready, preview_batch, resolve_review)
from .models import (HistoricalImportBatch, HistoricalImportRow, HistoricalMappingTemplate, Participant)


def _require(user, permission):
    if not user.is_authenticated or not user.is_active or not user.is_staff or not user.has_perm(permission):
        raise PermissionDenied


@login_required
@require_http_methods(["GET", "POST"])
def historical_index(request):
    _require(request.user, "participants.add_historicalimportbatch")
    error = ""
    if request.method == "POST":
        try:
            uploaded = request.FILES.get("source_file")
            if uploaded is None:
                raise ValidationError("Choose a CSV or .xlsx file.")
            batch = create_batch(uploaded_file=uploaded, actor=request.user)
            return people_saved(request,"participants:historical-map", batch_id=batch.pk)
        except ValidationError as exc:
            error = "; ".join(exc.messages)
    batches = HistoricalImportBatch.objects.order_by("-created_at")[:50]
    return render(request, "participants/historical_index.html", {"batches": batches, "error": error})


@login_required
@require_http_methods(["GET", "POST"])
def historical_map(request, batch_id):
    _require(request.user, "participants.change_historicalimportbatch")
    batch = get_object_or_404(HistoricalImportBatch, pk=batch_id)
    error = ""
    try:
        headers, _ = _read_rows(batch)
    except ValidationError as exc:
        headers = []
        error = "; ".join(exc.messages)
    templates = HistoricalMappingTemplate.objects.filter(kind="import", created_by=request.user)
    selected = request.GET.get("template")
    template = templates.filter(pk=selected).first() if selected and selected.isdigit() else None
    mapping = template.mapping if template else batch.mapping
    if request.method == "POST" and headers:
        mapping = {field: request.POST.get("map_" + field, "") for field in IMPORT_FIELDS}
        mapping = {field: column for field, column in mapping.items() if column}
        try:
            preview_batch(batch, mapping, actor=request.user)
            name = request.POST.get("template_name", "").strip()[:120]
            if name:
                HistoricalMappingTemplate.objects.create(name=name, kind="import", mapping=mapping,
                                                         created_by=request.user)
            return people_saved(request,"participants:historical-batch", batch_id=batch.pk)
        except ValidationError as exc:
            error = "; ".join(exc.messages)
    mapped = [(field, mapping.get(field, "")) for field in IMPORT_FIELDS]
    return render(request, "participants/historical_map.html", {"batch": batch, "headers": headers,
        "mapped": mapped, "templates": templates, "error": error})


@login_required
@require_http_methods(["GET", "POST"])
def historical_batch(request, batch_id):
    _require(request.user, "participants.view_historicalimportbatch")
    batch = get_object_or_404(HistoricalImportBatch, pk=batch_id)
    error = ""
    if request.method == "POST":
        _require(request.user, "participants.change_historicalimportbatch")
        try:
            import_ready(batch, actor=request.user)
            return people_saved(request,"participants:historical-batch", batch_id=batch.pk)
        except ValidationError as exc:
            error = "; ".join(exc.messages)
    return render(request, "participants/historical_batch.html", {"batch": batch,
        "rows": batch.rows.select_related("participant").all(), "error": error})


@login_required
@require_http_methods(["GET", "POST"])
def historical_review(request, row_id):
    _require(request.user, "participants.change_historicalimportrow")
    row = get_object_or_404(HistoricalImportRow.objects.select_related("batch"), pk=row_id)
    if row.status != HistoricalImportRow.Status.REVIEW:
        return people_saved(request,"participants:historical-batch", batch_id=row.batch_id)
    error = ""
    if request.method == "POST":
        try:
            chosen = request.POST.get("participant_uuid", "")
            person = Participant.objects.filter(identity_uuid=chosen).first() if chosen else None
            resolve_review(row, actor=request.user, decision=request.POST.get("decision", ""),
                           reason=request.POST.get("reason", ""), participant=person)
            return people_saved(request,"participants:historical-batch", batch_id=row.batch_id)
        except (ValidationError, ValueError) as exc:
            error = str(exc)
    matches = Participant.objects.filter(identity_uuid__in=row.possible_matches)
    return render(request, "participants/historical_review.html", {"row": row,
        "matches": matches, "error": error})


@login_required
@require_http_methods(["GET", "POST"])
def historical_export(request):
    _require(request.user, "participants.add_historicalexportrecord")
    _require(request.user, "participants.view_participant")
    templates = HistoricalMappingTemplate.objects.filter(kind="export", created_by=request.user)
    selected = request.GET.get("template")
    template = templates.filter(pk=selected).first() if selected and selected.isdigit() else None
    fields = template.fields if template else ["identity_uuid", "first_name", "last_name", "email"]
    error = ""
    if request.method == "POST":
        fields = request.POST.getlist("fields")
        filters = {key: request.POST.get(key, "").strip() for key in
                   ("event_id", "date_from", "date_to", "group", "registration_source")}
        filters["without_events"] = bool(request.POST.get("without_events"))
        file_format = request.POST.get("format", "csv")
        try:
            payload, _ = export_participants(fields=fields, filters=filters, file_format=file_format,
                                             actor=request.user)
            name = request.POST.get("template_name", "").strip()[:120]
            if name:
                HistoricalMappingTemplate.objects.create(name=name, kind="export", fields=fields,
                                                         created_by=request.user)
            content_type = "text/csv; charset=utf-8" if file_format == "csv" else \
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            response = HttpResponse(payload, content_type=content_type)
            response["Content-Disposition"] = f'attachment; filename="btw-historical-participants.{file_format}"'
            response["Cache-Control"] = "no-store"
            return response
        except ValidationError as exc:
            error = "; ".join(exc.messages)
    return render(request, "participants/historical_export.html", {"fields": EXPORT_FIELDS,
        "selected_fields": fields, "templates": templates, "error": error})
