"""Copy the app's setup (forms, quizzes, consents, rates, reminders…) from one copy of the app to another.

Only setup is copied: never people, events, customers, results, pay or messages. Records keep their
IDs; a record that already exists on the receiving side (same ID, or same unique name/key) is updated.
Links to staff accounts are cleared (or set to the person doing the import) because accounts differ.
"""
from __future__ import annotations

import base64
import json

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.db import transaction
from django.utils import timezone

FORMAT = 1


def _m(label):
    return apps.get_model(label)


def _items(label, **filters):
    return list(_m(label).objects.filter(**filters))


def _name(obj):
    for attr in ("name", "title", "label", "key", "version_label", "slug"):
        value = getattr(obj, attr, None)
        if value:
            return str(value)
    return str(obj)


def _sample(text):
    return str(text).upper().startswith(("SAMPLE", "SYNTHETIC"))


# Each group: key, label, help, and a function returning [(choice_id, label, [objects…])] in save order.
def _quizzes():
    rows = []
    for c in _m("assessments.AssessmentCategory").objects.order_by("display_order", "name"):
        sets = _items("assessments.QuestionSet", category=c)
        questions = _items("assessments.Question", category=c)
        choices = _items("assessments.QuestionChoice", question__category=c)
        items = _items("assessments.QuestionSetItem", question_set__category=c)
        rows.append((str(c.pk), f"{c.name} ({len(questions)} questions)", [c] + sets + questions + choices + items))
    return rows


def _forms():
    rows = []
    for f in _m("participants.RegistrationForm").objects.order_by("pk"):
        versions = _items("participants.RegistrationFormVersion", form=f)
        questions = _items("participants.RegistrationQuestion", form=f)
        rows.append((str(f.pk), f"{_name(f)} ({len(versions)} version{'s' if len(versions) != 1 else ''})", [f] + versions + questions))
    return rows


def _each(label, order=("pk",)):
    def rows():
        return [(str(o.pk), _name(o), [o]) for o in _m(label).objects.order_by(*order)]
    return rows


def _all(label_list, text):
    def rows():
        objs = []
        for label in label_list:
            objs += list(_m(label).objects.all())
        return [("all", text(objs), objs)] if objs else []
    return rows


GROUPS = (
    ("consents", "Consent documents", _each("participants.ConsentDocumentVersion")),
    ("forms", "Sign-up forms", _forms),
    ("quizzes", "Quizzes", _quizzes),
    ("stations", "Stations", _each("stations.Station", ("name",))),
    ("games", "Games", _each("games.GameDefinition")),
    ("simulators", "Simulator capture settings", _each("simulators.SimulatorCaptureProfile")),
    ("pay", "Staff roles and pay rates", _all(["workdays.StaffRole", "workdays.CompensationRate"],
                                             lambda o: f"{sum(1 for x in o if x._meta.model_name == 'staffrole')} roles, "
                                                       f"{sum(1 for x in o if x._meta.model_name == 'compensationrate')} rates")),
    ("expenses", "Expense categories", _all(["event_reports.ExpenseCategory"], lambda o: f"{len(o)} categories")),
    ("checklist", "Event checklist (master)", _all(["operations.ChecklistMaster", "operations.MasterChecklistCategory",
                                                   "operations.MasterChecklistItem"], lambda o: f"{len(o)} items and sections")),
    ("documents", "Participant document types", _all(["participants.ParticipantDocumentCategory"], lambda o: f"{len(o)} types")),
    ("recruiting", "Recruiting stages and work classifications", _all(["recruiting.RecruitingStage", "recruiting.Classification"],
                                                                      lambda o: f"{len(o)} stages and classifications")),
    ("sops", "SOPs", _each("sops.SOP", ("title",))),
    ("templates", "Message templates", _each("messaging.MessageTemplate", ("name",))),
    ("reminders", "Reminders and reminder roles", _all(["reminders.ReminderRole", "reminders.ReminderRule"],
                                                       lambda o: f"{len(o)} reminders and roles (people aren't copied)")),
    ("onboarding", "Customer onboarding questions and service agreement",
     _all(["onboarding.OnboardingQuestion", "onboarding.AgreementVersion"], lambda o: f"{len(o)} questions and agreement versions")),
    ("legal", "Privacy Policy and Terms", _all(["security.LegalPage"], lambda o: f"{len(o)} pages")),
)


def catalog():
    """What this copy of the app has, for the export checklist."""
    result = []
    for key, label, finder in GROUPS:
        try:
            rows = finder()
        except Exception:
            rows = []
        result.append({"key": key, "label": label, "choices": [
            {"id": f"{key}:{cid}", "label": text, "count": len(objs), "sample": _sample(text)} for cid, text, objs in rows]})
    return result


def _migrations():
    from django.db.migrations.recorder import MigrationRecorder
    latest = {}
    for app, name in MigrationRecorder.Migration.objects.values_list("app", "name"):
        if name > latest.get(app, ""):
            latest[app] = name
    return latest


