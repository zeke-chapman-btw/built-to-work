"""Controlled additive template application; domain records remain authoritative."""
import hashlib
import json
import uuid
from copy import deepcopy
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from apps.events.models import Event
from apps.events.services import audit_action
from apps.assessments.models import EventAssessmentCategory, QuestionSet
from apps.games.models import EventGameConfiguration
from apps.stations.models import EventStation, ExperienceSession
from apps.stations.services import assign_station
from .template_models import OperationsTemplate, OperationsTemplateRevision, OperationsTemplateResource, EventOperationsApplication, KINDS

SALT = "btw.operations.application.10b"

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

def allowed(user, *permissions):
    return bool(user.is_authenticated and user.is_active and user.is_staff and user.has_perms(permissions))

def require(user, *permissions):
    if not allowed(user, *permissions): raise PermissionDenied

def can_view_setup(user):
    return allowed(user, "events.view_event", "operations.view_event_operations_setup")

def can_manage(user):
    return allowed(user, "operations.view_operationstemplate", "operations.manage_operations_templates")

def require_manage(user):
    require(user, "operations.view_operationstemplate", "operations.manage_operations_templates")

def can_apply(user):
    return allowed(user, "events.view_event", "events.change_event", "operations.view_event_operations_setup", "operations.view_operationstemplate", "operations.apply_operations_templates")

def draft_initial(revision):
    initial = dict(name=revision.name, description=revision.description, not_applicable=revision.not_applicable,
                   version_token=revision.updated_at.isoformat())
    resources = list(revision.resources.all())
    initial["question_sets"] = [r.question_set_id for r in resources if r.kind == "assessment"]
    initial["stations"] = [r.station_id for r in resources if r.kind == "station"]
    for row in resources:
        if row.kind == "game": initial["game"] = row.game_id
        if row.kind == "registration": initial.update({"registration_"+k: v for k, v in row.options.items()})
    return initial

@transaction.atomic
def save_draft(*, actor, values, revision=None):
    require_manage(actor)
    if revision is None:
        template = OperationsTemplate.objects.create(name=values["name"], created_by=actor)
        revision = OperationsTemplateRevision(template=template, number=1, created_by=actor)
        action = "operations.template.created"
    else:
        revision = OperationsTemplateRevision.objects.select_for_update().select_related("template").get(pk=revision.pk)
        if revision.status != "draft" or revision.template.retired_at:
            raise ValidationError("Only an active template's draft can be edited.")
        if values.get("version_token") != revision.updated_at.isoformat():
            raise ValidationError("This draft changed. Reload it before saving.")
        action = "operations.template.draft_saved"
    revision.name = values["name"]
    revision.description = values.get("description", "")
    revision.not_applicable = sorted(values.get("not_applicable", []))
    revision.updated_by = actor
    revision.save()
    revision.resources.all().delete()
    for n, qs in enumerate(values.get("question_sets", [])):
        OperationsTemplateResource.objects.create(revision=revision, kind="assessment", question_set=qs, position=n)
    for n, station in enumerate(values.get("stations", [])):
        OperationsTemplateResource.objects.create(revision=revision, kind="station", station=station, position=n)
    if values.get("game"):
        OperationsTemplateResource.objects.create(revision=revision, kind="game", game=values["game"])
    if values.get("registration_group_mode"):
        OperationsTemplateResource.objects.create(revision=revision, kind="registration", options={
            "group_mode": values["registration_group_mode"], "group_label": values["registration_group_label"],
            "capacity": values.get("registration_capacity")})
    # A service caller receives the same validation as the form, even outside HTTP.
    validate_draft(revision, publishing=False)
    audit_action(actor=actor, action=action, instance=revision, source="operations_templates",
                 new_data={"template": str(revision.template_id), "revision": revision.number})
    return revision

