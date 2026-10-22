"""Canonical Operations consent workspace; reuse the audited publication workflow."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.models import AuditLog
from .consent_admin import ConsentAdminWorkflowMixin
from .models import ConsentDocumentVersion, ConsentAcceptance
from .workspace_views import context as participant_context


def context(request, **values):
    values.setdefault("page_description", "Manage consent documents, publication and recorded acceptances.")
    return participant_context(request, **values)


def consent_page(view):
    @login_required(login_url="admin:login")
    @never_cache
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def require_manager(user):
    if not user.is_superuser:
        raise PermissionDenied


@consent_page
@require_GET
def index(request):
    if not request.user.is_superuser:
        if request.user.has_perm("participants.view_consentacceptance"):
            return redirect("participants:consent-acceptances")
        raise PermissionDenied
    documents = ConsentDocumentVersion.objects.select_related("published_by", "text_verified_by").annotate(
        acceptance_count=Count("acceptances")).order_by("key", "-created_at", "-pk")
    return render(request, "participants/consents/index.html", context(request,
        page_title="Consents", documents=documents))


class OperationsConsentWorkflow(ConsentAdminWorkflowMixin):
    model = ConsentDocumentVersion


@consent_page
def workflow(request, action, document_id=None):
    require_manager(request.user)
    if action not in {"upload", "review", "preview", "pdf", "publish"}:
        raise Http404
    allowed = ["GET"] if action in {"preview", "pdf"} else ["POST"] if action == "publish" else ["GET", "POST"]
    if request.method not in allowed:
        return HttpResponseNotAllowed(allowed)
    view = getattr(OperationsConsentWorkflow(), action + "_view")
    return view(request, document_id) if document_id is not None else view(request)


@consent_page
@require_GET
def history(request, document_id):
    require_manager(request.user)
    document = get_object_or_404(ConsentDocumentVersion, pk=document_id)
    content_type = ContentType.objects.get_for_model(ConsentDocumentVersion, for_concrete_model=False)
    actions = AuditLog.objects.filter(content_type=content_type, object_id=str(document.pk)).select_related("actor")
    versions = ConsentDocumentVersion.objects.filter(key=document.key).order_by("-created_at", "-pk")
    return render(request, "participants/consents/history.html", context(request, page_title="Consent history",
        document=document, versions=versions, actions=Paginator(actions, 25).get_page(request.GET.get("page"))))


@consent_page
@require_GET
def acceptances(request):
    if not request.user.has_perm("participants.view_consentacceptance"):
        raise PermissionDenied
    rows = ConsentAcceptance.objects.select_related("document", "participant").order_by("-accepted_at", "-pk")
    document = None
    if request.GET.get("document"):
        try:
            document_id = int(request.GET["document"])
        except (ValueError, TypeError):
            raise Http404
        document = get_object_or_404(ConsentDocumentVersion, pk=document_id)
        rows = rows.filter(document=document)
    return render(request, "participants/consents/acceptances.html", context(request,
        page_title="Consent acceptance history", document=document,
        acceptances=Paginator(rows, 25).get_page(request.GET.get("page"))))