def export(selected):
    """JSON text for the chosen items, files included."""
    chosen = set(selected)
    objects, files = [], {}
    for key, label, finder in GROUPS:
        for cid, text, objs in finder():
            if f"{key}:{cid}" not in chosen:
                continue
            for obj in objs:
                objects.append(obj)
                for field in obj._meta.fields:
                    if field.get_internal_type() in ("FileField", "ImageField"):
                        value = getattr(obj, field.name)
                        if value and value.name and value.name not in files:
                            try:
                                with value.open("rb") as handle:
                                    files[value.name] = base64.b64encode(handle.read()).decode()
                            except (OSError, ValueError):
                                pass
    data = json.loads(serializers.serialize("json", objects))
    return json.dumps({"format": FORMAT, "made_at": timezone.now().isoformat(), "migrations": _migrations(),
                       "groups": sorted({c.split(":", 1)[0] for c in chosen}), "objects": data, "files": files})


def read(raw):
    try:
        bundle = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("That isn't a setup file from this app.")
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT or "objects" not in bundle:
        raise ValueError("That isn't a setup file from this app.")
    return bundle


def differences(bundle):
    """Apps whose database version differs. Both copies should run the same release."""
    mine = _migrations()
    theirs = bundle.get("migrations") or {}
    labels = {o["model"].split(".")[0] for o in bundle["objects"]}
    return sorted(app for app in labels if theirs.get(app) != mine.get(app))


def summary(bundle):
    counts = {}
    for o in bundle["objects"]:
        counts[o["model"]] = counts.get(o["model"], 0) + 1
    named = {}
    for key, label, _f in GROUPS:
        named[key] = label
    return {"groups": [named.get(g, g) for g in bundle.get("groups", [])], "records": len(bundle["objects"]),
            "files": len(bundle.get("files") or {}), "made_at": bundle.get("made_at", "")}


# Published versions can't be edited; if one is already here it is left as it is.
KEEP_EXISTING = {"participants.registrationformversion", "participants.consentdocumentversion"}


@transaction.atomic
def apply(bundle, actor):
    """Create or update every record in the file. Returns (created, updated)."""
    from apps.participants.intake_model_guards import contract_write
    with contract_write():
        return _apply(bundle, actor)


def _apply(bundle, actor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    remap, created, updated = {}, 0, 0
    for item in bundle["objects"]:
        model = apps.get_model(item["model"])
        fields = dict(item["fields"])
        pk = original = item.get("pk")
        for f in model._meta.fields:
            if not f.is_relation or f.name not in fields:
                continue
            if f.related_model is User:
                fields[f.name] = None if f.null else actor.pk
            elif fields[f.name] is not None:
                fields[f.name] = remap.get((f.related_model._meta.label_lower, str(fields[f.name])), fields[f.name])
        for f in model._meta.many_to_many:
            fields.pop(f.name, None)  # holders etc. are people; set them on the new app
        existing = _same_record(model, fields)
        if existing is None and pk is not None and model._meta.pk.get_internal_type() not in ("AutoField", "BigAutoField"):
            existing = model.objects.filter(pk=pk).first()  # UUIDs and slugs mean the same record everywhere
        if existing is None and model._meta.pk.get_internal_type() in ("AutoField", "BigAutoField"):
            pk = None  # numbered IDs differ between copies; let this copy number it
        if existing and model._meta.label_lower in KEEP_EXISTING:
            if str(existing.pk) != str(original):
                remap[(model._meta.label_lower, str(original))] = existing.pk
            continue
        record = {"model": item["model"], "pk": existing.pk if existing else pk, "fields": fields}
        from django.core.serializers.json import DjangoJSONEncoder
        obj = next(serializers.deserialize("json", json.dumps([record], cls=DjangoJSONEncoder), ignorenonexistent=True)).object
        if existing:
            updated += 1
        else:
            created += 1
        obj._state.adding = existing is None  # update in place instead of inserting a duplicate
        obj.save()
        if str(obj.pk) != str(original):
            remap[(model._meta.label_lower, str(original))] = obj.pk
    _restore_files(bundle.get("files") or {})
    return created, updated


def _unique_sets(model):
    from django.db.models import UniqueConstraint
    sets = [(f.name,) for f in model._meta.fields if f.unique and not f.primary_key]
    sets += [tuple(u) for u in model._meta.unique_together]
    sets += [tuple(c.fields) for c in model._meta.constraints
             if isinstance(c, UniqueConstraint) and c.fields and c.condition is None]
    return sets


NATURAL = {"onboarding.onboardingquestion": ("section", "label"), "onboarding.agreementversion": ("version",),
           "messaging.messagetemplate": ("name",), "sops.sop": ("title",)}


def _same_record(model, fields):
    """A record already here with the same unique name/key (seeded on both sides with different IDs)."""
    names = {f.name: f for f in model._meta.fields}
    extra = NATURAL.get(model._meta.label_lower)
    for group in _unique_sets(model) + ([extra] if extra else []):
        if not all(n in fields and fields[n] not in (None, "") for n in group):
            continue
        lookup = {(n + "_id" if names[n].is_relation else n): fields[n] for n in group if n in names}
        found = model.objects.filter(**lookup).first()
        if found:
            return found
    return None


def _restore_files(files):
    """Put copied files (consent PDFs, quiz pictures) in the storage each record uses."""
    if not files:
        return
    from django.core.files.base import ContentFile
    storages = []
    for label in ("participants.ConsentDocumentVersion", "assessments.Question"):
        for f in apps.get_model(label)._meta.fields:
            if f.get_internal_type() in ("FileField", "ImageField"):
                storages.append((label, f))
    for name, data in files.items():
        for label, field in storages:
            model = apps.get_model(label)
            if model.objects.filter(**{field.name: name}).exists():
                if not field.storage.exists(name):
                    field.storage.save(name, ContentFile(base64.b64decode(data)))
                break
