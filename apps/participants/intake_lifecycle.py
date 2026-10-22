"""Admin-only lifecycle over the existing registration form/version records."""
from copy import deepcopy
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.core.audit import record_audit
from .models import (RegistrationForm, RegistrationFormVersion, RegistrationQuestion,
                     QualificationCatalogEntry, IntakeFormAssignment)
from .intake_contracts import validate_contract, contract_hash, PROTECTED
from .intake_defaults import default_contract
from .intake_model_guards import contract_write


def require_admin(actor):
    if not (getattr(actor, "is_authenticated", False) and actor.is_active
            and actor.is_staff and actor.is_superuser):
        raise PermissionDenied("Only an active BTW Admin can manage intake forms.")


def _audit(actor, action, instance, **data):
    record_audit(actor=actor, action="intake." + action, instance=instance,
                 source="operations_intake", new_data=data)


def revision(version):
    return f"{version.pk}:{version.updated_at.isoformat()}:{contract_hash(version.snapshot)}"


def _sync_questions(form, snapshot):
    active = []
    for position, q in enumerate(snapshot["questions"]):
        question, _ = RegistrationQuestion.objects.update_or_create(
            form=form, canonical_key=q["key"], defaults={
                "label": q["label"], "field_type": q["type"], "help_text": q.get("help_text", ""),
                "options": q["options"], "visibility_rule": q.get("rule", {}),
                "configuration": q, "is_required": q["required"], "is_active": True,
                "is_protected": q["key"] in PROTECTED, "position": position})
        active.append(question.pk)
    form.questions.exclude(pk__in=active).update(is_active=False)


def _catalogs(snapshot):
    mappings = snapshot["qualification_mappings"]
    entries = {str(e.stable_id): e for e in QualificationCatalogEntry.objects.select_related("category").filter(
        stable_id__in=[m["catalog_uuid"] for m in mappings], is_active=True)}
    for mapping in mappings:
        entry = entries.get(str(uuid.UUID(mapping["catalog_uuid"])))
        expected = {"skills": "skill", "license_class": "license",
                    "certification_choices": "certification"}[mapping["question"]]
        if not entry or not entry.category.is_active or entry.kind != expected:
            raise ValidationError("Published mappings need an active catalog entry of the correct kind.")
        mapping["catalog_snapshot"] = {"stable_id": str(entry.stable_id), "name": entry.name,
                                       "kind": entry.kind, "category": entry.category.name}


@transaction.atomic
def create_form(*, actor, name, snapshot=None):
    require_admin(actor)
    name = (name or "").strip()
    if not name or len(name) > 160:
        raise ValidationError("Enter a form name up to 160 characters.")
    snapshot = validate_contract(snapshot if snapshot is not None else default_contract())
    form = RegistrationForm.objects.create(
        name=name, slug=(slugify(name)[:30] or "intake") + "-" + uuid.uuid4().hex[:16],
        is_active=True, is_smart_intake=True)
    with contract_write():
        version = RegistrationFormVersion.objects.create(
            form=form, version=1, snapshot=snapshot, stable_id=uuid.uuid4(),
            status="draft", created_by=actor)
        _sync_questions(form, snapshot)
    _audit(actor, "form.created", form, draft_version=1, version_id=str(version.stable_id))
    return version


@transaction.atomic
def duplicate_form(*, actor, source_version_id, name):
    require_admin(actor)
    source = RegistrationFormVersion.objects.select_for_update(of=("self",)).select_related("form").get(
        pk=source_version_id, form__is_smart_intake=True)
    version = create_form(actor=actor, name=name, snapshot=deepcopy(source.snapshot))
    _audit(actor, "form.duplicated", version.form, source_version=str(source.stable_id))
    return version


@transaction.atomic
def save_draft(*, actor, form_id, contract, expected_revision):
    require_admin(actor)
    form = RegistrationForm.objects.select_for_update(of=("self",)).get(pk=form_id, is_smart_intake=True)
    current = form.versions.order_by("-version").first()
    if not current or revision(current) != expected_revision:
        raise ValidationError("This form changed while you were editing. Reload before saving.")
    contract = validate_contract(contract)
    with contract_write():
        if current.status == "draft":
            current.snapshot = contract
            current.save(update_fields=["snapshot", "updated_at"])
        else:
            current = RegistrationFormVersion.objects.create(
                form=form, version=current.version + 1, status="draft", snapshot=contract,
                stable_id=uuid.uuid4(), created_by=actor)
        _sync_questions(form, contract)
    _audit(actor, "draft.saved", current, version=current.version, digest=contract_hash(contract))
    return current


