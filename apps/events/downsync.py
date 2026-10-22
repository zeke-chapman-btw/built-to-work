"""Central-to-trailer event snapshots and conservative local import."""
from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.participants.models import (
    ConsentAcceptance, ConsentDocumentVersion, Participant, RegistrationForm,
    RegistrationFormVersion, RegistrationSubmission,
)
from .models import (
    CentralEventDownload, CentralEventDownloadPage, Event, EventGroup,
    EventRegistration, EventRegistrationQuestion, OfflineEventPreparation,
    OfflineTicketRange, QrTicket, TrailerInstance,
)


def stamp(value):
    return value.isoformat() if value else None


def when(value):
    result = parse_datetime(value) if value else None
    if value and (result is None or timezone.is_naive(result)):
        raise ValidationError("Invalid synchronization timestamp")
    return result


def _fields(instance, names):
    return {name: getattr(instance, name) for name in names}


EVENT_FIELDS = (
    "name", "code", "description", "timezone_name", "location_name", "address_line_1",
    "address_line_2", "city", "state", "postal_code", "country", "status",
    "allow_station_auto_check_in", "registration_capacity", "group_mode", "group_label",
    "public_registration_status",
)
EVENT_DATES = ("start_at", "end_at", "registration_opens_at", "registration_closes_at", "registration_deadline_at", "created_at", "updated_at")
PARTICIPANT_FIELDS = (
    "kind", "first_name", "last_name", "preferred_name", "contact_email", "contact_phone",
    "city", "state", "postal_code", "address_line_1", "address_line_2", "life_stage", "age_classification",
)


def _document_payload(doc):
    if not doc.is_approved or not doc.original_pdf:
        raise ValidationError("Approved consent PDF is required for offline preparation")
    with doc.original_pdf.open("rb") as stream:
        pdf = stream.read()
    if not pdf.startswith(b"%PDF-") or hashlib.sha256(pdf).hexdigest() != doc.content_hash:
        raise ValidationError("Consent PDF integrity check failed")
    return {"key": doc.key, "version": doc.version, "title": doc.title, "body": doc.body,
            "content_hash": doc.content_hash, "is_approved": True,
            "effective_at": stamp(doc.effective_at), "published_at": stamp(doc.published_at),
            "pdf_base64": base64.b64encode(pdf).decode("ascii")}


def _form_payload(form_version):
    return {"slug": form_version.form.slug, "name": form_version.form.name,
            "version": form_version.version, "snapshot": form_version.snapshot,
            "published_at": stamp(form_version.published_at), "effective_at": stamp(form_version.effective_at)}


def event_configuration(preparation):
    event = preparation.event
    doc = preparation.consent_document or event.public_consent_document
    if not doc or not doc.is_approved or not doc.original_pdf:
        raise ValidationError("Approved consent PDF is required for offline preparation")
    used_docs = ConsentDocumentVersion.objects.filter(acceptances__submission__event=event).distinct()
    documents = [doc] + [item for item in used_docs if item.pk != doc.pk]
    form_version = RegistrationFormVersion.objects.filter(status="published", form__is_smart_intake=False).select_related("form").order_by("-version").first()
    return {
        "event_id": str(event.pk), "preparation_id": str(preparation.pk),
        "event": {"id": str(event.pk), **_fields(event, EVENT_FIELDS), **{key: stamp(getattr(event, key)) for key in EVENT_DATES}},
        "groups": [{"id": str(group.pk), "name": group.name, "is_active": group.is_active,
                    "capacity": group.capacity, "registration_deadline_at": stamp(group.registration_deadline_at),
                    "created_at": stamp(group.created_at), "updated_at": stamp(group.updated_at)}
                   for group in event.groups.order_by("pk")],
        "questions": [{"id": str(q.pk), "key": q.key, "label": q.label, "field_type": q.field_type,
                       "options": q.options, "is_required": q.is_required, "is_active": q.is_active,
                       "position": q.position} for q in event.registration_questions.order_by("pk")],
        "consent": _document_payload(doc),
        "consent_versions": [_document_payload(item) for item in documents],
        "form": _form_payload(form_version) if form_version else None,
        "form_versions": [_form_payload(item) for item in RegistrationFormVersion.objects.filter(
            submissions__event=event, form__is_smart_intake=False).select_related("form").distinct() if not form_version or item.pk != form_version.pk],
        "ranges": [{"kind": r.kind, "start_number": r.start_number, "end_number": r.end_number,
                    "next_number": r.next_number, "is_exhausted": r.is_exhausted}
                   for r in preparation.ticket_ranges.order_by("kind")],
        "central_updated_at": stamp(timezone.now()),
    }


