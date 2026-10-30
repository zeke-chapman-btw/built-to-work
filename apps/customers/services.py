"""Authorized, locked customer workflows. Every write has a deliberate audit event."""
import uuid
from collections import Counter

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Max
from django.utils import timezone

from apps.core.audit import record_audit
from .access import require_manager
from .models import (CustomerOrganization as Org, CustomerLocation, CustomerContact,
                     CustomerRelationship, CustomerIdentity, CustomerMembership as Membership,
                     CustomerAccessPlan, Campaign, WorkforceNeed as Need,
                     WorkforceNeedRevision as Revision, CustomerAuditScope,
                     TERM_FIELD_NAMES, EDITABLE_TERM_FIELDS)
from .validation import require_submittable


def reason(value):
    value = str(value or "").strip()
    if not value:
        raise ValidationError({"reason": "A reason is required."})
    return value


def expected(record, value, label="record"):
    try:
        valid = int(value) == record.revision and str(value).strip() == str(int(value))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValidationError(f"This {label} changed. Reload and review the current version before saving.")


def fields_only(data, allowed):
    unknown = set(data) - set(allowed)
    if unknown:
        raise ValidationError("Unsupported fields: " + ", ".join(sorted(unknown)))


def locked_org(actor, org_id, revision, *, archived=False):
    actor = require_manager(actor, lock=True)
    org = Org.objects.select_for_update().get(pk=org_id)
    expected(org, revision, "organization")
    if org.status == Org.Status.ARCHIVED and not archived:
        raise ValidationError("Restore this archived organization before making changes.")
    return actor, org


def touch(record, *fields):
    record.revision += 1
    record.save(update_fields=[*fields, "revision", "updated_at"])


def audit(actor, org, action, obj, *, before=None, after=None, why, need=None, campaign=None, other_org=None):
    entry = record_audit(actor=actor, action="customer." + action, instance=obj,
                         source="customer_management", old_data=before, new_data=after, reason=reason(why))
    for scope in [org] + ([other_org] if other_org and other_org.pk != org.pk else []):
        if need and need.organization_id != scope.pk:
            raise ValidationError("Invalid Workforce Need audit scope.")
        if campaign and campaign.organization_id != scope.pk:
            raise ValidationError("Invalid Campaign audit scope.")
        CustomerAuditScope.objects.create(organization=scope, audit=entry, workforce_need=need, campaign=campaign)
    return entry


ORG_FIELDS = ("name", "legal_name", "industry", "website", "business_email", "phone", "description", "status", "primary_contact")


@transaction.atomic
def save_organization(*, actor, data, org_id=None, expected_revision=None, why):
    fields_only(data, ORG_FIELDS)
    why = reason(why)
    if org_id:
        actor, org = locked_org(actor, org_id, expected_revision)
    else:
        actor = require_manager(actor, lock=True)
        org = Org()
    if data.get("status") == Org.Status.ARCHIVED:
        raise ValidationError({"status": "Use the explicit archive action."})
    if data.get("primary_contact"):
        contact = CustomerContact.objects.filter(pk=getattr(data["primary_contact"], "pk", data["primary_contact"]), organization=org).first() if org_id else None
        if not contact:
            raise ValidationError({"primary_contact": "Choose a contact from this organization."})
        data = dict(data, primary_contact=contact)
    changed = [k for k, v in data.items() if getattr(org, k) != v]
    for key, value in data.items():
        setattr(org, key, value)
    org.revision += 1
    org.full_clean()
    org.save()
    if not org_id:
        CustomerAccessPlan.objects.create(organization=org)
    audit(actor, org, "organization.updated" if org_id else "organization.created", org,
          after={"changed_fields": changed, "status": org.status}, why=why)
    return org


