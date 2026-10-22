import json
import uuid
from datetime import date
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from apps.core.audit import record_audit
from apps.events.services import register_participant
from .forms import RegistrationQuestionForm, SnapshotRegistrationForm, LIFE_STAGE_CHOICES
from .models import (Participant, ParticipantCareerProfile, RegistrationForm, RegistrationFormVersion, RegistrationQuestion, RegistrationSubmission, ConsentDocumentVersion, ConsentAcceptance)

DEFAULT_QUESTIONS = [
    ("first_name", "First name", "short_text", True, True), ("preferred_name", "Preferred name", "short_text", False, False), ("last_name", "Last name", "short_text", True, True),
    ("contact_phone", "Phone number", "short_text", True, True), ("date_of_birth", "Date of birth", "date", True, True),
    ("contact_email", "Email", "short_text", False, True), ("life_stage", "Which best describes you currently?", "single_choice", True, True),
    ("address_line_1", "Address", "short_text", False, False), ("address_line_2", "Address line 2", "short_text", False, False), ("city", "City", "short_text", False, False), ("state", "State", "short_text", False, False), ("postal_code", "ZIP / postal code", "short_text", False, False),
    ("employment_status", "Employment status", "short_text", False, False), ("current_industry", "Current or previous industry", "short_text", False, False),
    ("career_interests", "Career and industry interests", "long_text", False, False), ("construction_experience", "Do you have construction experience?", "yes_no", False, False),
    ("years_experience", "Years of construction experience", "number", False, False), ("certifications", "Certifications", "long_text", False, False),
    ("willing_to_travel", "Are you willing to travel for work?", "yes_no", False, False), ("travel_distance", "How far would you travel?", "single_choice", False, False),
]

def audit(actor, action, instance, new_data=None):
    return record_audit(actor=actor, action=action, instance=instance, new_data=new_data)

def standard_form():
    form, _ = RegistrationForm.objects.get_or_create(slug="standard", defaults={"name": "BTW Standard Intake"})
    for pos, (key, label, ftype, required, protected) in enumerate(DEFAULT_QUESTIONS):
        q, created = RegistrationQuestion.objects.get_or_create(form=form, canonical_key=key, defaults={"label": label, "field_type": ftype, "is_required": required, "is_protected": protected, "position": pos})
        if key == "life_stage" and not q.options:
            q.options = [{"value": value, "label": label} for value, label in LIFE_STAGE_CHOICES]
            q.save(update_fields=["options", "updated_at"])
        if key == "travel_distance" and not q.options:
            q.options = [{"value": value, "label": label} for value, label in [("50", "Up to 50 miles"), ("100", "Up to 100 miles"), ("anywhere", "Anywhere")]]
            q.save(update_fields=["options", "updated_at"])
        if not created and q.is_protected and q.field_type != ftype:
            q.field_type = ftype
            q.save(update_fields=["field_type", "updated_at"])
    extras = [
        ("school_name", "School or institution", "short_text", {"field": "life_stage", "value": "middle_school"}),
        ("grade_or_program", "Grade, program, or field of study", "short_text", {"field": "life_stage", "value": "high_school"}),
        ("trade_or_college_program", "Trade school or college program", "short_text", {"field": "life_stage", "value": "trade_school"}),
        ("graduation_year", "Expected or actual graduation year", "number", {"field": "life_stage", "value": "college"}),
        ("previous_experience", "Previous work or experience", "long_text", {"field": "life_stage", "value": "retired"}),
        ("certification_details", "Certification details", "long_text", {"field": "certifications", "not_empty": True}),
    ]
    for pos, (key, label, ftype, rule) in enumerate(extras, 100):
        RegistrationQuestion.objects.get_or_create(form=form, canonical_key=key, defaults={"label": label, "field_type": ftype, "visibility_rule": rule, "position": pos})
    return form

def published_version(form, actor=None):
    return form.versions.filter(status=RegistrationFormVersion.Status.PUBLISHED).order_by("-version").first()

def age_for(dob):
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