def registration_changed_at(reg):
    values = [reg.updated_at, reg.participant.updated_at]
    for ticket in reg.tickets.all():
        values.append(ticket.issued_at)
        if ticket.revoked_at:
            values.append(ticket.revoked_at)
    for sub in reg.participant.registration_submissions.filter(event_id=reg.event_id):
        values.append(sub.updated_at)
        values.extend(a.created_at for a in sub.consent_acceptances.all())
    return max(values)


def eligible_registrations(event, since, as_of):
    result = []
    rows = EventRegistration.objects.filter(event=event, source__in=("online", "public")).select_related("participant", "group").prefetch_related("tickets")
    for reg in rows:
        modified = registration_changed_at(reg)
        if modified <= as_of and (since is None or modified > since):
            result.append(reg)
    return sorted(result, key=lambda reg: str(reg.pk))


def _ticket_record(ticket):
    return {"id": str(ticket.pk), "token": str(ticket.token), "ticket_number": ticket.ticket_number,
            "issued_at": stamp(ticket.issued_at), "expires_at": stamp(ticket.expires_at),
            "revoked_at": stamp(ticket.revoked_at), "is_current": ticket.is_current,
            "superseded_by_id": str(ticket.superseded_by_id) if ticket.superseded_by_id else None}


def registration_record(reg):
    participant = reg.participant
    ticket = reg.tickets.filter(is_current=True).first()
    if ticket is None and reg.status == EventRegistration.Status.VOID:
        ticket = reg.tickets.filter(ticket_number__isnull=False).order_by("-issued_at").first()
    if ticket is None or not ticket.ticket_number:
        raise ValidationError("Pre-registration lacks a numbered ticket")
    submission = participant.registration_submissions.filter(event=reg.event, status="submitted").select_related("form_version__form").order_by("-submitted_at").first()
    if submission is None:
        raise ValidationError("Pre-registration lacks its submission snapshot")
    acceptance = submission.consent_acceptances.select_related("document").order_by("-accepted_at").first()
    if acceptance is None:
        raise ValidationError("Pre-registration lacks consent evidence")
    record = {
        "participant": {"identity_uuid": str(participant.identity_uuid), **_fields(participant, PARTICIPANT_FIELDS),
                        "date_of_birth": participant.date_of_birth.isoformat() if participant.date_of_birth else None,
                        "created_at": stamp(participant.created_at), "updated_at": stamp(participant.updated_at)},
        "registration": {"id": str(reg.pk), "event_id": str(reg.event_id), "group_id": str(reg.group_id) if reg.group_id else None,
                         "custom_answers": reg.custom_answers, "status": reg.status, "source": reg.source,
                         "registered_at": stamp(reg.registered_at), "created_at": stamp(reg.created_at), "updated_at": stamp(reg.updated_at),
                         "ticket_email_opt_in": reg.ticket_email_opt_in, "ticket_sms_opt_in": reg.ticket_sms_opt_in,
                         "ticket_communication_opt_in_at": stamp(reg.ticket_communication_opt_in_at)},
        "ticket": _ticket_record(ticket),
        "prior_tickets": [_ticket_record(old) for old in reg.tickets.filter(ticket_number__isnull=False).exclude(pk=ticket.pk)],
        "submission": {"source_id": submission.pk, "form_slug": submission.form_version.form.slug,
                       "form_version": submission.form_version.version, "status": submission.status,
                       "answers": submission.answers, "answer_snapshot": submission.answer_snapshot,
                       "age_classification": submission.age_classification, "submitted_at": stamp(submission.submitted_at),
                       "source": submission.source, "created_at": stamp(submission.created_at), "updated_at": stamp(submission.updated_at)},
        "consent": {"key": acceptance.document.key, "version": acceptance.document.version,
                    "content_hash": acceptance.document.content_hash, "accepted_at": stamp(acceptance.accepted_at),
                    "created_at": stamp(acceptance.created_at),
                    "updated_at": stamp(acceptance.updated_at),
                    "metadata": acceptance.metadata},
        "changed_at": stamp(registration_changed_at(reg)),
    }
    if submission.intake_operation_id:
        # Automatic pre-registration downloads carry references and answers only.
        # New form definitions require explicit local Admin installation.
        record["submission"].update(
            intake_operation_id=str(submission.intake_operation_id),
            form_stable_id=str(submission.form_version.stable_id),
            contract_hash=submission.form_version.content_hash,
            intake_metadata=submission.intake_metadata,
            answer_snapshot={"answers": submission.answers})
    return record