def resource_snapshot(row):
    if row.kind == "assessment":
        qs = row.question_set
        if row.options or not qs.category.is_active or qs.status != "published" or qs.readiness()["total"] < 15:
            raise ValidationError("Choose an active, valid published question set; quiz options use the existing default category pool.")
        return {"id": str(qs.pk), "label": str(qs), "category": str(qs.category_id), "version": qs.version,
                "effective_at": str(qs.effective_at), "content_digest": digest([{"question": str(item.question_id), "member_active": item.is_active, "text": item.question.text, "difficulty": item.question.difficulty, "active": item.question.is_active, "image": str(item.question.image), "choices": list(item.question.choices.order_by("pk").values("id", "text", "is_correct", "display_order"))} for item in qs.items.select_related("question").order_by("pk")])}
    if row.kind == "station":
        if row.options or not row.station.is_active: raise ValidationError("Choose an active station. Context activation is never templated.")
        return {"id": str(row.station_id), "label": str(row.station), "type": row.station.station_type}
    if row.kind == "game":
        game = row.game
        if row.options or not game.active or game.implementation_key != "duck_hunt": raise ValidationError("Only the existing active Duck Hunt implementation is supported by templates.")
        return {"id": str(game.pk), "label": game.display_name, "version": game.implementation_version, "implementation": game.implementation_key}
    values = row.options
    if set(values) != {"group_mode", "group_label", "capacity"} or values["group_mode"] not in ("disabled", "optional", "required") or not isinstance(values["group_label"], str) or not values["group_label"].strip() or len(values["group_label"]) > 80 or (values["capacity"] is not None and (type(values["capacity"]) is not int or values["capacity"] < 1)):
        raise ValidationError("Choose valid registration grouping and capacity. Public access, consent and dates are not templated.")
    return deepcopy(values)

def validate_draft(revision, *, publishing=True):
    rows = list(revision.resources.select_related("question_set__category", "station", "game").all())
    categories = []
    for row in rows:
        row.full_clean()
        resource_snapshot(row)
        if row.kind == "assessment": categories.append(row.question_set.category_id)
    if len(categories) != len(set(categories)) or (categories and len(categories) < 2):
        raise ValidationError("Choose at least two distinct assessment categories with one published set per category.")
    if publishing and not rows: raise ValidationError("Include at least one operational resource before publishing.")
    return rows

@transaction.atomic
def publish(*, actor, revision, expected_version):
    require_manage(actor)
    revision = OperationsTemplateRevision.objects.select_for_update().select_related("template").get(pk=revision.pk)
    if revision.status != "draft" or revision.template.retired_at: raise ValidationError("Only an active draft can be published.")
    if revision.updated_at.isoformat() != expected_version: raise ValidationError("The draft changed. Review it again before publishing.")
    rows = validate_draft(revision)
    snapshot = {"name": revision.name, "description": revision.description, "revision": revision.number,
                "not_applicable": revision.not_applicable,
                "resources": [{"kind": row.kind, "position": row.position, "value": resource_snapshot(row)} for row in rows]}
    revision.snapshot, revision.digest = snapshot, digest(snapshot)
    revision.status, revision.published_at, revision.published_by = "published", timezone.now(), actor
    revision.save()
    audit_action(actor=actor, action="operations.template.published", instance=revision, source="operations_templates", new_data={"revision": revision.number, "digest": revision.digest})
    return revision

@transaction.atomic
def copy_revision(*, actor, revision, duplicate=False):
    require_manage(actor)
    revision = OperationsTemplateRevision.objects.select_related("template").get(pk=revision.pk)
    template = OperationsTemplate.objects.select_for_update().get(pk=revision.template_id)
    if not duplicate and (template.retired_at or revision.status != "published"):
        raise ValidationError("Create a new revision from a published revision of an active template.")
    if duplicate:
        template = OperationsTemplate.objects.create(name=(revision.name[:150]+" (copy)"), created_by=actor)
        number = 1
    else:
        if template.revisions.filter(status="draft").exists(): raise ValidationError("This template already has a draft; edit it first.")
        number = (template.revisions.order_by("-number").first().number)+1
    draft = OperationsTemplateRevision.objects.create(template=template, number=number,
        name=template.name if duplicate else revision.name, description=revision.description,
        not_applicable=deepcopy(revision.not_applicable), created_by=actor, updated_by=actor)
    for row in revision.resources.all():
        OperationsTemplateResource.objects.create(revision=draft, kind=row.kind, question_set_id=row.question_set_id,
            station_id=row.station_id, game_id=row.game_id, position=row.position, options=deepcopy(row.options))
    audit_action(actor=actor, action="operations.template.duplicated" if duplicate else "operations.template.revision_created", instance=draft, source="operations_templates", new_data={"source_revision": str(revision.pk), "revision": number})
    return draft