def registration_start(request, event_id=None):
    event = None
    if event_id:
        from apps.events.models import Event
        event = get_object_or_404(Event, pk=event_id)
    from .intake_views import assigned
    smart = assigned("event" if event else "online", event=event)
    if smart:
        return redirect("participants:intake-start", assignment_id=smart.pk)
    if request.method == "POST":
        return redirect("participants:registration-form" + (("?event=" + str(event.pk)) if event else ""))
    return render(request, "participants/registration_start.html", {"event": event})

def _registration_complete(request, submission):
    from apps.events.models import QrTicket
    participant = submission.participant
    event = submission.event
    ticket = None
    if event:
        ticket = QrTicket.objects.filter(registration__event=event, registration__participant=participant, is_current=True).first()
    return render(request, "participants/registration_complete.html", {
        "participant": participant, "submission": submission, "ticket": ticket,
        "event": event, "legal_pending": False,
    })


def registration_form(request, *, fixed_event=None, public=False):
    # Explicit 11B assignments opt in without changing legacy, unassigned forms.
    from .intake_views import assigned
    smart_event = fixed_event
    raw_event = request.GET.get("event") or request.POST.get("event")
    if smart_event is None and raw_event:
        from apps.events.models import Event
        smart_event = get_object_or_404(Event, pk=raw_event)
    smart = assigned("event" if smart_event else "online", event=smart_event)
    if smart:
        return redirect("participants:intake-start", assignment_id=smart.pk)
    # The rendered form carries a request key; resubmitting that form is idempotent.
    # Legacy/API POSTs without a key remain independent submissions.
    submitted_key = request.POST.get("registration_request_key", "") if request.method == "POST" else ""
    try:
        request_key = uuid.UUID(submitted_key).hex if submitted_key else uuid.uuid4().hex
    except ValueError:
        return HttpResponseBadRequest("Invalid registration request key.")
    if public and request.method == "POST" and not submitted_key:
        return HttpResponseBadRequest("A registration request key is required.")
    form_model = standard_form()
    version = published_version(form_model)
    event_id = None if fixed_event is not None else (request.GET.get("event") or request.POST.get("event"))
    event = fixed_event
    if event_id:
        from apps.events.models import Event
        event = get_object_or_404(Event, pk=event_id)
    if request.method == "POST":
        prior = RegistrationSubmission.objects.select_related("participant", "event").filter(idempotency_key=request_key).first()
        if prior:
            if prior.event_id != (event.pk if event else None):
                return render(request, "participants/registration_unavailable.html", {
                    "event": event, "reason": "This form was already submitted for another event. Start a new registration."
                }, status=409)
            return _registration_complete(request, prior)
    approved = (event.public_consent_document if public and event else
                ConsentDocumentVersion.objects.filter(key="btw-intake", is_approved=True).order_by("-effective_at", "-version").first())
    if approved and not approved.is_approved:
        approved = None
    if not version or not approved:
        return render(request, "participants/registration_unavailable.html", {
            "event": event, "reason": "Registration is temporarily unavailable while BTW updates its approved consent information."
        }, status=503)
    if public and request.method == "POST" and request.POST.get("consent_document_id") != str(approved.pk):
        return render(request, "participants/registration_unavailable.html", {
            "event": event, "reason": "The consent information changed. Please reload and review it before registering."
        }, status=409)
    questions = version.snapshot.get("questions", [])
    form = SnapshotRegistrationForm(request.POST or None, questions=questions, consent_document=approved, event=event)
    if request.method == "POST" and form.is_valid():
        from apps.events.models import EventRegistration
        data = form.cleaned_data
        age = age_for(data["date_of_birth"])
        classification = "minor" if age < 18 else "adult"
        try:
            with transaction.atomic():
                if public:
                    from apps.events.models import Event
                    event = Event.objects.select_for_update().get(pk=event.pk)
                    current_document = ConsentDocumentVersion.objects.select_for_update().get(pk=approved.pk)
                    if (not current_document.is_approved or not current_document.body.strip()
                        or (current_document.effective_at and current_document.effective_at > timezone.now())
                            or event.status != Event.Status.UPCOMING or event.public_registration_status != "open"
                            or not event.registration_is_open or event.end_at < timezone.now()
                            or event.public_consent_document_id != approved.pk or not approved.is_approved):
                        raise ValidationError("Registration is no longer available. Please reload this page.")
                participant = Participant.objects.create(
                    first_name=data.get("first_name", ""), last_name=data.get("last_name", ""),
                    preferred_name=data.get("preferred_name", ""), contact_email=data.get("contact_email", ""),
                    contact_phone=data.get("contact_phone", ""), city=data.get("city", ""),
                    state=data.get("state", ""), postal_code=data.get("postal_code", ""),
                    date_of_birth=data["date_of_birth"], life_stage=data.get("life_stage", ""),
                    age_classification=classification,
                )
                ParticipantCareerProfile.objects.update_or_create(participant=participant, defaults={
                    "employment_status": data.get("employment_status", ""),
                    "career_interests": data.get("career_interests", ""),
                    "willing_to_travel": data.get("willing_to_travel") == "yes",
                    "education_training": data.get("education_training", ""),
                    "skills_interests": data.get("certifications", ""),
                })
                answers = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in data.items()
                           if k not in ("consent", "event_group") and not k.startswith("event_answer_") and v not in (None, "", [])}
                event_answers = {}
                if event:
                    for question in event.registration_questions.filter(is_active=True):
                        answer = data.get(f"event_answer_{question.key}")
                        if answer not in (None, "", []):
                            event_answers[question.key] = {"label": question.label, "answer": answer}
                event_questions = ([{"key": q.key, "label": q.label, "field_type": q.field_type,
                                     "options": q.options, "required": q.is_required, "position": q.position}
                                    for q in event.registration_questions.filter(is_active=True).order_by("position", "id")]
                                   if event else [])
                submission = RegistrationSubmission.objects.create(
                    form_version=version, participant=participant, event=event,
                    status=RegistrationSubmission.Status.SUBMITTED, answers=answers,
                    answer_snapshot={"questions": questions, "answers": answers, "event_questions": event_questions,
                                     "event_answers": event_answers, "event_group_id": str(data["event_group"].pk) if data.get("event_group") else None},
                    source="public_online" if public else "public",
                    age_classification=classification, submitted_at=timezone.now(),
                    idempotency_key=request_key,
                )
                ConsentAcceptance.objects.create(participant=participant, submission=submission, document=approved,
                    metadata={"document_version": approved.version, "pdf_sha256": approved.content_hash,
                              "source": "public_online" if public else "public"})
                if event:
                    event_registration, created = register_participant(event=event, participant=participant, actor=None, source=EventRegistration.Source.ONLINE if public else EventRegistration.Source.PUBLIC,
                                         group=data.get("event_group"), custom_answers=event_answers)
                    if created:
                        event_registration.ticket_email_opt_in = bool(data.get("ticket_email_opt_in"))
                        event_registration.ticket_sms_opt_in = bool(data.get("ticket_sms_opt_in"))
                        if event_registration.ticket_email_opt_in or event_registration.ticket_sms_opt_in:
                            event_registration.ticket_communication_opt_in_at = timezone.now()
                        event_registration.save(update_fields=["ticket_email_opt_in", "ticket_sms_opt_in", "ticket_communication_opt_in_at"])
                        from apps.events.communications import queue_ticket_delivery
                        queue_ticket_delivery(event_registration)
        except IntegrityError:
            prior = RegistrationSubmission.objects.filter(idempotency_key=request_key).first()
            if prior is None:
                raise
            return _registration_complete(request, prior)
        except ValidationError as exc:
            if not public:
                raise
            form.add_error(None, exc)
        else:
            return _registration_complete(request, submission)
    return render(request, "participants/registration_form.html", {
        "form": form, "event": event, "version": version,
        "consent_document": approved, "progress": 50, "registration_request_key": request_key,
        "public_registration": public,
    })