def _same(obj, values):
    return all(getattr(obj, key) == value for key, value in values.items())


def apply_configuration(download, config):
    if str(config.get("event_id")) != str(download.event_id) or str(config.get("preparation_id")) != str(download.preparation_id):
        raise ValidationError("Downloaded event scope mismatch")
    remote_event = config["event"]
    fields = {key: remote_event[key] for key in EVENT_FIELDS}
    fields.update({key: when(remote_event.get(key)) for key in EVENT_DATES if key not in ("created_at", "updated_at")})
    event = Event.objects.filter(pk=download.event_id).first()
    previous_event_updated_at = event.updated_at if event else None
    changed_at = when(remote_event["updated_at"])
    if event is None:
        event = Event.objects.create(id=download.event_id, **fields)
    elif not _same(event, fields):
        if event.updated_at > changed_at:
            raise ValidationError("Local event configuration is newer; staff review required")
        for key, value in fields.items():
            setattr(event, key, value)
        event.save()
    Event.objects.filter(pk=event.pk).update(created_at=when(remote_event["created_at"]), updated_at=changed_at)

    for row in config["groups"]:
        values = {key: row[key] for key in ("name", "is_active", "capacity")}
        values["registration_deadline_at"] = when(row.get("registration_deadline_at"))
        group = EventGroup.objects.filter(pk=uuid.UUID(row["id"])).first()
        if group and group.event_id != event.pk:
            raise ValidationError("Group belongs to another event")
        if group is None:
            group = EventGroup.objects.create(id=row["id"], event=event, **values)
        elif not _same(group, values):
            if group.updated_at > when(row["updated_at"]):
                raise ValidationError("Local group configuration is newer; staff review required")
            for key, value in values.items():
                setattr(group, key, value)
            group.save()
        EventGroup.objects.filter(pk=group.pk).update(created_at=when(row["created_at"]), updated_at=when(row["updated_at"]))

    for row in config["questions"]:
        values = {key: row[key] for key in ("key", "label", "field_type", "options", "is_required", "is_active", "position")}
        question = EventRegistrationQuestion.objects.filter(pk=uuid.UUID(row["id"])).first()
        if question and question.event_id != event.pk:
            raise ValidationError("Question belongs to another event")
        if question is None:
            EventRegistrationQuestion.objects.create(id=row["id"], event=event, **values)
        elif not _same(question, values):
            active = OfflineEventPreparation.objects.filter(pk=download.preparation_id, status="active").exists()
            if active:
                raise ValidationError("Active local question configuration differs; staff review required")
            for key, value in values.items():
                setattr(question, key, value)
            question.save()

    documents = config.get("consent_versions") or [config["consent"]]
    for data in documents:
        try:
            pdf = base64.b64decode(data["pdf_base64"], validate=True)
        except (ValueError, KeyError) as exc:
            raise ValidationError("Invalid consent PDF transfer") from exc
        if len(pdf) > 10 * 1024 * 1024 or not pdf.startswith(b"%PDF-") or hashlib.sha256(pdf).hexdigest() != data["content_hash"]:
            raise ValidationError("Consent PDF integrity check failed")
        doc = ConsentDocumentVersion.objects.filter(key=data["key"], version=data["version"]).first()
        if doc:
            if doc.content_hash != data["content_hash"] or doc.body != data["body"] or not doc.is_approved:
                raise ValidationError("Local consent version conflicts with central version")
            if doc.original_pdf:
                with doc.original_pdf.open("rb") as existing_pdf:
                    if hashlib.sha256(existing_pdf.read()).hexdigest() != data["content_hash"]:
                        raise ValidationError("Local consent PDF integrity check failed")
            else:
                doc.original_pdf.save(f"{doc.key}-{data["content_hash"][:12]}.pdf", ContentFile(pdf), save=True)
        else:
            doc = ConsentDocumentVersion(key=data["key"], version=data["version"], title=data["title"],
                body=data["body"], content_hash=data["content_hash"], is_approved=True,
                effective_at=when(data["effective_at"]), published_at=when(data["published_at"]))
            doc.original_pdf.save(f"{doc.key}-{data["content_hash"][:12]}.pdf", ContentFile(pdf), save=False)
            doc.save()
    current = config["consent"]
    doc = ConsentDocumentVersion.objects.get(key=current["key"], version=current["version"])
    if doc.content_hash != current["content_hash"] or doc.body != current["body"]:
        raise ValidationError("Current consent version does not match its transferred document")
    for f in [config["form"], *config.get("form_versions", [])]:
        if f is None:
            continue
        form, _ = RegistrationForm.objects.get_or_create(slug=f["slug"], defaults={"name": f["name"]})
        form_version = RegistrationFormVersion.objects.filter(form=form, version=f["version"]).first()
        if form_version:
            if form_version.snapshot != f["snapshot"] or form_version.status != "published":
                raise ValidationError("Local registration form version conflicts with central version")
        else:
            RegistrationFormVersion.objects.create(form=form, version=f["version"], snapshot=f["snapshot"],
                status="published", published_at=when(f["published_at"]), effective_at=when(f["effective_at"]))

    trailer, _ = TrailerInstance.objects.get_or_create(identity=download.trailer_identity,
                                                        defaults={"display_name": download.trailer_identity})
    prep = OfflineEventPreparation.objects.filter(pk=download.preparation_id).first()
    if prep and (prep.event_id != event.pk or prep.trailer_id != trailer.pk):
        raise ValidationError("Local preparation belongs to another event or trailer")
    if prep is None:
        prep = OfflineEventPreparation.objects.create(id=download.preparation_id, event=event, trailer=trailer,
                                                       status="prepared", consent_document=doc,
                                                       consent_hash=doc.content_hash, sync_identity=trailer.identity)
    else:
        prep.consent_document = doc
        prep.consent_hash = doc.content_hash
        prep.sync_identity = trailer.identity
        prep.save(update_fields=("consent_document", "consent_hash", "sync_identity", "updated_at"))
    if previous_event_updated_at and event.public_consent_document_id not in (None, doc.pk) and previous_event_updated_at > changed_at:
        raise ValidationError("Local event consent selection is newer; staff review required")
    event.public_consent_document = doc
    event.save(update_fields=("public_consent_document",))
    Event.objects.filter(pk=event.pk).update(updated_at=changed_at)

    for row in config["ranges"]:
        item = OfflineTicketRange.objects.filter(preparation=prep, kind=row["kind"]).first()
        if item:
            if item.start_number != row["start_number"] or item.end_number != row["end_number"]:
                raise ValidationError("Local reserved ticket range conflicts with central allocation")
            # Never move a used local allocation counter backwards.
            item.next_number = max(item.next_number, row["next_number"])
            item.is_exhausted = item.next_number > item.end_number
            item.save(update_fields=("next_number", "is_exhausted"))
        else:
            OfflineTicketRange.objects.create(preparation=prep, kind=row["kind"], start_number=row["start_number"],
                                              end_number=row["end_number"], next_number=row["next_number"],
                                              is_exhausted=row["is_exhausted"])
    return event, doc