@transaction.atomic
def retire(*, actor, template):
    require_manage(actor)
    template = OperationsTemplate.objects.select_for_update().get(pk=template.pk)
    if template.retired_at: raise ValidationError("This template is already retired.")
    template.retired_at, template.retired_by = timezone.now(), actor
    template.save(update_fields=("retired_at", "retired_by", "updated_at"))
    audit_action(actor=actor, action="operations.template.retired", instance=template, source="operations_templates")
    return template

def event_state(event):
    return {"event": {"status": event.status, "start_at": str(event.start_at), "end_at": str(event.end_at),
                "updated": str(event.updated_at), "group_mode": event.group_mode, "group_label": event.group_label,
                "capacity": event.registration_capacity, "public_status": event.public_registration_status},
            "assessments": list(event.assessment_categories.order_by("pk").values("category_id", "question_set_id", "pool_mode", "enabled", "display_name", "description", "display_order")),
            "stations": list(event.station_assignments.order_by("pk").values("station_id", "enabled", "configuration", "is_active_context", "display_order")),
            "game": list(EventGameConfiguration.objects.filter(event=event).values("game_id", "settings", "configuration_version")),
            "registration_count": event.registrations.count(),
            "experience_count": ExperienceSession.objects.filter(event=event).count()}

def application_plan(*, actor, event, revision):
    if not can_apply(actor): raise PermissionDenied
    if revision.status != "published" or revision.template.retired_at: raise ValidationError("Choose a published revision of an active template.")
    if digest(revision.snapshot) != revision.digest: raise ValidationError("Revision integrity check failed.")
    conflicts, changes = [], []
    if event.status not in ("draft", "upcoming") or event.start_at <= timezone.now():
        conflicts.append("Template application is limited to future draft or upcoming events. Existing editors retain their lifecycle policies.")
    if ExperienceSession.objects.filter(event=event).exists():
        conflicts.append("This event already has experience history. Preserve its configuration and use existing authorized editors.")
    existing_application = EventOperationsApplication.objects.filter(event=event).first()
    if existing_application: conflicts.append("A template is already recorded for this event. Reapplication/update is deferred; use existing editors for event-specific changes.")
    resources = list(revision.resources.select_related("question_set__category", "station", "game"))
    if len(resources) != len(revision.snapshot.get("resources", [])): raise ValidationError("Revision resource integrity check failed.")
    for row, frozen in zip(resources, revision.snapshot["resources"]):
        try:
            current = resource_snapshot(row)
            if current != frozen["value"] or row.kind != frozen["kind"] or row.position != frozen["position"]:
                raise ValidationError("A referenced resource changed since publication. Create a new revision after reviewing it.")
        except ValidationError as exc:
            conflicts.extend(exc.messages); continue
        label, action = frozen["value"].get("label", "Registration settings"), "Add"
        if row.kind == "assessment":
            if not actor.is_superuser: raise PermissionDenied("Quiz configuration retains its existing superuser-only editing policy.")
            candidate = EventAssessmentCategory(event=event, category=row.question_set.category, question_set=row.question_set)
            try: candidate.clean()
            except ValidationError as exc: conflicts.extend(exc.messages)
            existing = event.assessment_categories.filter(category=row.question_set.category).first()
            if existing:
                if existing.question_set_id == row.question_set_id and existing.enabled and existing.pool_mode == "default": action = "Keep existing"
                else: conflicts.append("Quiz category already has different settings: "+label)
        elif row.kind == "station":
            existing = event.station_assignments.filter(station=row.station).first()
            if existing:
                if existing.enabled: action = "Keep existing"
                else: conflicts.append("Station assignment is disabled; template will not overwrite it: "+label)
        elif row.kind == "game":
            require(actor, "games.add_eventgameconfiguration")
            existing = EventGameConfiguration.objects.filter(event=event).first()
            if existing:
                if existing.game_id == row.game_id and existing.settings == {}: action = "Keep existing"
                else: conflicts.append("An event game configuration already exists with different settings.")
        elif row.kind == "registration":
            proposed = current
            existing = {"group_mode": event.group_mode, "group_label": event.group_label, "capacity": event.registration_capacity}
            if existing == proposed: action = "Keep existing"
            elif event.registrations.exists() or existing != {"group_mode": "disabled", "group_label": "Group", "capacity": None}:
                conflicts.append("Registration settings or registrations already exist. No replacement will be made.")
            else: action = "Set grouping/capacity"
        changes.append({"kind": row.kind, "label": label, "action": action, "value": current})
    for kind in revision.not_applicable:
        present = {"assessment": event.assessment_categories.exists(), "station": event.station_assignments.exists(),
                   "game": EventGameConfiguration.objects.filter(event=event).exists(),
                   "registration": event.group_mode != "disabled" or event.registration_capacity is not None or event.public_registration_status != "disabled"}[kind]
        if present: conflicts.append(dict(KINDS)[kind]+" already has configuration and cannot be marked Not Applicable.")
    state = event_state(event)
    return {"changes": changes, "conflicts": list(dict.fromkeys(conflicts)), "event_digest": digest(state), "revision_digest": revision.digest}