def _admin_only(request):
    if not request.user.is_authenticated:
        return redirect("admin:login")
    if not request.user.is_superuser:
        raise PermissionDenied

def form_builder(request):
    guard = _admin_only(request)
    if guard: return guard
    form = standard_form()
    return render(request, "internal/registration_forms.html", {"registration_form": form, "questions": form.questions.all().order_by("position", "id"), "versions": form.versions.all().order_by("-version"), "current_version": published_version(form)})

def _protected_changed(request, q):
    try:
        options = json.loads(request.POST.get("options", "[]") or "[]")
        rule = json.loads(request.POST.get("visibility_rule", "{}") or "{}")
        position = int(request.POST.get("position", q.position))
    except (TypeError, ValueError):
        return True
    structural = request.POST.get("field_type") != q.field_type or ("is_required" in request.POST) != q.is_required or ("is_active" in request.POST) != q.is_active
    if q.position not in (None, 0): structural = structural or position != q.position
    if q.options: structural = structural or options != q.options
    if q.visibility_rule: structural = structural or rule != q.visibility_rule
    return structural

def form_question_edit(request, question_id):
    guard = _admin_only(request)
    if guard: return guard
    q = get_object_or_404(RegistrationQuestion, pk=question_id)
    form = RegistrationQuestionForm(request.POST or None, instance=q)
    if q.is_protected and request.method == "POST" and _protected_changed(request, q):
        return render(request, "internal/registration_question_form.html", {"form": form, "question": q, "error": "Protected field structure cannot be changed."}, status=400)
    if request.method == "POST" and form.is_valid():
        form.save(); audit(request.user, "registration.question.updated", q, {"canonical_key": q.canonical_key})
        return redirect("participants:registration-forms")
    return render(request, "internal/registration_question_form.html", {"form": form, "question": q})

