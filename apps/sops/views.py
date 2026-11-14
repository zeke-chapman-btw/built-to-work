"""SOPs: a reading list for staff and a library admins manage from pop-ups."""
import uuid
from functools import wraps

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.audit import record_audit
from apps.internal_ui.record_editors import editor_response, editor_success
from apps.internal_ui.views import _staff_context
from apps.operations import storage
from .access import can_manage, can_read, visible
from .formatting import render_body
from .models import SOP


def staff_only(view):
    @login_required(login_url="/admin/login/")
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_read(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def manager_only(view):
    @staff_only
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_manage(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def page(request, template, **values):
    response = render(request, template, _staff_context(request, **values))
    response["Cache-Control"] = "private, no-store"
    return response


def _snapshot(item):
    return {"title": item.title, "category": item.category, "kind": item.kind, "audience": item.audience,
            "is_active": item.is_active, "position": item.position, "pdf_name": item.pdf_name}


@staff_only
@require_http_methods(["GET"])
def index(request):
    q = request.GET.get("q", "").strip()[:120]
    items = visible(request.user)
    if q:
        items = items.filter(Q(title__icontains=q) | Q(summary__icontains=q) | Q(category__icontains=q) | Q(body__icontains=q))
    areas = sorted({(c or "General") for c in visible(request.user).values_list("category", flat=True)}, key=str.lower)
    area = request.GET.get("area", "")
    if area:
        items = items.filter(category="") if area == "General" else items.filter(category=area)
    groups = {}
    for item in items.order_by("position", "title"):
        groups.setdefault(item.category or "General", []).append(item)
    recent = list(items.order_by("-updated_at")[:3]) if not q and not area else []
    rows = sorted(items, key=lambda i: ((i.category or "General").lower(), i.position, i.title.lower()))
    return page(request, "sops/index.html", page_title="SOPs", groups=list(groups.items()), q=q, areas=areas, area=area,
                recent=recent, rows=rows, can_manage=can_manage(request.user))


@staff_only
@require_http_methods(["GET"])
def detail(request, sop_id):
    item = get_object_or_404(visible(request.user), pk=sop_id)
    return page(request, "sops/detail.html", page_title=item.title, sop=item, body=render_body(item.body),
                can_manage=can_manage(request.user))


@staff_only
@require_http_methods(["GET"])
def pdf(request, sop_id):
    item = get_object_or_404(visible(request.user), pk=sop_id, kind=SOP.Kind.PDF)
    if not item.pdf_key or not storage.uploads_available():
        raise Http404
    stream = storage.private_storage().open(item.pdf_key, "rb")
    response = FileResponse(stream, content_type="application/pdf", as_attachment=request.GET.get("download") == "1",
                            filename=item.pdf_name or "sop.pdf")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


class SOPForm(forms.ModelForm):
    pdf_file = forms.FileField(label="PDF file", required=False)

    class Meta:
        model = SOP
        fields = ("title", "category", "summary", "audience", "kind", "body")
        widgets = {"body": forms.Textarea(attrs={"rows": 10, "data-show-when": "kind=page"}),
                   "kind": forms.RadioSelect(attrs={"class": "btw-chip-choices"}),
                   "audience": forms.RadioSelect(attrs={"class": "btw-chip-choices"})}
        labels = {"body": "Steps"}
        help_texts = {"body": "Start a line with # for a heading, - for a bullet, or 1. for a numbered step."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categories = sorted({c for c in SOP.objects.exclude(category="").values_list("category", flat=True)})
        self.fields["category"].widget.attrs["list"] = "sop-categories"
        self.fields["pdf_file"].widget.attrs["data-show-when"] = "kind=pdf"
        self.categories = categories
        self.uploads = storage.uploads_available()
        if not self.uploads:
            self.fields["pdf_file"].disabled = True
            self.fields["pdf_file"].help_text = "PDF uploads turn on once private file storage is set up. Written pages work now."
        elif self.instance.pdf_name:
            self.fields["pdf_file"].help_text = f"Current file: {self.instance.pdf_name}. Choose a new file to replace it."

    def clean(self):
        cleaned = super().clean()
        kind, upload = cleaned.get("kind"), cleaned.get("pdf_file")
        if kind == SOP.Kind.PAGE and not (cleaned.get("body") or "").strip():
            self.add_error("body", "Write the steps for this SOP.")
        if kind == SOP.Kind.PDF:
            if not self.uploads:
                self.add_error("kind", "PDF uploads aren't available yet. Use a written page for now.")
            elif not upload and not self.instance.pdf_key:
                self.add_error("pdf_file", "Choose a PDF file.")
        if upload:
            try:
                filename, content_type, payload = storage.validate_upload(upload)
            except ValidationError as exc:
                self.add_error("pdf_file", exc)
            else:
                if content_type != "application/pdf":
                    self.add_error("pdf_file", "Choose a PDF file.")
                else:
                    cleaned["pdf_payload"] = (filename, payload)
        return cleaned


def _editor(request, form, *, title, back):
    return editor_response(request, template="sops/form_page.html", panel="internal/ui/record_form_panel.html", context={
        "form": form, "page_title": title, "back": back, "save_label": "Save changes", "editor_return_label": "SOP library",
        "fields_template": "sops/form_fields.html", "full_page_editor": request.headers.get("X-BTW-Editor") != "1",
        **_staff_context(request)})


@manager_only
@require_http_methods(["GET", "POST"])
def edit(request, sop_id=None):
    item = get_object_or_404(SOP, pk=sop_id) if sop_id else SOP()
    back = reverse("sops:manage")
    before = _snapshot(item) if sop_id else None
    form = SOPForm(request.POST or None, request.FILES or None, instance=item)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            item = form.save(commit=False)
            if not sop_id:
                item.created_by = request.user
                item.position = (SOP.objects.aggregate(top=Max("position"))["top"] or 0) + 1
            item.updated_by = request.user
            payload = form.cleaned_data.get("pdf_payload")
            if payload:
                filename, data = payload
                key = storage.private_storage().save(f"sops/{uuid.uuid4().hex}.pdf", ContentFile(data))
                item.pdf_key, item.pdf_name, item.pdf_size = key, filename, len(data)
            item.save()
            record_audit(actor=request.user, action="sop.updated" if sop_id else "sop.created", instance=item,
                         source="sop_library", old_data=before, new_data=_snapshot(item))
        return editor_success(request, back)
    return _editor(request, form, title="Edit SOP" if sop_id else "New SOP", back=back)


@manager_only
@require_http_methods(["GET"])
def manage(request):
    items = list(SOP.objects.order_by("-is_active", "position", "title"))
    return page(request, "sops/manage.html", page_title="SOP library", items=items,
                active=[i for i in items if i.is_active], retired=[i for i in items if not i.is_active])


@manager_only
@require_POST
def action(request, sop_id, verb):
    item = get_object_or_404(SOP, pk=sop_id)
    before = _snapshot(item)
    with transaction.atomic():
        if verb in ("retire", "restore"):
            item.is_active = verb == "restore"
            item.save(update_fields=["is_active", "updated_at"])
        elif verb in ("up", "down"):
            ordered = list(SOP.objects.filter(is_active=True).order_by("position", "title"))
            if item in ordered:
                i = ordered.index(item)
                j = i - 1 if verb == "up" else i + 1
                if 0 <= j < len(ordered):
                    ordered[i], ordered[j] = ordered[j], ordered[i]
                for n, row in enumerate(ordered, 1):
                    if row.position != n:
                        SOP.objects.filter(pk=row.pk).update(position=n)
            item.refresh_from_db()
        else:
            raise Http404
        record_audit(actor=request.user, action="sop." + verb, instance=item, source="sop_library",
                     old_data=before, new_data=_snapshot(item))
    return redirect("sops:manage")
