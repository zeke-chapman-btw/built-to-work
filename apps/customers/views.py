"""Staff customer screens. GETs query only; writes use locked domain services."""
from functools import wraps
from urllib.parse import urlencode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods
from . import forms as f, services as s
from .presentation import membership_rows
from .access import require_manager, organizations
from .models import (CustomerOrganization as Org, CustomerContact, CustomerLocation,
                     CustomerRelationship, CustomerMembership as Membership, CustomerIdentity,
                     Campaign, WorkforceNeed as Need, WorkforceNeedRevision as Revision,
                     CustomerAuditScope, TERM_FIELD_NAMES)

TABS = (("overview", "Overview"), ("company", "Company Profile"), ("needs", "Workforce Needs"),
        ("campaigns", "Campaigns"), ("matches", "Matches"), ("users", "Users & Contacts"),
        ("access", "Access & Plan"), ("history", "History"))


def staff_view(view):
    @login_required
    @wraps(view)
    def guarded(request, *args, **kwargs):
        require_manager(request.user)
        return view(request, *args, **kwargs)
    return guarded


def local_return(request, value, fallback):
    if (value and value.startswith(("/internal/customers/", "/internal/campaigns/"))
            and url_has_allowed_host_and_scheme(value, allowed_hosts=set(), require_https=request.is_secure())):
        return value
    return fallback


def org_for(request, org_id):
    return get_object_or_404(organizations(request.user).select_related("primary_contact", "primary_lead__identity", "access_plan"), pk=org_id)


def workspace_url(org, tab="overview", back=""):
    url = reverse("customers:workspace", args=[org.pk, tab])
    return url + ("?" + urlencode({"return_to": back}) if back else "")


def context(request, org=None, tab="overview", **extra):
    back = local_return(request, request.GET.get("return_to") or request.POST.get("return_to"), reverse("customers:directory"))
    return dict(organization=org, active_tab=tab, back_url=back, customer_tabs=[{"key": key, "label": label, "url": workspace_url(org, key, back)} for key, label in TABS] if org else [], **extra)


def page(request, query):
    result = Paginator(query, 20).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    result.query_prefix = params.urlencode() + ("&" if params else "")
    return result


def activity(org, need=None, campaign=None):
    query = CustomerAuditScope.objects.filter(organization=org).select_related("audit__actor")
    if need:
        query = query.filter(workforce_need=need)
    if campaign:
        query = query.filter(campaign=campaign)
    return query.order_by("-audit__timestamp", "-pk")


@staff_view
@require_http_methods(["GET"])
def directory(request):
    query = organizations(request.user).select_related("primary_contact", "primary_lead__identity").annotate(need_count=Count("workforce_needs", distinct=True)).order_by("name", "pk")
    term, status, prepared = request.GET.get("q", "").strip(), request.GET.get("status", ""), request.GET.get("provisioned", "")
    if term:
        query = query.filter(Q(name__icontains=term) | Q(legal_name__icontains=term) | Q(industry__icontains=term))
    if status in Org.Status.values:
        query = query.filter(status=status)
    if prepared in {"yes", "no"}:
        query = query.filter(provisioned=prepared == "yes")
    return render(request, "customers/directory.html", context(request, rows=page(request, query), q=term, status=status, provisioned=prepared, statuses=Org.Status.choices, directory_context=request.get_full_path()))