@transaction.atomic
def organization_status(*, actor, org_id, expected_revision, action, why):
    actor, org = locked_org(actor, org_id, expected_revision, archived=True)
    why = reason(why)
    old = org.status
    if action == "archive" and old != Org.Status.ARCHIVED:
        org.archived_from_status = old
        org.status = Org.Status.ARCHIVED
    elif action == "restore" and old == Org.Status.ARCHIVED and org.archived_from_status in {"prospect", "active", "inactive"}:
        org.status = org.archived_from_status
        org.archived_from_status = ""
    else:
        raise ValidationError("This organization action is not available.")
    touch(org, "status", "archived_from_status")
    audit(actor, org, "organization." + action, org, before={"status": old}, after={"status": org.status}, why=why)
    return org


LOCATION_FIELDS = ("name", "kind", "address_line1", "address_line2", "city", "state", "postal_code", "country", "notes", "is_active")
CONTACT_FIELDS = ("first_name", "last_name", "title", "email", "phone", "responsibilities", "is_active")


@transaction.atomic
def save_child(*, actor, org_id, expected_org_revision, kind, data, record_id=None, expected_revision=None, why):
    actor, org = locked_org(actor, org_id, expected_org_revision)
    if kind not in {"location", "contact"}:
        raise ValidationError("Choose a supported customer record type.")
    model, allowed = {"location": (CustomerLocation, LOCATION_FIELDS), "contact": (CustomerContact, CONTACT_FIELDS)}[kind]
    fields_only(data, allowed)
    obj = model.objects.select_for_update().get(pk=record_id, organization=org) if record_id else model(organization=org)
    if record_id:
        expected(obj, expected_revision)
    changed = [k for k, v in data.items() if getattr(obj, k) != v]
    for key, value in data.items():
        setattr(obj, key, value)
    obj.revision += 1
    obj.full_clean()
    obj.save()
    touch(org)
    audit(actor, org, kind + (".updated" if record_id else ".created"), obj, after={"changed_fields": changed, "active": obj.is_active}, why=why)
    return obj


@transaction.atomic
def save_relationship(*, actor, org_id, target_id, kind, active, expected_org_revision, why):
    actor = require_manager(actor, lock=True)
    records = {o.pk: o for o in Org.objects.select_for_update().filter(pk__in=[org_id, target_id]).order_by("pk")}
    try:
        org, target = records[uuid.UUID(str(org_id))], records[uuid.UUID(str(target_id))]
    except (KeyError, ValueError):
        raise ValidationError("Choose two existing organizations.")
    expected(org, expected_org_revision, "organization")
    if org.pk == target.pk or org.status == "archived" or (active and target.status == "archived"):
        raise ValidationError("Choose a different, available organization.")
    if kind not in {"parent", "division", "connected"}:
        raise ValidationError({"kind": "Choose a valid relationship."})
    if active and kind != "connected":
        seen, pending = set(), [target.pk]
        while pending:
            key = pending.pop()
            if key == org.pk:
                raise ValidationError("A parent/division relationship cannot create a cycle.")
            if key not in seen:
                seen.add(key)
                pending.extend(CustomerRelationship.objects.filter(source_id=key, kind__in=["parent", "division"], is_active=True).values_list("target_id", flat=True))
    obj, created = CustomerRelationship.objects.get_or_create(source=org, target=target, kind=kind, defaults={"is_active": active})
    obj.is_active = active
    touch(obj, "is_active")
    touch(org)
    touch(target)
    audit(actor, org, "relationship.created" if created else "relationship.updated", obj,
          after={"kind": kind, "active": active, "target_id": str(target.pk)}, why=why, other_org=target)
    return obj


def seat_usage(org):
    counts = Counter(Membership.objects.filter(organization=org, state="enabled").values_list("role", flat=True))
    return {"lead": counts["lead"], "member": counts["member"]}


def capacity(plan, before, after):
    for role in ("lead", "member"):
        limit = getattr(plan, role + "_limit")
        if after[role] > before[role] and (limit is None or after[role] > limit):
            raise ValidationError({"role": f"The {role.title()} allowance is unconfigured or has no remaining capacity. Configure Access & Plan or use a seat-neutral transfer."})