def apply_registration(download, row):
    smart = row.get("submission", {}).get("intake_operation_id")
    approved_intake = None
    if smart:
        from apps.participants.intake_lifecycle import verify_published
        reference = row["submission"]
        approved_intake = RegistrationFormVersion.objects.select_related("form").filter(
            stable_id=reference.get("form_stable_id"), form__slug=reference.get("form_slug"),
            version=reference.get("form_version")).first()
        if approved_intake is None or approved_intake.content_hash != reference.get("contract_hash"):
            raise ValidationError("A local Admin must explicitly install this exact approved intake version before importing its pre-registration.")
        verify_published(approved_intake, allow_retired=True)
    data = row["registration"]
    if data["event_id"] != str(download.event_id) or data["source"] not in ("online", "public"):
        raise ValidationError("Downloaded registration has wrong event or source")
    person = row["participant"]
    identity = uuid.UUID(person["identity_uuid"])
    pfields = {key: person[key] for key in PARTICIPANT_FIELDS}
    pfields["date_of_birth"] = datetime.fromisoformat(person["date_of_birth"]).date() if person.get("date_of_birth") else None
    participant = Participant.objects.filter(identity_uuid=identity).first()
    if participant is None:
        participant = Participant.objects.create(identity_uuid=identity, **pfields)
        Participant.objects.filter(pk=participant.pk).update(created_at=when(person["created_at"]), updated_at=when(person["updated_at"]))
    elif not _same(participant, pfields):
        if smart:
            raise ValidationError("Local participant facts differ from the central intake copy; staff review is required before updating them.")
        if participant.updated_at > when(person["updated_at"]):
            raise ValidationError("Local participant record is newer; staff review required")
        for key, value in pfields.items():
            setattr(participant, key, value)
        participant.save()
        Participant.objects.filter(pk=participant.pk).update(updated_at=when(person["updated_at"]))

    group_id = uuid.UUID(data["group_id"]) if data.get("group_id") else None
    if group_id and not EventGroup.objects.filter(pk=group_id, event_id=download.event_id).exists():
        raise ValidationError("Registration references an unknown group")
    reg = EventRegistration.objects.filter(pk=uuid.UUID(data["id"])).first()
    if reg and (reg.event_id != download.event_id or reg.participant_id != participant.pk):
        raise ValidationError("Registration UUID belongs to another event or participant")
    rfields = {key: data[key] for key in ("custom_answers", "status", "source", "ticket_email_opt_in", "ticket_sms_opt_in")}
    rfields.update(group_id=group_id, ticket_communication_opt_in_at=when(data.get("ticket_communication_opt_in_at")),
                   registered_at=when(data["registered_at"]))
    if reg is None:
        reg = EventRegistration.objects.create(id=data["id"], event_id=download.event_id, participant=participant, **rfields)
        EventRegistration.objects.filter(pk=reg.pk).update(created_at=when(data["created_at"]), updated_at=when(data["updated_at"]))
    elif not _same(reg, rfields):
        if reg.updated_at > when(data["updated_at"]):
            raise ValidationError("Local registration is newer; staff review required")
        for key, value in rfields.items():
            setattr(reg, key, value)
        reg.save()
        EventRegistration.objects.filter(pk=reg.pk).update(updated_at=when(data["updated_at"]))

    current_ticket = row["ticket"]
    prior_tickets = row.get("prior_tickets", [])
    if not current_ticket["is_current"] and data["status"] != EventRegistration.Status.VOID:
        raise ValidationError("Active downloaded registration has no current ticket")
    if current_ticket["is_current"] and data["status"] == EventRegistration.Status.VOID:
        raise ValidationError("Voided downloaded registration still has a current ticket")
    ticket_rows = [*prior_tickets, current_ticket]
    ticket_ids = [item["id"] for item in ticket_rows]
    ticket_numbers = [item["ticket_number"] for item in ticket_rows]
    if len(ticket_ids) != len(set(ticket_ids)) or len(ticket_numbers) != len(set(ticket_numbers)):
        raise ValidationError("Duplicate ticket identity in central snapshot")
    local_current = QrTicket.objects.filter(registration=reg, is_current=True).first()
    if local_current and str(local_current.pk) != current_ticket["id"] and str(local_current.pk) not in ticket_ids[:-1]:
        raise ValidationError("Local current ticket is not in central history; staff review required")
    imported = {}
    for item in ticket_rows:
        number = item["ticket_number"]
        if len(number) != 10 or not number.isdigit() or (item is not current_ticket and item["is_current"]):
            raise ValidationError("Invalid ten-digit ticket history")
        ticket_id = uuid.UUID(item["id"])
        token = uuid.UUID(item["token"])
        existing = QrTicket.objects.filter(ticket_number=number).first()
        if existing and (existing.pk != ticket_id or existing.registration_id != reg.pk or existing.token != token):
            raise ValidationError("Ticket number collision or changed QR token")
        if QrTicket.objects.filter(pk=ticket_id).exclude(ticket_number=number, registration=reg).exists():
            raise ValidationError("Ticket UUID belongs to another registration or number")
        if QrTicket.objects.filter(token=token).exclude(pk=ticket_id).exists():
            raise ValidationError("Ticket QR token collision")
        if existing is None:
            existing = QrTicket.objects.create(
                id=ticket_id, registration=reg, token=token, ticket_number=number,
                issued_at=when(item["issued_at"]), expires_at=when(item["expires_at"]),
                revoked_at=when(item.get("revoked_at")), is_current=item["is_current"])
        elif not item["is_current"]:
            # A central reissue must invalidate the formerly current local ticket.
            existing.is_current = False
            if item.get("revoked_at") and (existing.revoked_at is None or existing.revoked_at > when(item["revoked_at"])):
                existing.revoked_at = when(item["revoked_at"])
            if existing.expires_at > when(item["expires_at"]):
                existing.expires_at = when(item["expires_at"])
            existing.save(update_fields=("is_current", "revoked_at", "expires_at"))
        elif not existing.is_current:
            raise ValidationError("Locally revoked ticket cannot be reactivated by download")
        else:
            # Apply only central restrictions; never undo a local revocation or shorten its protection.
            changed = []
            if item.get("revoked_at") and (existing.revoked_at is None or existing.revoked_at > when(item["revoked_at"])):
                existing.revoked_at = when(item["revoked_at"])
                changed.append("revoked_at")
            if existing.expires_at > when(item["expires_at"]):
                existing.expires_at = when(item["expires_at"])
                changed.append("expires_at")
            if changed:
                existing.save(update_fields=changed)
        imported[item["id"]] = existing
    for item in prior_tickets:
        replacement_id = item.get("superseded_by_id")
        if replacement_id:
            old = imported[item["id"]]
            replacement = imported.get(replacement_id)
            if replacement is None or (old.superseded_by_id and old.superseded_by_id != replacement.pk):
                raise ValidationError("Invalid ticket replacement history")
            if old.superseded_by_id is None:
                old.superseded_by = replacement
                old.save(update_fields=("superseded_by",))
    sub = row["submission"]
    form_version = approved_intake or RegistrationFormVersion.objects.filter(form__slug=sub["form_slug"], version=sub["form_version"], status="published").first()
    if not form_version:
        raise ValidationError("Submission references unknown published form version")
    source_key = "central-" + hashlib.sha256(f"{download.event_id}:{reg.pk}:{sub['source_id']}".encode()).hexdigest()[:56]
    if smart:
        source_key = "intake:" + str(uuid.UUID(smart))
    submission = RegistrationSubmission.objects.filter(idempotency_key=source_key).first()
    if smart and submission and (submission.form_version_id != form_version.pk or submission.answers != sub["answers"]):
        raise ValidationError("The portable intake operation conflicts with retained local evidence.")
    if submission and (submission.participant_id != participant.pk or submission.event_id != download.event_id):
        raise ValidationError("Submission identity collision")
    submission_created = submission is None
    if not submission:
        submission = RegistrationSubmission.objects.create(form_version=form_version, participant=participant,
            event_id=download.event_id, status=sub["status"], answers=sub["answers"],
            answer_snapshot={"contract": form_version.snapshot, "answers": sub["answers"]} if smart else sub["answer_snapshot"],
            age_classification=sub["age_classification"],
            submitted_at=when(sub["submitted_at"]), source=sub["source"], idempotency_key=source_key,
            **({"intake_operation_id": uuid.UUID(smart), "intake_metadata": sub["intake_metadata"]} if smart else {}))
        RegistrationSubmission.objects.filter(pk=submission.pk).update(created_at=when(sub["created_at"]), updated_at=when(sub["updated_at"]))
    consent = row["consent"]
    doc = ConsentDocumentVersion.objects.filter(key=consent["key"], version=consent["version"],
                                                content_hash=consent["content_hash"], is_approved=True).first()
    if not doc:
        raise ValidationError("Consent evidence references unavailable document version")
    acceptance = ConsentAcceptance.objects.filter(participant=participant, submission=submission, document=doc).first()
    if not acceptance:
        acceptance = ConsentAcceptance.objects.create(participant=participant, submission=submission,
                                                      document=doc, metadata=consent["metadata"])
        ConsentAcceptance.objects.filter(pk=acceptance.pk).update(
        accepted_at=when(consent["accepted_at"]), created_at=when(consent["created_at"]),
        updated_at=when(consent["updated_at"]))
    if smart and submission_created:
        from apps.participants.intake_projection import project_intake
        project_intake(submission=submission, is_new=False)
    return reg


