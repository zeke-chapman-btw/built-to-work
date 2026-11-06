"""Staff-only CRM screens. All writes pass through the existing policy lock."""
from functools import wraps
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.http import Http404
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.customers.access import require_manager
from apps.customers.models import CustomerContact, CustomerOrganization
from apps.participants.models import Participant

from . import access, forms, presentation, services
from .models import ContactRelationship, Interaction, InteractionRevision
from .navigation import origin, with_context


def staff_view(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        access.fresh_staff(request.user)
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response
    return wrapped


def errors(form, exc):
    if hasattr(exc, "message_dict"):
        for key, values in exc.message_dict.items():
            form.add_error(key if key in form.fields else None, values)
    else:
        form.add_error(None, getattr(exc, "messages", ["This related record is no longer available. Reload before saving."]))


def reject_extra(form, post):
    if set(post) - set(form.fields) - {"csrfmiddlewaretoken"}:
        form.add_error(None, "Only the displayed interaction details may be submitted.")


def record_for(request, interaction_id):
    try:
        return services.staff_record(request.user, interaction_id)
    except Interaction.DoesNotExist:
        raise Http404


@staff_view
@require_http_methods(["GET", "POST"])
def create(request, participant_id=None, organization_id=None):
    participant = get_object_or_404(Participant, pk=participant_id) if participant_id else None
    organization = get_object_or_404(CustomerOrganization, pk=organization_id) if organization_id else None
    access.internal_context(request.user, participant=participant, organization=organization, write=True)
    if participant and participant.archived_at or organization and organization.status == "archived":
        raise PermissionDenied("Archived relationships are read-only.")
    back = reverse("participants:staff-profile", args=[participant.pk]) + "#btw-interactions" if participant else reverse("customers:workspace", args=[organization.pk, "crm"])
    back = origin(request, participant=participant, organization=organization, fallback=back)
    initial = {"activity_type": request.GET.get("type", "call")}
    form = forms.ActivityForm(request.POST if request.method == "POST" else None,
        actor=request.user, organization=organization, initial=initial)
    if request.method == "POST":
        valid = form.is_valid()
        reject_extra(form, request.POST)
        if valid and not form.errors:
            try:
                row = services.create_internal(actor=request.user, data=form.interaction_data(),
                    operation_id=form.cleaned_data["operation_id"], participant_id=participant_id,
                    organization_id=organization_id, contact_id=getattr(form.cleaned_data.get("contact"), "pk", None),
                    engagement_id=getattr(form.cleaned_data.get("engagement"), "pk", None),
                    campaign_id=getattr(form.cleaned_data.get("campaign"), "pk", None),
                    workforce_request_id=getattr(form.cleaned_data.get("workforce_request"), "pk", None))
            except (ValidationError, ObjectDoesNotExist) as exc:
                errors(form, exc)
            else:
                messages.success(request, "Interaction recorded. No message was sent.")
                return redirect(back)
    return render(request, "crm/form.html", {"form": form, "page_title": "Log interaction",
        "subject": participant or organization, "back": back,
        "guidance": "Record a touchpoint that already happened. Only authorized BTW staff can see this history.",
        "submit_label": "Save interaction"})


@staff_view
@require_http_methods(["GET"])
def detail(request, interaction_id):
    row = record_for(request, interaction_id)
    back = origin(request, participant=row.participant, organization=row.organization, fallback=presentation.home_url(row))
    item = presentation.staff_item(request.user, row)
    item["subject_url"] = with_context(item["subject_url"], back, "crm_return_to")
    return render(request, "crm/detail.html", {"page_title": "Interaction", "item": item,
        "record": row, "back": back,
        "revisions": InteractionRevision.objects.filter(interaction=row).select_related("actor")})


@staff_view
@require_http_methods(["GET", "POST"])
def correct(request, interaction_id):
    row = record_for(request, interaction_id)
    access.internal_record(request.user, row, write=True)
    back = origin(request, participant=row.participant, organization=row.organization, fallback=reverse("crm:detail", args=[row.pk]))
    form = forms.ActivityForm(request.POST if request.method == "POST" else None, actor=request.user, row=row)
    if request.method == "POST":
        valid = form.is_valid()
        reject_extra(form, request.POST)
        if valid and not form.errors:
            try:
                services.correct(actor=request.user, interaction_id=row.pk, expected_revision=form.cleaned_data["revision"],
                    data=form.interaction_data(), why=form.cleaned_data["reason"])
            except (ValidationError, ObjectDoesNotExist) as exc:
                errors(form, exc)
            else:
                messages.success(request, "Correction saved; the original author and earlier versions are retained.")
                return redirect(back)
    return render(request, "crm/form.html", {"form": form, "page_title": "Correct interaction",
        "subject": row.participant or row.organization, "back": back,
        "submit_label": "Save correction", "guidance": "Explain the correction. Ownership and original authorship cannot change."})


@staff_view
@require_http_methods(["GET", "POST"])
def archive(request, interaction_id, action):
    if action not in {"archive", "restore"}:
        raise Http404
    row = record_for(request, interaction_id)
    access.internal_record(request.user, row, write=True)
    back = origin(request, participant=row.participant, organization=row.organization, fallback=reverse("crm:detail", args=[row.pk]))
    form = forms.ArchiveForm(request.POST if request.method == "POST" else None, initial={"revision": row.revision})
    if request.method == "POST" and form.is_valid():
        reject_extra(form, request.POST)
        if not form.errors:
            try:
                services.archive(actor=request.user, interaction_id=row.pk, expected_revision=form.cleaned_data["revision"],
                    why=form.cleaned_data["reason"], restore=action == "restore")
            except ValidationError as exc:
                errors(form, exc)
            else:
                return redirect(back)
    return render(request, "crm/form.html", {"form": form, "page_title": action.title() + " interaction",
        "subject": row.summary, "back": back, "submit_label": action.title(),
        "guidance": "The entry and its revision history are retained."})


@staff_view
@require_http_methods(["GET", "POST"])
def followup(request, interaction_id):
    row = record_for(request, interaction_id)
    access.internal_record(request.user, row, write=True)
    if row.archived_at:
        raise PermissionDenied("Restore this interaction before adding a follow-up.")
    back = origin(request, participant=row.participant, organization=row.organization, fallback=reverse("crm:detail", args=[row.pk]))
    form = forms.FollowupForm(request.POST if request.method == "POST" else None, actor=request.user,
        initial={"title": row.next_step or "Follow up: " + row.summary[:190], "due_date": row.follow_up_on,
                 "assigned_staff": row.responsible_staff_id})
    if request.method == "POST" and form.is_valid():
        try:
            task, result_form = services.create_followup(actor=request.user, interaction_id=row.pk,
                operation_id=form.cleaned_data["operation_id"],
                data={key: request.POST.get(key, "") for key in ("title", "description", "assigned_staff", "event", "due_date", "priority", "revision", "reason")})
        except (ValidationError, ObjectDoesNotExist) as exc:
            errors(form, exc)
        else:
            if task:
                messages.success(request, "Follow-up created in Tasks and linked to this interaction.")
                target = reverse("operations:task", args=[task.pk])
                return redirect(with_context(target, back, "crm_return_to") if request.GET.get("return_to") else target)
            errors(form, ValidationError(list(result_form.errors.values())))
    return render(request, "crm/form.html", {"form": form, "page_title": "Add follow-up task",
        "subject": row.summary, "back": back, "submit_label": "Create linked task",
        "guidance": "Choose who will follow up and when. The linked task will appear in the Tasks workspace."})


@staff_view
@require_http_methods(["GET", "POST"])
def contact(request, organization_id, contact_id):
    require_manager(request.user)
    person = get_object_or_404(CustomerContact.objects.select_related("organization"), pk=contact_id, organization_id=organization_id)
    organization = person.organization
    back = origin(request, organization=organization, fallback=reverse("customers:workspace", args=[organization.pk, "crm"]))
    if organization.status == "archived":
        raise PermissionDenied("Archived customer contact settings are read-only.")
    row = ContactRelationship.objects.filter(contact=person).first()
    initial = {"revision": row.revision if row else 0, "is_primary": organization.primary_contact_id == person.pk,
        **{key: getattr(row, key) if row else "unknown" for key in ("email_preference", "sms_preference", "phone_preference")}}
    if row:
        initial.update(role=row.role, preference_notes=row.preference_notes)
    form = forms.ContactForm(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        reject_extra(form, request.POST)
        if not form.errors:
            try:
                services.save_contact_relationship(actor=request.user, contact_id=person.pk,
                    data={key: form.cleaned_data[key] for key in services.CONTACT_FIELDS},
                    expected_revision=form.cleaned_data["revision"], why=form.cleaned_data["reason"])
            except ValidationError as exc:
                errors(form, exc)
            else:
                return redirect(back)
    return render(request, "crm/form.html", {"form": form, "page_title": "Contact relationship",
        "subject": person, "back": back,
        "submit_label": "Save contact settings", "guidance": "Record this contact's role and preferred contact methods. Portal access is managed separately under Users & Contacts; preferences do not record consent."})


@staff_view
@require_http_methods(["GET", "POST"])
def existing_task(request, interaction_id):
    row = record_for(request, interaction_id)
    access.internal_record(request.user, row)
    if row.archived_at:
        raise PermissionDenied("Restore the interaction before linking a Task.")
    back = origin(request, participant=row.participant, organization=row.organization, fallback=reverse("crm:detail", args=[row.pk]))
    form = forms.LinkTaskForm(request.POST if request.method == "POST" else None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        reject_extra(form, request.POST)
        if not form.errors:
            try:
                services.link_task(actor=request.user, interaction_id=row.pk, task_id=form.cleaned_data["task"].pk)
            except (ValidationError, ObjectDoesNotExist) as exc:
                errors(form, exc)
            else:
                messages.success(request, "Existing Task linked; its workflow and permissions are unchanged.")
                return redirect(back)
    return render(request, "crm/form.html", {"form": form, "page_title": "Link existing task",
        "subject": row.summary, "back": back, "submit_label": "Link task",
        "guidance": "Choose an existing accessible Task. Linking does not grant access to private interaction details."})


@staff_view
@require_http_methods(["GET"])
def recent(request):
    from django.utils.dateparse import parse_date
    from .models import ActivityType
    data = {"crm_types": ActivityType.choices}
    term = request.GET.get("q", "").strip()[:120].casefold()
    scope = request.GET.get("scope", "")
    kind = request.GET.get("crm_type", "")
    try:
        since = parse_date(request.GET.get("crm_from", "")) if request.GET.get("crm_from") else None
        until = parse_date(request.GET.get("crm_to", "")) if request.GET.get("crm_to") else None
    except ValueError:
        since = until = None
    for audience in ("btw", "employer"):
        rows = services.staff_history(request.user, audience=audience)
        rows = [row for row in rows if (not term or term in " ".join([row.summary, row.outcome, str(row.participant or row.organization or ""), str(row.need or "")]).casefold())
            and (scope != "participant" or row.participant_id)
            and (scope != "customer" or not row.participant_id)
            and (not kind or row.activity_type == kind)
            and (not since or row.occurred_at.date() >= since) and (not until or row.occurred_at.date() <= until)]
        page = Paginator(rows, 10).get_page(request.GET.get("crm_" + audience + "_page"))
        data[audience + "_items"] = [presentation.contextual_item(request, row) for row in page.object_list]
        data[audience + "_page"] = page
        params = request.GET.copy(); params.pop("crm_" + audience + "_page", None)
        data[audience + "_query"] = params.urlencode()
    return render(request, "crm/recent.html", data)