PLAN_FIELDS = ("lead_limit", "member_limit", "workforce_need_creation", "candidate_matching", "directory_search", "expanded_candidate_profile", "authorized_candidate_contact")


@transaction.atomic
def save_access_plan(*, actor, org_id, expected_org_revision, expected_revision, data, why):
    actor, org = locked_org(actor, org_id, expected_org_revision)
    fields_only(data, PLAN_FIELDS)
    plan = CustomerAccessPlan.objects.select_for_update().get(organization=org)
    expected(plan, expected_revision, "Access & Plan")
    before = {k: getattr(plan, k) for k in data}
    for key, value in data.items():
        setattr(plan, key, value)
    plan.revision += 1
    plan.full_clean()
    plan.save()
    touch(org)
    audit(actor, org, "access_plan.updated", plan, before=before, after={k: getattr(plan, k) for k in data}, why=why)
    return plan


IDENTITY_FIELDS = ("first_name", "last_name", "email", "phone", "title")


@transaction.atomic
def _prepare_identity(data):
    fields_only(data, IDENTITY_FIELDS)
    email = str(data.get("email", "")).strip().casefold()
    User = get_user_model()
    if CustomerIdentity.objects.filter(email__iexact=email).exists():
        raise ValidationError({"email": "Select the existing prepared customer identity explicitly; matching emails are not merged."})
    if email and User.objects.filter(email__iexact=email).exists():
        raise ValidationError({"email": "This email needs identity review; no staff or participant account was converted."})
    user = User(username="customer-" + uuid.uuid4().hex, is_active=False, is_staff=False, is_superuser=False, email="")
    user.set_unusable_password()
    user.save()
    obj = CustomerIdentity(user=user, **dict(data, email=email))
    obj.full_clean()
    obj.save()
    return obj


@transaction.atomic
def save_membership(*, actor, org_id, expected_org_revision, role, state="enabled", identity_id=None,
                    identity_data=None, contact_id=None, membership_id=None, expected_revision=None,
                    designate_primary=False, why):
    actor, org = locked_org(actor, org_id, expected_org_revision)
    why = reason(why)
    if role not in {"lead", "member"} or state not in {"enabled", "suspended", "removed"}:
        raise ValidationError("Choose a valid membership role and state.")
    old = Membership.objects.select_for_update().get(pk=membership_id, organization=org) if membership_id else None
    if not old and getattr(org.access_plan, role + "_limit") is None:
        raise ValidationError({"role": "Configure this role's allowance before adding a membership, including a suspended membership."})
    if old:
        expected(old, expected_revision, "membership")
        if identity_data or (identity_id and str(identity_id) != str(old.identity_id)):
            raise ValidationError("Membership edits cannot replace a person's identity.")
        identity = old.identity
    elif identity_id and not identity_data:
        identity = CustomerIdentity.objects.select_for_update().get(pk=identity_id)
        identity.full_clean()
    elif identity_data and not identity_id:
        try:
            identity = _prepare_identity(identity_data)
        except IntegrityError:
            raise ValidationError({"email": "This identity was prepared concurrently. Reload and explicitly select it."})
    else:
        raise ValidationError("Explicitly choose a prepared identity or prepare a new one.")
    if not old and Membership.objects.filter(organization=org, identity=identity).exists():
        raise ValidationError("This identity already has a membership here. Edit or restore that membership.")
    contact = CustomerContact.objects.get(pk=contact_id, organization=org) if contact_id else None
    if old and org.primary_lead_id == old.pk and (role != "lead" or state != "enabled"):
        raise ValidationError("Replace the Primary Lead before changing this role or suspending/removing this membership.")
    if org.provisioned and designate_primary:
        raise ValidationError("Use the explicit Primary Lead replacement workflow.")
    if not org.provisioned and state == "enabled" and (role != "lead" or not designate_primary):
        raise ValidationError("Provision the organization with an explicitly designated enabled Primary Lead first.")
    before = seat_usage(org)
    after = dict(before)
    if old and old.state == "enabled":
        after[old.role] -= 1
    if state == "enabled":
        after[role] += 1
    capacity(org.access_plan, before, after)
    obj = old or Membership(organization=org, identity=identity)
    prior = {"role": obj.role, "state": obj.state} if old else None
    obj.role, obj.state, obj.contact = role, state, contact
    obj.revision += 1
    obj.full_clean()
    obj.save()
    if not org.provisioned and state == "enabled":
        org.provisioned = True
        org.primary_lead = obj
        touch(org, "provisioned", "primary_lead")
        audit(actor, org, "primary_lead.designated", org, after={"membership_id": str(obj.pk)}, why=why)
    else:
        touch(org)
    audit(actor, org, "membership.updated" if old else "membership.created", obj, before=prior,
          after={"role": obj.role, "state": obj.state, "identity_id": str(identity.pk)}, why=why)
    return obj