@staff_view
@require_http_methods(["GET"])
def workspace(request, org_id, tab="overview"):
    org = org_for(request, org_id)
    if tab not in dict(TABS):
        raise Http404
    data = context(request, org, tab, seats=s.seat_usage(org))
    if tab == "overview":
        data.update(needs=org.workforce_needs.filter(status__in=["active", "hold"]).select_related("effective_revision", "working_revision")[:5], campaigns=org.campaigns.exclude(status__in=["completed", "cancelled"])[:5], recent=activity(org)[:5], pending_count=org.workforce_needs.filter(working_revision__state__in=["submitted", "review"]).count())
    elif tab == "company":
        data.update(locations=org.locations.all(), relationships=CustomerRelationship.objects.filter(Q(source=org) | Q(target=org)).select_related("source", "target"))
    elif tab in {"needs", "campaigns"}:
        query = org.workforce_needs.select_related("effective_revision", "working_revision", "campaign") if tab == "needs" else org.campaigns.all()
        term, status = request.GET.get("q", "").strip(), request.GET.get("status", "")
        choices = Need.Status.choices if tab == "needs" else Campaign.Status.choices
        if term:
            query = query.filter(Q(effective_revision__title__icontains=term) | Q(working_revision__title__icontains=term) | Q(internal_reference__icontains=term)) if tab == "needs" else query.filter(Q(name__icontains=term) | Q(description__icontains=term))
        if status in dict(choices):
            query = query.filter(status=status)
        data.update(rows=page(request, query), q=term, status=status, statuses=choices)
    elif tab == "users":
        data.update(memberships=org.memberships.select_related("identity", "contact"), membership_rows=membership_rows(org), contacts=org.contacts.all())
    elif tab == "access":
        plan = org.access_plan
        data.update(plan=plan, capabilities=[(plan._meta.get_field(name).verbose_name, "Enabled" if getattr(plan, name) is True else "Disabled" if getattr(plan, name) is False else "Unconfigured") for name in s.PLAN_FIELDS[2:]], recent=activity(org).filter(audit__action="customer.access_plan.updated")[:5])
    elif tab == "history":
        query = activity(org)
        term = request.GET.get("q", "").strip()
        if term:
            query = query.filter(Q(audit__action__icontains=term) | Q(audit__reason__icontains=term) | Q(audit__actor__username__icontains=term))
        data.update(rows=page(request, query), q=term)
    return render(request, "customers/workspace.html", data)


def add_errors(form, error):
    if hasattr(error, "message_dict"):
        for key, values in error.message_dict.items():
            if key in form.fields:
                form.add_error(key, values)
            else:
                try:
                    label = Revision._meta.get_field(key).verbose_name.capitalize()
                except Exception:
                    label = key.replace("_", " ").capitalize()
                form.add_error(None, [label + ": " + str(value) for value in values])
    else:
        form.add_error(None, error.messages)


def form_screen(request, *, org, tab, title, form_class, initial, save, instance=None, description="", success=None, review=None):
    kwargs = {"actor": request.user, "organization": org, "initial": initial}
    if instance is not None:
        kwargs["instance"] = instance
    if review:
        review = dict(review)
        review["url"] += "?" + urlencode({"return_to": initial.get("return_to", "")})
    form = form_class(request.POST if request.method == "POST" else None, **kwargs)
    if request.method == "POST" and form.is_valid():
        try:
            obj = save(form.cleaned_data)
        except ValidationError as error:
            add_errors(form, error)
        except ObjectDoesNotExist:
            form.add_error(None, "This record is no longer available in this organization. Reload before continuing.")
        else:
            messages.success(request, "Customer change saved.")
            target = success(obj) if success else workspace_url(org, tab, initial.get("return_to", ""))
            if "return_to=" not in target:
                target += ("&" if "?" in target else "?") + urlencode({"return_to": initial.get("return_to", "")})
            return redirect(target)
    return render(request, "customers/form.html", context(request, org, tab, title=title, description=description, form=form, review=review, cancel_url=workspace_url(org, tab, initial.get("return_to", "")) if org else reverse("customers:directory")))


def initials(request, org, record=None):
    return {"org_revision": org.revision if org else None, "record_revision": record.revision if record else None, "return_to": local_return(request, request.GET.get("return_to") or request.POST.get("return_to"), reverse("customers:directory"))}


@staff_view
@require_http_methods(["GET", "POST"])
def organization_editor(request, org_id=None):
    org = org_for(request, org_id) if org_id else None
    return form_screen(request, org=org, tab="company", title="Edit customer" if org else "New customer", form_class=f.OrganizationForm, instance=org or Org(), initial=initials(request, org), save=lambda d: s.save_organization(actor=request.user, org_id=org.pk if org else None, expected_revision=d["org_revision"], data={k:d[k] for k in s.ORG_FIELDS}, why=d["reason"]), success=lambda obj: workspace_url(obj, "company"))


@staff_view
@require_http_methods(["GET", "POST"])
def child_editor(request, org_id, kind, record_id=None):
    org = org_for(request, org_id)
    if kind not in {"location", "contact"}:
        raise Http404
    model, form_class, fields = (CustomerLocation, f.LocationForm, s.LOCATION_FIELDS) if kind == "location" else (CustomerContact, f.ContactForm, s.CONTACT_FIELDS)
    obj = get_object_or_404(model, organization=org, pk=record_id) if record_id else model(organization=org)
    return form_screen(request, org=org, tab="company" if kind == "location" else "users", title=("Edit " if record_id else "New ")+kind, form_class=form_class, instance=obj, initial=initials(request, org, obj), save=lambda d: s.save_child(actor=request.user, org_id=org.pk, expected_org_revision=d["org_revision"], kind=kind, data={k:d[k] for k in fields}, record_id=record_id, expected_revision=d["record_revision"], why=d["reason"]))