@transaction.atomic
def apply_download(download):
    download = CentralEventDownload.objects.select_for_update().get(pk=download.pk)
    if download.status != CentralEventDownload.Status.DOWNLOADING or not download.snapshot_at or download.next_cursor:
        raise ValidationError("Download is incomplete")
    apply_configuration(download, download.config)
    count = 0
    for page in download.pages.order_by("page_number"):
        for row in page.records:
            apply_registration(download, row)
            count += 1
    download.checkpoint_at = download.snapshot_at
    download.last_success_at = timezone.now()
    download.downloaded_count = EventRegistration.objects.filter(event_id=download.event_id, source__in=("online", "public")).count()
    download.status = CentralEventDownload.Status.READY
    download.last_error = ""
    download.next_cursor = ""
    download.save()
    download.pages.all().delete()
    return count


def configuration_issues(preparation, config):
    """Detect local edits or a newer advertised central configuration before activation."""
    issues = []
    event = preparation.event
    remote = config.get("event") or {}
    if str(event.pk) != str(remote.get("id")):
        return ["downloaded Event identity does not match"]
    if any(getattr(event, key) != remote.get(key) for key in EVENT_FIELDS):
        issues.append("Event registration configuration differs from the downloaded version")
    if any(stamp(getattr(event, key)) != remote.get(key) for key in EVENT_DATES if key not in ("created_at", "updated_at")):
        issues.append("Event schedule or deadline differs from the downloaded version")
    groups = {str(row.pk): row for row in event.groups.all()}
    expected_groups = {row["id"]: row for row in config.get("groups", [])}
    if groups.keys() != expected_groups.keys() or any(
        group.name != expected_groups[key]["name"] or group.capacity != expected_groups[key]["capacity"]
        or group.is_active != expected_groups[key]["is_active"]
        or stamp(group.registration_deadline_at) != expected_groups[key]["registration_deadline_at"]
        for key, group in groups.items() if key in expected_groups
    ):
        issues.append("Event groups differ from the downloaded version")
    questions = {str(row.pk): row for row in event.registration_questions.all()}
    expected_questions = {row["id"]: row for row in config.get("questions", [])}
    if questions.keys() != expected_questions.keys() or any(
        any(getattr(question, field) != expected_questions[key][field]
            for field in ("key", "label", "field_type", "options", "is_required", "is_active", "position"))
        for key, question in questions.items() if key in expected_questions
    ):
        issues.append("Event questions differ from the downloaded version")
    consent = config.get("consent") or {}
    if not preparation.consent_document_id or (
        preparation.consent_document.key != consent.get("key")
        or preparation.consent_document.version != consent.get("version")
        or preparation.consent_document.content_hash != consent.get("content_hash")
    ):
        issues.append("Consent version differs from the downloaded version")
    return issues