@transaction.atomic
def transfer_primary(*, actor, org_id, expected_org_revision, target_id, previous_role="lead", why):
    actor, org = locked_org(actor, org_id, expected_org_revision)
    why = reason(why)
    if not org.provisioned or str(org.primary_lead_id) == str(target_id) or previous_role not in {"lead", "member"}:
        raise ValidationError("Choose another enabled membership and an explicit role for the previous Primary Lead.")
    rows = {str(m.pk): m for m in Membership.objects.select_for_update().filter(organization=org, pk__in=[org.primary_lead_id, target_id]).order_by("pk")}
    old, new = rows.get(str(org.primary_lead_id)), rows.get(str(target_id))
    if not old or not new or new.state != "enabled":
        raise ValidationError("Choose an enabled membership from this organization.")
    before, after = seat_usage(org), seat_usage(org)
    after[old.role] -= 1
    after[new.role] -= 1
    after[previous_role] += 1
    after["lead"] += 1
    capacity(org.access_plan, before, after)
    # Promote, move the pointer, then explicitly change the old role. Intermediate
    # Primary Lead states remain valid on SQLite as well as deferred PostgreSQL.
    new.role = "lead"
    touch(new, "role")
    org.primary_lead = new
    touch(org, "primary_lead")
    old.role = previous_role
    touch(old, "role")
    audit(actor, org, "primary_lead.replaced", org,
          before={"membership_id": str(old.pk)},
          after={"membership_id": str(new.pk), "previous_lead_role": previous_role}, why=why)
    return org


@transaction.atomic
def update_identity(*, actor, org_id, expected_org_revision, identity_id, expected_revision, data, why):
    actor = require_manager(actor, lock=True)
    identity = CustomerIdentity.objects.select_for_update().get(pk=identity_id)
    expected(identity, expected_revision, "customer identity")
    org_ids = list(identity.memberships.values_list("organization_id", flat=True))
    orgs = list(Org.objects.select_for_update().filter(pk__in=org_ids).order_by("pk"))
    org = next((o for o in orgs if str(o.pk) == str(org_id)), None)
    if not org or org.status == "archived":
        raise ValidationError("Edit this identity from an available organization membership.")
    expected(org, expected_org_revision, "organization")
    fields_only(data, IDENTITY_FIELDS)
    changed = [k for k, v in data.items() if getattr(identity, k) != v]
    for key, value in data.items():
        setattr(identity, key, value)
    identity.full_clean()
    touch(identity, *data)
    for scope in orgs:
        touch(scope)
        audit(actor, scope, "identity.updated", identity, after={"changed_fields": changed}, why=why)
    return identity


