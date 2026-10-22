"""Explicit trailer installation and retained, portable intake envelopes.

Form downloads are never part of the automatic outbox worker.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
import uuid
from urllib import request as urlrequest
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from apps.core.audit import record_audit
from apps.events.models import OfflineEventPreparation, SyncOutboxItem
from apps.events.offline import queue_sync, reserve_ticket_number
from .models import (RegistrationForm, RegistrationFormVersion, IntakeFormAssignment,
                     TrailerIntakeActivation)
from .intake_contracts import validate_contract, contract_hash
from .intake_lifecycle import require_admin, verify_published, _sync_questions
from .intake_model_guards import contract_write

PACKAGE_SCHEMA = "btw-intake-package-11b-v1"
TRANSPORT_SCHEMA = "btw-intake-sync-11b-v1"


def portable_version(version):
    verify_published(version, allow_retired=True)
    return {"schema": PACKAGE_SCHEMA, "form_slug": version.form.slug, "form_name": version.form.name,
            "version": version.version, "stable_id": str(version.stable_id),
            "content_hash": version.content_hash, "published_at": version.published_at.isoformat(),
            "status": version.status, "contract": deepcopy(version.snapshot)}



def _install_catalog_references(contract, actor):
    """Install missing approved UUID references, never alter existing catalog rows."""
    from .models import QualificationCatalogEntry, QualificationCategory
    from django.utils.text import slugify
    for mapping in contract["qualification_mappings"]:
        snapshot = mapping.get("catalog_snapshot", {})
        stable = uuid.UUID(mapping["catalog_uuid"])
        existing = QualificationCatalogEntry.objects.filter(stable_id=stable).first()
        if existing:
            if existing.kind != snapshot.get("kind"):
                raise ValidationError("The installed catalog UUID has an incompatible kind.")
            continue
        name, category_name = snapshot.get("name"), snapshot.get("category")
        expected = {"skills": "skill", "license_class": "license", "certification_choices": "certification"}[mapping["question"]]
        if (snapshot.get("stable_id") != str(stable) or snapshot.get("kind") != expected
                or not isinstance(name, str) or not name.strip() or len(name) > 160
                or not isinstance(category_name, str) or not category_name.strip() or len(category_name) > 100):
            raise ValidationError("Published catalog reference is invalid.")
        category = QualificationCategory.objects.filter(name=category_name).first()
        if category is None:
            category = QualificationCategory.objects.create(name=category_name,
                slug=(slugify(category_name)[:70] or "intake") + "-" + hashlib.sha256(category_name.encode()).hexdigest()[:16],
                created_by=actor, updated_by=actor)
        entry = QualificationCatalogEntry(stable_id=stable, identifier="intake-" + stable.hex,
            name=name, category=category, kind=expected, is_active=category.is_active,
            created_by=actor, updated_by=actor)
        entry.full_clean()
        entry.save()

@transaction.atomic
def install_version(*, actor, preparation, package, confirmed=False, activate=True):
    require_admin(actor)
    if not confirmed:
        raise ValidationError("Explicit local Admin confirmation is required before installation.")
    preparation = OfflineEventPreparation.objects.select_for_update(of=("self",)).select_related("trailer", "event").get(
        pk=preparation.pk)
    if not isinstance(package, dict) or package.get("schema") != PACKAGE_SCHEMA:
        raise ValidationError("Unsupported intake package.")
    contract = validate_contract(package.get("contract"))
    try:
        stable_id = uuid.UUID(package["stable_id"])
        number = int(package["version"])
        published_at = parse_datetime(package["published_at"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationError("Invalid portable version identifiers.") from exc
    if (number < 1 or published_at is None or timezone.is_naive(published_at)
            or package.get("content_hash") != contract_hash(contract)
            or package.get("status") not in {"published", "retired"}):
        raise ValidationError("Published package integrity check failed.")
    if activate and package["status"] != "published":
        raise ValidationError("Retired versions can be retained for history, not activated for new intakes.")
    slug, name = package.get("form_slug"), package.get("form_name")
    if not isinstance(slug, str) or not slug or len(slug) > 100 or not isinstance(name, str) or not name.strip():
        raise ValidationError("Invalid portable form identity.")
    form = RegistrationForm.objects.filter(slug=slug).first()
    if form and not form.is_smart_intake:
        raise ValidationError("An existing legacy form owns this slug.")
    if form is None:
        form = RegistrationForm.objects.create(slug=slug, name=name[:160], is_smart_intake=True,
                                               is_active=package["status"] == "published")
    version = RegistrationFormVersion.objects.filter(stable_id=stable_id).select_related("form").first()
    collision = RegistrationFormVersion.objects.filter(form=form, version=number).first()
    if collision and collision.pk != getattr(version, "pk", None):
        raise ValidationError("The local form version has a different stable identity.")
    if version:
        if (version.form_id != form.pk or version.version != number
                or version.content_hash != package["content_hash"] or version.snapshot != contract):
            raise ValidationError("An installed immutable version conflicts with this package.")
    else:
        with contract_write():
            version = RegistrationFormVersion.objects.create(
                form=form, version=number, stable_id=stable_id, snapshot=contract,
                status=package["status"], content_hash=package["content_hash"],
                published_at=published_at, effective_at=published_at, created_by=actor)
            if not form.versions.filter(version__gt=number).exists():
                _sync_questions(form, contract)
    verify_published(version, allow_retired=True)
    _install_catalog_references(contract, actor)
    if activate:
        if not form.is_active:
            raise ValidationError("An explicitly retired local form cannot be silently reactivated.")
        assignment, _ = IntakeFormAssignment.objects.get_or_create(
            scope_key=f"trailer:{preparation.event_id}:{preparation.trailer_id}",
            defaults={"form": form, "active_version": version, "channel": "trailer",
                      "event": preparation.event, "trailer": preparation.trailer, "updated_by": actor})
        assignment.form, assignment.active_version, assignment.updated_by = form, version, actor
        assignment.is_active = True
        assignment.full_clean()
        assignment.save()
        activation, _ = TrailerIntakeActivation.objects.update_or_create(
            trailer=preparation.trailer, event=preparation.event,
            defaults={"active_version": version, "activated_by": actor, "last_success_at": timezone.now(),
                      "last_checked_at": timezone.now(), "last_error": ""})
    from apps.events.models import CentralEventDownload
    CentralEventDownload.objects.filter(event_id=preparation.event_id,
        trailer_identity=preparation.trailer.identity, status="failed").update(next_attempt_at=timezone.now())
    record_audit(actor=actor, action="intake.trailer.version_installed", instance=version,
        source="trailer_intake", new_data={"preparation": str(preparation.pk),
        "version_uuid": str(stable_id), "activated": activate})
    return version


class NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValidationError("The configured central endpoint redirected. Check its configuration.")


def fetch_cloud(*, actor, preparation, version_id=None):
    """Read only after an authenticated local Admin initiates the action."""
    require_admin(actor)
    endpoint = os.environ.get("BTW_CENTRAL_DOWNLOAD_URL", "").strip()
    token = os.environ.get("BTW_SYNC_API_TOKEN", "").strip()
    parsed = urlsplit(endpoint)
    if (not token or not parsed.hostname or parsed.username or parsed.password or parsed.fragment
            or (parsed.scheme != "https" and not (settings.DEBUG and parsed.hostname in {"localhost", "127.0.0.1"}))):
        raise ValidationError("The existing authenticated central download connection is not configured.")
    query = dict(parse_qsl(parsed.query))
    query.update(event_id=str(preparation.event_id), intake="1")
    if version_id:
        query["intake_version"] = str(uuid.UUID(str(version_id)))
    url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    req = urlrequest.Request(url, headers={"Authorization": "Bearer " + token,
                              "X-Trailer-Identity": preparation.trailer.identity, "Accept": "application/json"})
    try:
        with urlrequest.build_opener(NoRedirect).open(req, timeout=15) as response:
            if response.status != 200:
                raise ValidationError("The central intake download was not accepted.")
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValidationError("The intake package exceeds the transfer size limit.")
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise ValidationError("The central intake download failed. The previous active version is retained.") from exc
    if (not isinstance(data, dict) or data.get("event_id") != str(preparation.event_id)
            or data.get("trailer_identity") != preparation.trailer.identity):
        raise ValidationError("Central intake download scope mismatch.")
    return data


def _envelope(session, submission, actor, *, registration=None, review=None):
    acceptance = submission.consent_acceptances.select_related("document").get()
    payload = {
        "schema": TRANSPORT_SCHEMA, "operation_id": str(submission.intake_operation_id),
        "event_id": str(session.event_id), "source_identity": str(submission.participant.identity_uuid),
        "form": {"slug": submission.form_version.form.slug, "version": submission.form_version.version,
                 "stable_id": str(submission.form_version.stable_id), "hash": submission.form_version.content_hash},
        "answers": deepcopy(submission.answers), "metadata": deepcopy(submission.intake_metadata),
        "submitted_at": submission.submitted_at.isoformat(),
        "consent": {"key": acceptance.document.key, "version": acceptance.document.version,
                    "hash": acceptance.document.content_hash, "text_hash": session.context["consent_text_hash"],
                    "accepted_at": acceptance.accepted_at.isoformat()},
        "source_actor_uuid": str(actor.pk) if getattr(actor, "is_authenticated", False) else None,
        "registration": None, "review": None}
    if review:
        decision = review.decisions.order_by("-created_at", "-pk").first()
        payload["review"] = {"action": decision.action if decision else None,
            "target_identity": str(review.candidate_participant.identity_uuid) if review.candidate_participant_id else None,
            "reviewed_at": review.reviewed_at.isoformat(), "reason": decision.reason if decision else review.resolution_note}
    if registration:
        ticket = registration.tickets.filter(is_current=True).get()
        payload["registration"] = {
            "id": str(registration.pk), "owner_identity": str(registration.participant.identity_uuid),
            "registered_at": registration.registered_at.isoformat(), "group_id": str(registration.group_id) if registration.group_id else None,
            "custom_answers": registration.custom_answers, "ticket_email_opt_in": registration.ticket_email_opt_in,
            "ticket_sms_opt_in": registration.ticket_sms_opt_in,
            "ticket": {"id": str(ticket.pk), "token": str(ticket.token), "number": ticket.ticket_number,
                       "issued_at": ticket.issued_at.isoformat(), "expires_at": ticket.expires_at.isoformat()}}
    payload["envelope_hash"] = contract_hash(payload)
    return payload


def retain_pending_intake(*, session, submission, actor):
    preparation = OfflineEventPreparation.objects.get(pk=session.context["preparation_id"],
                                                     event=session.event, trailer=session.trailer)
    payload = _envelope(session, submission, actor)
    return queue_sync(preparation, operation_key="intake:" + str(submission.intake_operation_id),
                      operation_type="intake.completed", payload=payload)


def retain_local_envelope(*, session, submission, registration, actor, review=None, new_registration=False):
    """Reuse existing reserved ranges, tickets and transactional outbox.

    Existing registrations/tickets are never renumbered on a returning intake.
    """
    preparation = OfflineEventPreparation.objects.get(pk=session.context["preparation_id"],
                                                     event=session.event, trailer=session.trailer)
    if new_registration:
        ticket = registration.tickets.select_for_update(of=("self",)).filter(is_current=True).get()
        ticket.ticket_number = reserve_ticket_number(preparation)
        ticket.save(update_fields=["ticket_number"])
    payload = _envelope(session, submission, actor, registration=registration, review=review)
    suffix = ":review:" + str(review.pk) if review else ""
    return queue_sync(preparation, operation_key="intake:" + str(submission.intake_operation_id) + suffix,
                      operation_type="intake.completed", payload=payload)