def preview_token(*, actor, event, revision, plan):
    return signing.dumps({"actor": str(actor.pk), "event": str(event.pk), "revision": str(revision.pk),
        "event_digest": plan["event_digest"], "revision_digest": revision.digest, "key": str(uuid.uuid4())}, salt=SALT)

@transaction.atomic
def apply_template(*, actor, event, revision, token):
    if not can_apply(actor): raise PermissionDenied
    try: claim = signing.loads(token, salt=SALT, max_age=1800)
    except signing.BadSignature: raise ValidationError("The application preview expired or is invalid. Review a fresh preview.")
    if any(claim.get(key) != str(value) for key, value in (("actor", actor.pk), ("event", event.pk), ("revision", revision.pk))):
        raise ValidationError("The preview does not match this user, event and revision.")
    event = Event.objects.select_for_update().get(pk=event.pk)
    revision = OperationsTemplateRevision.objects.select_for_update().select_related("template").get(pk=revision.pk)
    previous = EventOperationsApplication.objects.filter(event=event).first()
    if previous:
        if previous.revision_id == revision.pk and str(previous.confirmation_key) == claim["key"] and claim["revision_digest"] == revision.digest:
            # Re-check resource authority even on a retry.
            if revision.resources.filter(kind="assessment").exists() and not actor.is_superuser: raise PermissionDenied
            if revision.resources.filter(kind="game").exists(): require(actor, "games.add_eventgameconfiguration")
            return previous, False
        raise ValidationError("This event already has an applied template. No replacement was made.")
    plan = application_plan(actor=actor, event=event, revision=revision)
    if plan["event_digest"] != claim["event_digest"] or revision.digest != claim["revision_digest"]:
        raise ValidationError("Event configuration changed after preview. Review again; no changes were made.")
    if plan["conflicts"]: raise ValidationError(plan["conflicts"])
    for item in plan["changes"]:
        if item["action"] == "Keep existing": continue
        value, kind = item["value"], item["kind"]
        if kind == "assessment":
            row = EventAssessmentCategory(event=event, category_id=value["category"], question_set_id=value["id"], display_order=len(event.assessment_categories.all()))
            row.full_clean(); row.save()
            audit_action(actor=actor, action="assessment.event_config.saved", instance=row, source="operations_templates", new_data={"set_id": value["id"], "revision": str(revision.pk)})
        elif kind == "station":
            from apps.stations.models import Station
            assign_station(event=event, station=Station.objects.get(pk=value["id"]), actor=actor)
        elif kind == "game":
            row = EventGameConfiguration(event=event, game_id=value["id"], settings={}, configuration_version="ops-"+str(revision.pk))
            row.full_clean(); row.save()
            audit_action(actor=actor, action="operations.event_game.configured", instance=row, source="operations_templates", new_data={"revision": str(revision.pk)})
        elif kind == "registration":
            old = {"group_mode": event.group_mode, "capacity": event.registration_capacity}
            event.group_mode, event.group_label, event.registration_capacity = value["group_mode"], value["group_label"], value["capacity"]
            event.full_clean(); event.save(update_fields=("group_mode", "group_label", "registration_capacity", "updated_at"))
            audit_action(actor=actor, action="operations.event_registration.configured", instance=event, source="operations_templates", old_data=old, new_data={"group_mode": event.group_mode, "capacity": event.registration_capacity})
    application = EventOperationsApplication.objects.create(event=event, revision=revision, snapshot=deepcopy(revision.snapshot), confirmation_key=claim["key"], event_before_digest=claim["event_digest"], applied_by=actor)
    audit_action(actor=actor, action="operations.template.applied", instance=application, source="operations_templates",
                 new_data={"event": str(event.pk), "revision": str(revision.pk), "digest": revision.digest})
    return application, True