def snapshot_references(rev):
    if rev.location_mode == "location" and rev.location_id:
        location = CustomerLocation.objects.get(pk=rev.location_id, organization_id=rev.organization_id)
        for key in ("address_line1", "address_line2", "city", "postal_code", "country"):
            setattr(rev, key, getattr(location, key))
        rev.location_state = location.state
        rev.location_name = location.name
    elif rev.location_mode != "location":
        rev.location = None
    if rev.primary_contact_id:
        contact = CustomerContact.objects.get(pk=rev.primary_contact_id, organization_id=rev.organization_id)
        rev.contact_name, rev.contact_email, rev.contact_phone = str(contact), contact.email, contact.phone


def lock_need(org, need_id, revision):
    need = Need.objects.select_for_update(of=("self",)).select_related("effective_revision", "working_revision").get(pk=need_id, organization=org)
    expected(need, revision, "Workforce Need")
    return need


@transaction.atomic
def save_hiring_draft(*, actor, org_id, expected_org_revision, data, need_id=None, expected_need_revision=None, expected_working_id=None, why):
    actor, org = locked_org(actor, org_id, expected_org_revision)
    fields_only(data, EDITABLE_TERM_FIELDS)
    why = reason(why)
    if need_id:
        need = lock_need(org, need_id, expected_need_revision)
        if need.status in {"closed", "cancelled"}:
            raise ValidationError("This Need is closed/cancelled; its hiring history is retained.")
        if str(need.working_revision_id or "") != str(expected_working_id or ""):
            raise ValidationError("The working revision changed. Reload before editing.")
    else:
        if org.access_plan.workforce_need_creation is not True:
            raise ValidationError("Enable Workforce Need creation in Access & Plan before creating a Need.")
        need = Need.objects.create(organization=org)
    rev = need.working_revision
    if rev and rev.state != "draft":
        raise ValidationError("Submitted hiring terms are frozen. Return or withdraw that revision before preparing another draft.")
    if not rev:
        number = (need.revisions.aggregate(n=Max("number"))["n"] or 0) + 1
        source = need.revisions.order_by("-number").first() or need.effective_revision
        initial = {name: getattr(source, name) for name in TERM_FIELD_NAMES} if source else {}
        rev = Revision(organization=org, need=need, number=number, **initial)
    for key, value in data.items():
        if key in {"location", "primary_contact"} and value:
            model = CustomerLocation if key == "location" else CustomerContact
            value = model.objects.get(pk=getattr(value, "pk", value), organization=org)
        setattr(rev, key, value)
    snapshot_references(rev)
    rev.full_clean()
    rev.save()
    need.working_revision = rev
    touch(need, "working_revision")
    touch(org)
    audit(actor, org, "need.draft_saved", rev,
          after={"revision_id": str(rev.pk), "number": rev.number, "changed_fields": sorted(data)}, why=why, need=need)
    return need


@transaction.atomic
def revision_action(*, actor, org_id, need_id, expected_org_revision, expected_need_revision, revision_id, action, why):
    actor, org = locked_org(actor, org_id, expected_org_revision)
    need = lock_need(org, need_id, expected_need_revision)
    why = reason(why)
    if str(need.working_revision_id or "") != str(revision_id):
        raise ValidationError("Review the actual current working/submitted revision.")
    rev = Revision.objects.select_for_update().get(pk=revision_id, need=need, organization=org)
    old_state = rev.state
    if need.status == "cancelled" and action != "withdraw":
        raise ValidationError("A cancelled Need cannot resume review; its pending revision may be withdrawn.")
    if action == "submit" and rev.state == "draft":
        snapshot_references(rev)
        require_submittable(rev)
        rev.state, rev.submitted_at, rev.submitted_by = "submitted", timezone.now(), actor
        if not need.effective_revision_id:
            need.status = "submitted"
    elif action == "review" and rev.state == "submitted":
        rev.state, rev.reviewed_by = "review", actor
        if not need.effective_revision_id:
            need.status = "review"
    elif action == "approve" and rev.state == "review" and need.status != "cancelled":
        rev.state, rev.approved_at, rev.approved_by = "approved", timezone.now(), actor
        # Clear the pending pointer before approval for immediate SQLite guards.
        need.working_revision = None
        need.save(update_fields=["working_revision", "updated_at"])
        need.effective_revision = rev
        if need.status not in {"hold", "closed"}:
            need.status = "active"
    elif (action == "return" and rev.state in {"submitted", "review"}) or (action == "withdraw" and rev.state in {"submitted", "review", "draft"}):
        rev.state = "returned" if action == "return" else "withdrawn"
        need.working_revision = None
        need.save(update_fields=["working_revision", "updated_at"])
        if not need.effective_revision_id and need.status != "cancelled":
            need.status = "draft"
    else:
        raise ValidationError("That revision transition is not available.")
    rev.decision_reason = why
    rev.save()
    touch(need, "working_revision", "effective_revision", "status")
    touch(org)
    audit(actor, org, "need.revision_" + action, rev,
          before={"state": old_state}, after={"state": rev.state, "revision_id": str(rev.pk), "number": rev.number}, why=why, need=need)
    return need


