from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from apps.events.models import Event
from .template_models import OperationsTemplate, OperationsTemplateRevision, EventOperationsApplication
from .template_forms import TemplateDraftForm, ApplyTemplateForm, ConfirmTemplateActionForm
from . import template_services as svc


def _revision(revision_id):
    return get_object_or_404(OperationsTemplateRevision.objects.select_related("template"), pk=revision_id)

def _resource_rows(revision):
    if revision.status == "published": return revision.snapshot["resources"]
    result = []
    for row in revision.resources.select_related("question_set__category", "station", "game"):
        label = str(row.question_set) if row.kind == "assessment" else str(row.station) if row.kind == "station" else str(row.game) if row.kind == "game" else "Registration grouping/capacity"
        result.append({"kind": row.kind, "value": {"label": label, **row.options}})
    return result

@login_required(login_url="/admin/login/")
@require_http_methods(["GET"])
def template_list(request):
    svc.require(request.user, "operations.view_operationstemplate")
    templates = OperationsTemplate.objects.prefetch_related("revisions").all()
    return render(request, "operations_templates/list.html", {"page_title": "Operations Templates", "templates": templates, "can_manage": svc.can_manage(request.user)})

@login_required(login_url="/admin/login/")
@require_http_methods(["GET"])
def template_detail(request, revision_id):
    svc.require(request.user, "operations.view_operationstemplate")
    revision = _revision(revision_id)
    return render(request, "operations_templates/detail.html", {"page_title": revision.name, "revision": revision,
        "resources": _resource_rows(revision), "can_manage": svc.can_manage(request.user)})

@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def template_edit(request, revision_id=None):
    svc.require_manage(request.user)
    revision = _revision(revision_id) if revision_id else None
    if revision and (revision.status != "draft" or revision.template.retired_at):
        raise PermissionDenied("Published or retired template revisions cannot be edited.")
    form = TemplateDraftForm(request.POST if request.method == "POST" else None, initial=svc.draft_initial(revision) if revision else None)
    if request.method == "POST" and form.is_valid():
        try:
            saved = svc.save_draft(actor=request.user, values=form.cleaned_data, revision=revision)
            messages.success(request, "Template draft saved.")
            return redirect("operations:template-detail", revision_id=saved.pk)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, exc if isinstance(exc, ValidationError) else "A concurrent change prevented saving. Reload and review the draft.")
    return render(request, "operations_templates/edit.html", {"page_title": "Edit template draft" if revision else "New Operations Template", "form": form, "revision": revision}, status=400 if form.is_bound else 200)

@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def template_action(request, revision_id, action):
    svc.require_manage(request.user)
    revision = _revision(revision_id)
    labels = {"publish": "Publish revision", "revise": "Create new revision", "duplicate": "Duplicate template", "retire": "Retire template"}
    if action not in labels: raise PermissionDenied
    claim = {"actor": str(request.user.pk), "revision": str(revision.pk), "action": action, "version": revision.updated_at.isoformat()}
    form = ConfirmTemplateActionForm(request.POST if request.method == "POST" else None, initial={"token": signing.dumps(claim, salt="ops-template-action")})
    if request.method == "POST" and form.is_valid():
        try:
            token = signing.loads(form.cleaned_data["token"], salt="ops-template-action", max_age=1800)
            if token != claim: raise ValidationError("This revision changed or the confirmation expired. Review it again.")
            if action == "publish": saved = svc.publish(actor=request.user, revision=revision, expected_version=token["version"])
            elif action == "retire": svc.retire(actor=request.user, template=revision.template); saved = revision
            else: saved = svc.copy_revision(actor=request.user, revision=revision, duplicate=action=="duplicate")
            messages.success(request, labels[action]+" completed.")
            return redirect("operations:template-detail", revision_id=saved.pk)
        except (ValidationError, signing.BadSignature, IntegrityError) as exc:
            form.add_error(None, exc if isinstance(exc, ValidationError) else "The action could not be confirmed. Reload and review the revision.")
    return render(request, "operations_templates/action.html", {"page_title": labels[action], "revision": revision,
        "action_label": labels[action], "form": form, "resources": _resource_rows(revision)}, status=400 if form.is_bound else 200)

@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def template_apply(request, event_id, revision_id):
    if not svc.can_apply(request.user): raise PermissionDenied
    event = get_object_or_404(Event, pk=event_id)
    revision = _revision(revision_id)
    error, plan = None, None
    # POST is handled before a new plan, so an exact successful retry is idempotent.
    if request.method == "POST":
        form = ApplyTemplateForm(request.POST)
        if form.is_valid():
            try:
                application, created = svc.apply_template(actor=request.user, event=event, revision=revision, token=form.cleaned_data["token"])
                messages.success(request, "Operations template applied." if created else "This application was already recorded; no changes repeated.")
                return redirect(str(event_url(event))+"?section=operations")
            except (ValidationError, IntegrityError) as exc:
                form.add_error(None, exc if isinstance(exc, ValidationError) else "Configuration changed concurrently. No partial changes were kept. Review a fresh preview.")
    else: form = ApplyTemplateForm()
    try:
        plan = svc.application_plan(actor=request.user, event=event, revision=revision)
        if request.method == "GET": form = ApplyTemplateForm(initial={"token": svc.preview_token(actor=request.user, event=event, revision=revision, plan=plan)})
    except ValidationError as exc: error = "; ".join(exc.messages)
    return render(request, "operations_templates/apply.html", {"page_title": "Apply Operations Template", "event": event,
        "section": "operations", "revision": revision, "plan": plan, "form": form, "error": error,
        "existing_state": svc.event_state(event)}, status=400 if request.method == "POST" else 200)

def event_url(event):
    from django.urls import reverse
    return reverse("events:event_detail", args=[event.pk])