@staff_view
@require_http_methods(["GET", "POST"])
def relationship_editor(request, org_id):
    org = org_for(request, org_id)
    return form_screen(request, org=org, tab="company", title="Company relationship", description="Explicit relationship only; access, users and entitlements are not inherited. Choose an existing relationship and clear Active to disconnect without erasing history.", form_class=f.RelationshipForm, initial=initials(request, org), save=lambda d: s.save_relationship(actor=request.user, org_id=org.pk, target_id=d["target"].pk, kind=d["kind"], active=d["is_active"], expected_org_revision=d["org_revision"], why=d["reason"]))


@staff_view
@require_http_methods(["GET", "POST"])
def plan_editor(request, org_id):
    org = org_for(request, org_id)
    return form_screen(request, org=org, tab="access", title="Configure Access & Plan", description="Future entitlements are configuration only. They do not expose participants or enable unfinished features.", form_class=f.AccessPlanForm, instance=org.access_plan, initial=initials(request, org, org.access_plan), save=lambda d: s.save_access_plan(actor=request.user, org_id=org.pk, expected_org_revision=d["org_revision"], expected_revision=d["record_revision"], data={k:d[k] for k in s.PLAN_FIELDS}, why=d["reason"]))


@staff_view
@require_http_methods(["GET", "POST"])
def membership_editor(request, org_id, record_id=None):
    org = org_for(request, org_id)
    obj = get_object_or_404(Membership.objects.select_related("identity"), pk=record_id, organization=org) if record_id else None
    def save(d):
        return s.save_membership(actor=request.user, org_id=org.pk, expected_org_revision=d["org_revision"], membership_id=record_id, expected_revision=d["record_revision"], role=d["role"], state=d["state"], contact_id=d["contact"].pk if d.get("contact") else None, identity_id=d["identity"].pk if d.get("identity") else None, identity_data={k:d[k] for k in s.IDENTITY_FIELDS} if not obj and d["identity_mode"] == "new" else None, designate_primary=d.get("designate_primary", False), why=d["reason"])
    return form_screen(request, org=org, tab="users", title="Edit membership" if obj else "Prepare customer user", description="Preparation reserves a seat for an enabled membership. Customer access requires an active account, invitation acceptance and an eligible company membership. Preparing or editing this record does not send an invitation.", form_class=f.MembershipEditForm if obj else f.MembershipForm, instance=obj, initial=initials(request, org, obj), save=save)


@staff_view
@require_http_methods(["GET", "POST"])
def identity_editor(request, org_id, record_id):
    org = org_for(request, org_id)
    obj = get_object_or_404(CustomerIdentity.objects.filter(memberships__organization=org), pk=record_id)
    return form_screen(request, org=org, tab="users", title="Edit prepared identity", form_class=f.IdentityForm, instance=obj, initial=initials(request, org, obj), save=lambda d: s.update_identity(actor=request.user, org_id=org.pk, expected_org_revision=d["org_revision"], identity_id=obj.pk, expected_revision=d["record_revision"], data={k:d[k] for k in s.IDENTITY_FIELDS}, why=d["reason"]))


@staff_view
@require_http_methods(["GET", "POST"])
def primary_editor(request, org_id):
    org = org_for(request, org_id)
    return form_screen(request, org=org, tab="users", title="Replace Primary Lead", description="Choose an enabled membership and explicitly choose the previous Primary Lead's role. Seat-neutral swaps are allowed.", form_class=f.PrimaryLeadForm, initial=initials(request, org), save=lambda d: s.transfer_primary(actor=request.user, org_id=org.pk, expected_org_revision=d["org_revision"], target_id=d["target"].pk, previous_role=d["previous_role"], why=d["reason"]))


@staff_view
@require_http_methods(["GET", "POST"])
def organization_action(request, org_id, action):
    org = org_for(request, org_id)
    if action not in {"archive", "restore"}:
        raise Http404
    return form_screen(request, org=org, tab="company", title=action.title()+" customer", description="Only organization status changes. Memberships, Needs, Campaigns, history and connected organizations are preserved.", form_class=f.ActionForm, initial=initials(request, org), save=lambda d: s.organization_status(actor=request.user, org_id=org.pk, expected_revision=d["org_revision"], action=action, why=d["reason"]))