@transaction.atomic
def need_status(*, actor, org_id, need_id, expected_org_revision, expected_need_revision, action, why):
    actor, org = locked_org(actor, org_id, expected_org_revision)
    need = lock_need(org, need_id, expected_need_revision)
    why = reason(why)
    old = need.status
    changed = ["status"]
    if action == "hold" and old == "active":
        need.status = "hold"
    elif action == "resume" and old == "hold" and need.effective_revision_id:
        need.status = "active"
    elif action == "close" and old in {"active", "hold"}:
        need.status, need.closed_by, need.closed_at, need.closure_reason = "closed", actor, timezone.now(), why
        changed += ["closed_by", "closed_at", "closure_reason"]
    elif action == "cancel" and old not in {"closed", "cancelled"}:
        need.status = "cancelled"
    else:
        raise ValidationError("That Workforce Need status transition is not available.")
    touch(need, *changed)
    touch(org)
    audit(actor, org, "need." + action, need, before={"status": old}, after={"status": need.status}, why=why, need=need)
    return need


@transaction.atomic
def update_need_administration(*, actor, org_id, need_id, expected_org_revision, expected_need_revision, data, why):
    actor, org = locked_org(actor, org_id, expected_org_revision)
    need = lock_need(org, need_id, expected_need_revision)
    fields_only(data, ("internal_reference", "internal_note"))
    for key, value in data.items():
        setattr(need, key, value)
    need.full_clean()
    touch(need, *data)
    touch(org)
    audit(actor, org, "need.administrative_correction", need, after={"changed_fields": sorted(data)}, why=why, need=need)
    return need


CAMPAIGN_FIELDS = ("name", "description", "objectives", "start_date", "end_date", "primary_contact")


def invalidate_campaign(campaign):
    campaign.planning_version += 1
    campaign.approved_planning_version = None
    campaign.approved_by = None
    campaign.approved_at = None
    if campaign.status == "active":
        campaign.status = "hold"
    touch(campaign, "planning_version", "approved_planning_version", "approved_by", "approved_at", "status")


@transaction.atomic
def save_campaign(*, actor, org_id, expected_org_revision, data, campaign_id=None, expected_revision=None, why):
    actor, org = locked_org(actor, org_id, expected_org_revision)
    fields_only(data, CAMPAIGN_FIELDS)
    obj = Campaign.objects.select_for_update().get(pk=campaign_id, organization=org) if campaign_id else Campaign(organization=org)
    if campaign_id:
        expected(obj, expected_revision, "Campaign")
        if obj.status in {"completed", "cancelled"}:
            raise ValidationError("This Campaign is completed/cancelled; its planning history is retained.")
    data = dict(data)
    if data.get("primary_contact"):
        data["primary_contact"] = CustomerContact.objects.get(pk=getattr(data["primary_contact"], "pk", data["primary_contact"]), organization=org)
    changed = [k for k, v in data.items() if getattr(obj, k) != v]
    old_contact_name = obj.contact_name
    for key, value in data.items():
        setattr(obj, key, value)
    obj.contact_name = str(obj.primary_contact) if obj.primary_contact_id else ""
    if obj.contact_name != old_contact_name:
        changed.append("contact_name")
    if changed:
        obj.planning_version += 1
        obj.approved_planning_version, obj.approved_by, obj.approved_at = None, None, None
        if obj.status == "active":
            obj.status = "hold"
    obj.revision += 1
    obj.full_clean()
    obj.save()
    touch(org)
    audit(actor, org, "campaign.updated" if campaign_id else "campaign.created", obj,
          after={"changed_fields": changed, "planning_version": obj.planning_version, "status": obj.status}, why=why, campaign=obj)
    return obj