@transaction.atomic
def publish(*, actor, version_id, expected_revision):
    require_admin(actor)
    target = RegistrationFormVersion.objects.select_related("form").get(
        pk=version_id, form__is_smart_intake=True)
    form = RegistrationForm.objects.select_for_update(of=("self",)).get(pk=target.form_id)
    version = RegistrationFormVersion.objects.select_for_update(of=("self",)).get(pk=version_id)
    if not form.is_active or version.status != "draft" or revision(version) != expected_revision:
        raise ValidationError("Publish an unchanged draft of an active form.")
    validate_contract(version.snapshot)
    _catalogs(version.snapshot)
    for mapping in version.snapshot["qualification_mappings"]:
        mapping["mapping_version"] = str(version.stable_id)
    with contract_write():
        version.status = "published"
        version.content_hash = contract_hash(version.snapshot)
        version.published_at = timezone.now()
        version.effective_at = version.published_at
        version.save(update_fields=["status", "snapshot", "content_hash", "published_at", "effective_at", "updated_at"])
    # Publishing does not change any assignment or trailer activation.
    _audit(actor, "version.published", version, version=version.version, digest=version.content_hash)
    return version


@transaction.atomic
def retire(*, actor, form_id):
    require_admin(actor)
    form = RegistrationForm.objects.select_for_update(of=("self",)).get(pk=form_id, is_smart_intake=True)
    form.is_active = False
    form.save(update_fields=["is_active", "updated_at"])
    with contract_write():
        for version in form.versions.filter(status="published"):
            version.status = "retired"
            version.save(update_fields=["status", "updated_at"])
    _audit(actor, "form.retired", form)
    # Pinned sessions and authentic queued submissions retain their original contract.
    return form


def verify_published(version, *, allow_retired=False):
    validate_contract(version.snapshot)
    if not version.stable_id or version.content_hash != contract_hash(version.snapshot):
        raise ValidationError("Intake contract integrity check failed.")
    if version.status not in ({"published", "retired"} if allow_retired else {"published"}):
        raise ValidationError("This version has not been approved and published.")
    for mapping in version.snapshot["qualification_mappings"]:
        snapshot = mapping.get("catalog_snapshot", {})
        if (mapping.get("mapping_version") != str(version.stable_id)
                or snapshot.get("stable_id") != str(uuid.UUID(mapping["catalog_uuid"]))):
            raise ValidationError("Published qualification mapping provenance is incomplete.")
    return version


@transaction.atomic
def assign(*, actor, version_id, channel, event=None, trailer=None):
    require_admin(actor)
    version = RegistrationFormVersion.objects.select_related("form").get(
        pk=version_id, form__is_smart_intake=True)
    form = RegistrationForm.objects.select_for_update(of=("self",)).get(pk=version.form_id)
    if not form.is_active:
        raise ValidationError("A retired form cannot be assigned for new registrations.")
    verify_published(version)
    candidate = IntakeFormAssignment(channel=channel, event=event, trailer=trailer,
        form=form, active_version=version, updated_by=actor)
    candidate.clean()
    previous = IntakeFormAssignment.objects.select_for_update(of=("self",)).filter(scope_key=candidate.scope_key).first()
    if previous:
        previous.form, previous.active_version = form, version
        previous.updated_by, previous.is_active = actor, True
        previous.full_clean()
        previous.save()
        assignment = previous
    else:
        candidate.full_clean()
        candidate.save()
        assignment = candidate
    _audit(actor, "form.assigned", assignment, version=str(version.stable_id),
           channel=channel, event=str(event.pk) if event else None,
           trailer=str(trailer.pk) if trailer else None)
    return assignment


def export_version(*, actor, version_id):
    require_admin(actor)
    version = RegistrationFormVersion.objects.select_related("form").get(
        pk=version_id, form__is_smart_intake=True)
    verify_published(version)
    return {"schema": "btw-intake-package-11b-v1", "form_slug": version.form.slug,
            "form_name": version.form.name, "version": version.version,
            "stable_id": str(version.stable_id), "content_hash": version.content_hash,
            "published_at": version.published_at.isoformat(), "contract": deepcopy(version.snapshot)}