def revision_sections(revision):
    if not revision:
        return []
    groups = []
    for title, names in f.HIRING_SECTIONS:
        values = []
        for name in names:
            if name in {"location", "primary_contact"}:
                continue
            value = getattr(revision, name)
            if value is not None and value != "":
                display = getattr(revision, "get_"+name+"_display", None)
                values.append({"label": revision._meta.get_field(name).verbose_name, "value": display() if display else value})
        if title.startswith("Contact") and revision.contact_name:
            values[:0] = [{"label": "Customer contact", "value": revision.contact_name}, {"label": "Email", "value": revision.contact_email}, {"label": "Phone", "value": revision.contact_phone}]
        if values:
            groups.append({"title": title, "values": values})
    return groups


@staff_view
@require_http_methods(["GET"])
def need_detail(request, org_id, record_id):
    org = org_for(request, org_id)
    obj = get_object_or_404(Need.objects.select_related("effective_revision", "working_revision", "campaign", "closed_by"), organization=org, pk=record_id)
    working, actions = obj.working_revision, []
    if working:
        if obj.status not in {"closed", "cancelled"} and working.state == "draft":
            actions.append(("submit", "Submit for BTW review"))
        if obj.status != "cancelled" and working.state == "submitted":
            actions += [("review", "Start BTW review"), ("return", "Return for correction")]
        if obj.status != "cancelled" and working.state == "review":
            actions += [("approve", "Approve this revision"), ("return", "Return for correction")]
        actions.append(("withdraw", "Withdraw pending revision"))
    actions += {"active": [("hold", "Place on hold"), ("close", "Close / complete")], "hold": [("resume", "Resume approved Need"), ("close", "Close / complete")]}.get(obj.status, [])
    if obj.status not in {"closed", "cancelled"}:
        actions.append(("cancel", "Cancel Need"))
    return render(request, "customers/need.html", context(request, org, "needs", need=obj, effective_sections=revision_sections(obj.effective_revision), working_sections=revision_sections(working), actions=actions, editable=obj.status not in {"closed", "cancelled"} and (not working or working.state == "draft"), revisions=[{"revision": rev, "sections": revision_sections(rev)} for rev in obj.revisions.all()], recent=activity(org, need=obj)[:30]))


@staff_view
@require_http_methods(["GET", "POST"])
def need_editor(request, org_id, record_id=None):
    org = org_for(request, org_id)
    need = get_object_or_404(Need.objects.select_related("working_revision", "effective_revision"), pk=record_id, organization=org) if record_id else None
    working = need.working_revision if need else None
    source = working or (need.revisions.order_by("-number").first() if need else None)
    obj = working if working and working.state == "draft" else Revision(organization=org, **{name: getattr(source, name) for name in TERM_FIELD_NAMES}) if source else Revision(organization=org)
    initial = initials(request, org, need)
    initial["working_id"] = working.pk if working else None
    return form_screen(request, org=org, tab="needs", title="Edit hiring draft" if need else "New Workforce Need", description="Incomplete drafts are welcome. Every hiring term requires review before becoming effective. Saving does not submit or approve this draft.", form_class=f.HiringForm, instance=obj, initial=initial, save=lambda d: s.save_hiring_draft(actor=request.user, org_id=org.pk, expected_org_revision=d["org_revision"], need_id=record_id, expected_need_revision=d["record_revision"], expected_working_id=d["working_id"], data={k:d[k] for k in f.EDITABLE_TERM_FIELDS}, why=d["reason"]), success=lambda obj: reverse("customers:need", args=[org.pk, obj.pk]))


@staff_view
@require_http_methods(["GET", "POST"])
def need_action(request, org_id, record_id, action):
    org = org_for(request, org_id)
    obj = get_object_or_404(Need.objects.select_related("working_revision"), organization=org, pk=record_id)
    revision_actions = {"submit", "review", "approve", "return", "withdraw"}
    if action not in revision_actions | {"hold", "resume", "close", "cancel", "administration", "campaign"}:
        raise Http404
    initial = initials(request, org, obj)
    initial.update(working_id=obj.working_revision_id, internal_reference=obj.internal_reference, internal_note=obj.internal_note, campaign=obj.campaign_id)
    def save(d):
        common = dict(actor=request.user, org_id=org.pk, need_id=obj.pk, expected_org_revision=d["org_revision"], expected_need_revision=d["record_revision"], why=d["reason"])
        if action in revision_actions:
            return s.revision_action(**common, revision_id=d["working_id"], action=action)
        if action == "administration":
            return s.update_need_administration(**common, data={k:d[k] for k in ("internal_reference", "internal_note")})
        if action == "campaign":
            return s.assign_campaign(**common, campaign_id=d["campaign"])
        return s.need_status(**common, action=action)
    review = {"label": "Current working revision " + str(obj.working_revision.number), "sections": revision_sections(obj.working_revision), "url": reverse("customers:need", args=[org.pk, obj.pk])} if action in revision_actions and obj.working_revision else None
    return form_screen(request, org=org, tab="needs", title=action.replace("_", " ").title()+" Workforce Need", description="A reason is required. Review the current version before confirming. Prior approved details stay effective while changes are pending.", form_class=f.NeedAdministrationForm if action == "administration" else f.AssociationForm if action == "campaign" else f.ActionForm, initial=initial, save=save, review=review, success=lambda unused: reverse("customers:need", args=[org.pk, obj.pk]))


