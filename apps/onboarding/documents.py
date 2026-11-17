"""Customers → (customer) → Documents."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.internal_ui.record_editors import editor_response, editor_success

from .forms import MAX_W9
from .models import CustomerDocument


def _org(request, org_id):
    from apps.billing.views import _org as billing_org
    return billing_org(request, org_id)


def _tab(org):
    return reverse("customers:workspace", args=[org.pk, "documents"])


def documents_context(org):
    today = timezone.localdate()
    rows = list(CustomerDocument.objects.filter(organization=org, removed_at__isnull=True).select_related("uploaded_by", "onboarding"))
    for row in rows:
        row.expired = bool(row.expires_on and row.expires_on < today)
        row.expiring = bool(row.expires_on and today <= row.expires_on <= today + timedelta(days=30))
    have = {r.kind for r in rows}
    missing = [label for kind, label in ((CustomerDocument.Kind.W9, "W-9"), (CustomerDocument.Kind.AGREEMENT, "Service agreement"))
               if kind not in have]
    return {"documents": rows, "missing_documents": missing}


class DocumentForm(forms.ModelForm):
    upload = forms.FileField(label="File", required=False, help_text="PDF, image, Word, Excel or text, up to 10 MB.")

    class Meta:
        model = CustomerDocument
        fields = ("kind", "title", "expires_on", "notes")
        labels = {"kind": "Type", "title": "Name", "expires_on": "Expires (optional)", "notes": "Notes"}
        help_texts = {"title": "Leave blank to use the type, like “W-9”.", "expires_on": "For things like insurance certificates. The account manager is reminded before it expires."}
        widgets = {"expires_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(["upload", "kind", "title", "expires_on", "notes"])
        self.fields["title"].required = False
        if not self.instance.pk:
            self.fields["upload"].required = True

    def clean_upload(self):
        upload = self.cleaned_data.get("upload")
        if upload:
            if upload.size > MAX_W9:
                raise forms.ValidationError("That file is over 10 MB.")
            if not upload.name.lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx", ".xls", ".xlsx", ".txt")):
                raise forms.ValidationError("Upload a PDF, image, Word, Excel or text file.")
        return upload


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def edit(request, org_id, pk=None):
    org = _org(request, org_id)
    row = get_object_or_404(CustomerDocument, pk=pk, organization=org, removed_at__isnull=True) if pk else CustomerDocument(organization=org)
    form = DocumentForm(request.POST or None, request.FILES or None, instance=row,
                        initial=None if pk else {"kind": request.GET.get("kind") or "other"})
    if request.method == "POST" and request.POST.get("remove") == "1" and row.pk:
        row.removed_at = timezone.now()
        row.save(update_fields=["removed_at"])
        messages.success(request, f"{row.title} removed.")
        return editor_success(request, _tab(org))
    if request.method == "POST" and form.is_valid():
        doc = form.save(commit=False)
        upload = form.cleaned_data.get("upload")
        if upload:
            doc.file_name = upload.name[:200]
            doc.file.save(f"{org.pk}-{upload.name}"[:100], upload, save=False)
        if not doc.title:
            doc.title = doc.get_kind_display()
        if not doc.pk:
            doc.uploaded_by = request.user
        doc.save()
        messages.success(request, f"{doc.title} saved.")
        return editor_success(request, _tab(org))
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
                           context={"form": form, "page_title": "Edit document" if row.pk else "Upload document",
                                    "editor_context": org.name, "save_label": "Save", "back": _tab(org), "back_label": org.name,
                                    "fields_template": "onboarding/document_fields.html", "document": row})


@login_required(login_url="/admin/login/")
def download(request, org_id, pk):
    org = _org(request, org_id)
    row = get_object_or_404(CustomerDocument, pk=pk, organization=org)
    if row.file:
        return FileResponse(row.file.open("rb"), as_attachment=request.GET.get("view") != "1",
                            filename=row.file_name or f"{row.title}.pdf")
    if row.kind == "agreement" and row.onboarding_id:
        return render(request, "onboarding/signed_agreement.html", {"row": row.onboarding, "org": org})
    raise Http404
