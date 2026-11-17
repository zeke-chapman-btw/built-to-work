"""Restricted historical data tools for BTW staff."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from apps.internal_ui.people_editors import people_render as render, people_saved
from django.views.decorators.http import require_http_methods, require_POST
from .historical import (COLUMNS, FIXED, columns_spec, fixed_spec, spec_columns, EXPORT_FIELDS, IMPORT_FIELDS, IMPORT_GROUPS, guess_mapping, MAX_FILE_BYTES, MAX_ROWS, MAX_COLUMNS, _read_rows, create_batch, export_participants,
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
    return render(request, "participants/historical_index.html", {"batches": batches, "error": error, "import_fields": IMPORT_FIELDS, "import_groups": IMPORT_GROUPS,
        "import_max_mb": MAX_FILE_BYTES // (1024 * 1024), "import_max_rows": MAX_ROWS, "import_max_columns": MAX_COLUMNS})


def _posted_spec(post, field):
    choice = post.get("map_" + field, "")
    if choice == "__fixed__":
        value = post.get("fixed_" + field, "").strip()[:500]
        return fixed_spec(value) if value else ""
    if choice == "__cols__":
        columns = [c for c in post.getlist("cols_" + field) if c][:60]
        return columns_spec(columns) if columns else ""
    return choice


def _field_choice(name, label, spec):
    mode = "fixed" if spec.startswith(FIXED) else "cols" if spec.startswith(COLUMNS) else "column"
    return {"name": name, "label": label, "mode": mode, "chosen": spec if mode == "column" else "",
            "fixed": spec[len(FIXED):] if mode == "fixed" else "", "columns": spec_columns(spec) if mode == "cols" else []}


@login_required
@require_http_methods(["GET", "POST"])
def historical_map(request, batch_id):
    _require(request.user, "participants.change_historicalimportbatch")
    batch = get_object_or_404(HistoricalImportBatch, pk=batch_id)
    error = ""
    try:
        headers, rows = _read_rows(batch)
    except ValidationError as exc:
        headers, rows = [], []
        error = "; ".join(exc.messages)
    templates = HistoricalMappingTemplate.objects.filter(kind="import", created_by=request.user)
    selected = request.GET.get("template")
    template = templates.filter(pk=selected).first() if selected and selected.isdigit() else None
    mapping = template.mapping if template else (batch.mapping or guess_mapping(headers))
    if request.method == "POST" and headers:
        mapping = {field: _posted_spec(request.POST, field) for field in IMPORT_FIELDS}
        mapping = {field: spec for field, spec in mapping.items() if spec}
        try:
            preview_batch(batch, mapping, actor=request.user)
            name = request.POST.get("template_name", "").strip()[:120]
            if name:
                HistoricalMappingTemplate.objects.create(name=name, kind="import", mapping=mapping,
                                                         created_by=request.user)
            return people_saved(request,"participants:historical-batch", batch_id=batch.pk)
        except ValidationError as exc:
            error = "; ".join(exc.messages)
    first = rows[0][1] if rows else {}
    examples = [{"header": header, "example": (first.get(header) or "")[:40]} for header in headers]
    groups = [{"title": title, "fields": [_field_choice(name, label, mapping.get(name, "")) for name, label in fields]}
              for title, fields in IMPORT_GROUPS]
    used = {column for spec in mapping.values() for column in spec_columns(spec)}
    return render(request, "participants/historical_map.html", {"batch": batch, "headers": headers, "examples": examples,
        "groups": groups, "row_count": len(rows), "unused": [h for h in headers if h not in used],
        "templates": templates, "error": error, "page_title": "Match your columns"})


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
    from .historical import IMPORT_LABELS
    everything = batch.rows.select_related("participant").order_by("row_number")
    counts = {"all": everything.count()}
    for key in ("ready", "review", "error", "imported", "skipped"):
        counts[key] = everything.filter(status=key).count()
    show = request.GET.get("show", "")
    filters = [{"key": "", "label": "All", "count": counts["all"], "chip": ""},
               {"key": "ready", "label": "Ready", "count": counts["ready"], "chip": "g"},
               {"key": "review", "label": "Needs a look", "count": counts["review"], "chip": "y"},
               {"key": "error", "label": "Problem", "count": counts["error"], "chip": "r"},
               {"key": "imported", "label": "Imported", "count": counts["imported"], "chip": "g"},
               {"key": "skipped", "label": "Skipped", "count": counts["skipped"], "chip": ""}]
    filters = [f for f in filters if f["count"] or not f["key"] or f["key"] == show]
    for f in filters:
        f["on"] = f["key"] == show
    rows = list(everything.filter(status=show) if show in counts else everything)
    for row in rows:
        data = dict(row.normalized_data or {})
        if not data.get("first_name"):  # rows with a problem weren't read; show what the file says
            raw = {k: v for k, v in (row.raw_data or {}).items() if not k.startswith("__")}
            mapping = batch.mapping or {}
            for field in ("first_name", "last_name", "email", "phone", "city", "state"):
                column = mapping.get(field)
                if column in raw:
                    data[field] = raw[column]
            if not data.get("first_name") and raw.get(mapping.get("full_name", "")):
                data["first_name"] = raw[mapping["full_name"]]
        row.shown = data
        row.details = [(IMPORT_LABELS.get(k, k.replace("_", " ").capitalize()), v) for k, v in (row.normalized_data or {}).items()
                       if v and k not in ("first_name", "last_name")]
    return render(request, "participants/historical_batch.html", {"batch": batch, "rows": rows, "counts": counts,
        "filters": filters, "error": error})


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