@transaction.atomic
def campaign_action(*, actor, org_id, campaign_id, expected_org_revision, expected_revision, action, why):
    actor, org = locked_org(actor, org_id, expected_org_revision)
    obj = Campaign.objects.select_for_update().get(pk=campaign_id, organization=org)
    expected(obj, expected_revision, "Campaign")
    why = reason(why)
    old = obj.status
    if action == "approve_activate" and old in {"planning", "hold"}:
        if not obj.description.strip() or not obj.objectives.strip():
            raise ValidationError("Provide meaningful Campaign description and objectives before approval.")
        obj.status, obj.approved_planning_version = "active", obj.planning_version
        obj.approved_by, obj.approved_at = actor, timezone.now()
    elif action == "hold" and old in {"planning", "active"}:
        obj.status = "hold"
    elif action == "planning" and old == "hold":
        obj.status, obj.approved_planning_version, obj.approved_by, obj.approved_at = "planning", None, None, None
    elif action == "complete" and old in {"active", "hold"} and obj.approved_planning_version == obj.planning_version:
        obj.status = "completed"
    elif action == "cancel" and old not in {"completed", "cancelled"}:
        obj.status = "cancelled"
    else:
        raise ValidationError("Use an available explicit Campaign action; generic status updates cannot activate it.")
    obj.full_clean()
    touch(obj, "status", "approved_planning_version", "approved_by", "approved_at")
    touch(org)
    audit(actor, org, "campaign." + action, obj, before={"status": old},
          after={"status": obj.status, "planning_version": obj.planning_version, "approved_version": obj.approved_planning_version}, why=why, campaign=obj)
    return obj


@transaction.atomic
def assign_campaign(*, actor, org_id, need_id, campaign_id, expected_org_revision, expected_need_revision, why):
    actor, org = locked_org(actor, org_id, expected_org_revision)
    need = lock_need(org, need_id, expected_need_revision)
    target_id = getattr(campaign_id, "pk", campaign_id) or None
    if str(need.campaign_id or "") == str(target_id or ""):
        raise ValidationError("Choose a different Campaign or remove the association.")
    ids = [x for x in [need.campaign_id, target_id] if x]
    campaigns = list(Campaign.objects.select_for_update().filter(organization=org, pk__in=ids).order_by("pk"))
    target = next((c for c in campaigns if str(c.pk) == str(target_id)), None)
    if target_id and not target:
        raise ValidationError({"campaign": "Choose a Campaign from this organization."})
    if any(c.status in {"completed", "cancelled"} for c in campaigns):
        raise ValidationError("Completed/cancelled Campaign scope is retained unchanged.")
    old_id = need.campaign_id
    need.campaign = target
    need.full_clean()
    touch(need, "campaign")
    for obj in campaigns:
        invalidate_campaign(obj)
        audit(actor, org, "campaign.scope_changed", obj, after={"planning_version": obj.planning_version, "status": obj.status}, why=why, campaign=obj)
    touch(org)
    audit(actor, org, "need.campaign_changed", need,
          before={"campaign_id": str(old_id) if old_id else None},
          after={"campaign_id": str(target_id) if target_id else None}, why=why, need=need)
    return need
