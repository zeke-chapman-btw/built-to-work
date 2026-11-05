"""Employer-only manual interaction entry; current release policy on every request."""
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.customers.portal_views import customer_view, context
from apps.recruiting import employer
from apps.recruiting.access import policy_transaction

from . import access, forms, services
from .models import Interaction, InteractionRevision
from .views import errors, reject_extra


def candidate_url(disclosure_id):
    return reverse("customer_portal:recruiting-candidate", args=[disclosure_id])


def candidate_context(request, disclosure_id):
    organization_id = request.customer_member.organization_id
    candidate = employer.detail(request.user, organization_id, disclosure_id)
    return organization_id, candidate


def interaction_context(request, disclosure_id, candidate):
    rows = services.employer_history(request.user, request.customer_member.organization_id,
        disclosure_id, include_archived=request.GET.get("archived") == "1")
    return {"crm_history": Paginator(rows, 10).get_page(request.GET.get("activity_page")),
        "crm_can_record": candidate["can_respond"] and not getattr(request, "customer_preview", False),
        "crm_disclosure_id": disclosure_id, "crm_include_archived": request.GET.get("archived") == "1"}


def writable(request):
    if getattr(request, "customer_preview", False):
        raise PermissionDenied("Customer Administrator Preview is view-only.")


def authorized_record(request, disclosure_id, interaction_id, *, write=False):
    # Never authorize a record through another release, even for the same candidate/company.
    with policy_transaction():
        try:
            row = Interaction.objects.select_related("membership__identity").get(pk=interaction_id)
        except Interaction.DoesNotExist:
            raise Http404
        if str(row.disclosure_id) != str(disclosure_id):
            raise PermissionDenied("This interaction is unavailable in this candidate release.")
        access.employer_record(request.user, request.customer_member.organization_id, row, write=write)
        return row


@customer_view
@require_http_methods(["GET", "POST"])
def create(request, disclosure_id):
    writable(request)
    organization_id, candidate = candidate_context(request, disclosure_id)
    employer.customer_grant(request.user, organization_id, disclosure_id, write=True)
    form = forms.ActivityForm(request.POST if request.method == "POST" else None, employer=True,
        initial={"activity_type": request.GET.get("type", "call")})
    if request.method == "POST":
        valid = form.is_valid()
        reject_extra(form, request.POST)
        if valid and not form.errors:
            try:
                services.create_employer(actor=request.user, organization_id=organization_id,
                    disclosure_id=disclosure_id, data=form.interaction_data(), operation_id=form.cleaned_data["operation_id"])
            except (ValidationError, ObjectDoesNotExist) as exc:
                errors(form, exc)
            else:
                messages.success(request, "Interaction recorded for your company. No message was sent and no recruiting decision changed.")
                return redirect(candidate_url(disclosure_id) + "#company-interactions")
    return render(request, "crm/portal_form.html", context(request, "candidates", form=form,
        page_title="Record candidate interaction", subject=candidate["position"], back=candidate_url(disclosure_id),
        guidance="Record an action your company performed outside BTW. These records are visible to authorized members of your company and BTW staff.", submit_label="Save interaction"))


@customer_view
@require_http_methods(["GET"])
def detail(request, disclosure_id, interaction_id):
    _, candidate = candidate_context(request, disclosure_id)
    row = authorized_record(request, disclosure_id, interaction_id)
    item = services.employer_projection(row)
    member, grant = employer.customer_grant(request.user, request.customer_member.organization_id, disclosure_id)
    item["can_correct"] = (member.role == "lead" or row.created_by_id == request.user.pk) and employer.may_respond(member, grant) and not getattr(request, "customer_preview", False)
    # Explicit public fields only. Do not pass internal snapshots, Tasks or Participant models.
    revisions = []
    for revision in InteractionRevision.objects.filter(interaction=row).select_related("actor"):
        value = revision.snapshot
        revisions.append({"number": revision.number, "action": revision.get_action_display(),
            "date": revision.recorded_at, "reason": revision.reason,
            "summary": value.get("summary", ""), "outcome": value.get("outcome", ""),
            "notes": value.get("notes", ""), "next_step": value.get("next_step", ""),
            "occurred_at": value.get("occurred_at", "")})
    return render(request, "crm/portal_detail.html", context(request, "candidates", item=item,
        crm_disclosure_id=disclosure_id, subject=candidate["position"], back=candidate_url(disclosure_id), revisions=revisions))


@customer_view
@require_http_methods(["GET", "POST"])
def correct(request, disclosure_id, interaction_id):
    writable(request)
    organization_id, candidate = candidate_context(request, disclosure_id)
    row = authorized_record(request, disclosure_id, interaction_id, write=True)
    form = forms.ActivityForm(request.POST if request.method == "POST" else None, row=row, employer=True)
    if request.method == "POST":
        valid = form.is_valid()
        reject_extra(form, request.POST)
        if valid and not form.errors:
            try:
                services.correct(actor=request.user, organization_id=organization_id, interaction_id=row.pk,
                    expected_revision=form.cleaned_data["revision"], data=form.interaction_data(), why=form.cleaned_data["reason"])
            except (ValidationError, ObjectDoesNotExist) as exc:
                errors(form, exc)
            else:
                messages.success(request, "Correction saved. The original author and earlier versions are retained.")
                return redirect("crm:employer-detail", disclosure_id=disclosure_id, interaction_id=row.pk)
    return render(request, "crm/portal_form.html", context(request, "candidates", form=form,
        page_title="Correct company interaction", subject=candidate["position"],
        back=reverse("crm:employer-detail", args=[disclosure_id, row.pk]),
        guidance="Explain your correction. The company, candidate, release and original author cannot change.", submit_label="Save correction"))


@customer_view
@require_http_methods(["GET", "POST"])
def archive(request, disclosure_id, interaction_id, action):
    writable(request)
    if action not in {"archive", "restore"}:
        raise Http404
    organization_id, candidate = candidate_context(request, disclosure_id)
    row = authorized_record(request, disclosure_id, interaction_id, write=True)
    form = forms.ArchiveForm(request.POST if request.method == "POST" else None, initial={"revision": row.revision})
    if request.method == "POST" and form.is_valid():
        reject_extra(form, request.POST)
        if not form.errors:
            try:
                services.archive(actor=request.user, organization_id=organization_id, interaction_id=row.pk,
                    expected_revision=form.cleaned_data["revision"], why=form.cleaned_data["reason"], restore=action == "restore")
            except (ValidationError, ObjectDoesNotExist) as exc:
                errors(form, exc)
            else:
                return redirect("crm:employer-detail", disclosure_id=disclosure_id, interaction_id=row.pk)
    return render(request, "crm/portal_form.html", context(request, "candidates", form=form,
        page_title=action.title() + " company interaction", subject=candidate["position"],
        back=reverse("crm:employer-detail", args=[disclosure_id, row.pk]),
        guidance="The entry and its correction history are retained.", submit_label=action.title()))