def form_question_add(request):
    guard = _admin_only(request)
    if guard: return guard
    form_obj = standard_form()
    form = RegistrationQuestionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        question = form.save(commit=False)
        question.form = form_obj
        base = slugify(request.POST.get("canonical_key", "") or question.label or "custom-question") or "custom-question"
        key, suffix = base, 2
        while RegistrationQuestion.objects.filter(form=form_obj, canonical_key=key).exists():
            key = f"{base}-{suffix}"; suffix += 1
        question.canonical_key = key; question.is_protected = False; question.save()
        audit(request.user, "registration.question.created", question, {"canonical_key": key})
        return redirect("participants:registration-forms")
    return render(request, "internal/registration_question_form.html", {"form": form, "question": None})

def form_publish(request):
    guard = _admin_only(request)
    if guard: return guard
    if request.method != "POST": return redirect("participants:registration-forms")
    form = standard_form()
    prior = form.versions.filter(status=RegistrationFormVersion.Status.PUBLISHED).first()
    if prior:
        prior.status = RegistrationFormVersion.Status.RETIRED; prior.save(update_fields=["status", "updated_at"])
    num = (form.versions.order_by("-version").values_list("version", flat=True).first() or 0) + 1
    qs = form.questions.filter(is_active=True).order_by("position", "id")
    snapshot = {"questions": [{"key": q.canonical_key, "label": q.label, "help_text": q.help_text, "field_type": q.field_type, "options": q.options, "visibility_rule": q.visibility_rule, "required": q.is_required, "protected": q.is_protected, "position": q.position} for q in qs]}
    version = RegistrationFormVersion.objects.create(form=form, version=num, status=RegistrationFormVersion.Status.PUBLISHED, snapshot=snapshot, published_at=timezone.now(), created_by=request.user)
    audit(request.user, "registration.form.published", version, {"version": num})
    return redirect("participants:registration-forms")

def form_preview(request):
    guard = _admin_only(request)
    if guard: return guard
    form = standard_form(); versions = form.versions.all().order_by("-version"); requested = request.GET.get("version")
    version = versions.filter(pk=requested).first() if requested else versions.filter(status=RegistrationFormVersion.Status.PUBLISHED).first()
    version = version or versions.first()
    return render(request, "internal/registration_preview.html", {"version": version, "questions": version.snapshot.get("questions", []) if version else []})