@staff_view
@require_http_methods(["GET"])
def campaign_directory(request):
    query = Campaign.objects.select_related("organization").all()
    term, status = request.GET.get("q", "").strip(), request.GET.get("status", "")
    if term:
        query = query.filter(Q(name__icontains=term) | Q(organization__name__icontains=term))
    if status in Campaign.Status.values:
        query = query.filter(status=status)
    return render(request, "customers/campaign_directory.html", context(request, tab="campaigns", rows=page(request, query), q=term, status=status, statuses=Campaign.Status.choices))


@staff_view
@require_http_methods(["GET"])
def campaign_detail(request, org_id, record_id):
    org = org_for(request, org_id)
    obj = get_object_or_404(Campaign, organization=org, pk=record_id)
    actions = {"planning": [("approve_activate", "Approve & activate"), ("hold", "Place on hold"), ("cancel", "Cancel")], "active": [("hold", "Place on hold"), ("complete", "Complete"), ("cancel", "Cancel")], "hold": [("approve_activate", "Approve & activate"), ("planning", "Return to planning"), ("cancel", "Cancel")]}.get(obj.status, [])
    if obj.status == "hold" and obj.approved_planning_version == obj.planning_version:
        actions.append(("complete", "Complete"))
    return render(request, "customers/campaign.html", context(request, org, "campaigns", campaign=obj, actions=actions, needs=obj.workforce_needs.select_related("effective_revision", "working_revision"), recent=activity(org, campaign=obj)[:30]))


@staff_view
@require_http_methods(["GET", "POST"])
def campaign_editor(request, org_id, record_id=None):
    org = org_for(request, org_id)
    obj = get_object_or_404(Campaign, organization=org, pk=record_id) if record_id else Campaign(organization=org)
    return form_screen(request, org=org, tab="campaigns", title="Edit Campaign" if record_id else "New Campaign", description="Planning approval is separate from approval of Workforce Needs. Planning edits invalidate approval and put an active Campaign on hold.", form_class=f.CampaignForm, instance=obj, initial=initials(request, org, obj), save=lambda d: s.save_campaign(actor=request.user, org_id=org.pk, expected_org_revision=d["org_revision"], campaign_id=record_id, expected_revision=d["record_revision"], data={k:d[k] for k in s.CAMPAIGN_FIELDS}, why=d["reason"]), success=lambda obj: reverse("customers:campaign", args=[org.pk, obj.pk]))


@staff_view
@require_http_methods(["GET", "POST"])
def campaign_action(request, org_id, record_id, action):
    org = org_for(request, org_id)
    obj = get_object_or_404(Campaign, organization=org, pk=record_id)
    if action not in {"approve_activate", "hold", "planning", "complete", "cancel"}:
        raise Http404
    review = {"label": f"Current Campaign planning · Version {obj.planning_version}", "url": reverse("customers:campaign", args=[org.pk, obj.pk]), "sections": [{"title": "Planning details", "values": [{"label": label, "value": value or "Not provided"} for label, value in [("Campaign", obj.name), ("Description", obj.description), ("Objectives", obj.objectives), ("Start", obj.start_date), ("End", obj.end_date), ("Contact snapshot", obj.contact_name)]]}]}
    return form_screen(request, org=org, tab="campaigns", title=action.replace("_", " ").title()+" Campaign", description=f"Review {obj.name}, planning version {obj.planning_version}. This does not approve any Workforce Need.", form_class=f.ActionForm, initial=initials(request, org, obj), review=review, save=lambda d: s.campaign_action(actor=request.user, org_id=org.pk, campaign_id=obj.pk, expected_org_revision=d["org_revision"], expected_revision=d["record_revision"], action=action, why=d["reason"]), success=lambda unused: reverse("customers:campaign", args=[org.pk, obj.pk]))
