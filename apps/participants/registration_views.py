import json
from datetime import date
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from apps.core.audit import record_audit
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
    if request.method == "POST":
        return redirect("participants:registration-form" + (("?event=" + str(event.pk)) if event else ""))
    return render(request, "participants/registration_start.html", {"event": event})

def registration_form(request):
    form_model = standard_form()
    version = published_version(form_model)
    event_id = request.GET.get("event") or request.POST.get("event")
    event = None
    if event_id:
        from apps.events.models import Event
        event = get_object_or_404(Event, pk=event_id)
    approved = ConsentDocumentVersion.objects.filter(key="btw-intake", is_approved=True).order_by("-effective_at", "-version").first()
    if not version or not approved:
        return render(request, "participants/registration_unavailable.html", {"event": event, "reason": "Registration is temporarily unavailable while BTW updates its approved consent information."}, status=503)
    questions = version.snapshot.get("questions", [])
    form = SnapshotRegistrationForm(request.POST or None, questions=questions, consent_document=approved)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        age = age_for(data["date_of_birth"])
        classification = "minor" if age < 18 else "adult"
        with transaction.atomic():
            participant = Participant.objects.create(first_name=data.get("first_name", ""), last_name=data.get("last_name", ""), preferred_name=data.get("preferred_name", ""), contact_email=data.get("contact_email", ""), contact_phone=data.get("contact_phone", ""), city=data.get("city", ""), state=data.get("state", ""), postal_code=data.get("postal_code", ""), date_of_birth=data["date_of_birth"], life_stage=data.get("life_stage", ""), age_classification=classification)
            ParticipantCareerProfile.objects.update_or_create(participant=participant, defaults={"employment_status": data.get("employment_status", ""), "career_interests": data.get("career_interests", ""), "willing_to_travel": data.get("willing_to_travel") == "yes", "education_training": data.get("education_training", ""), "skills_interests": data.get("certifications", "")})
            answers = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in data.items() if k != "consent" and v not in (None, "", [])}
            submission = RegistrationSubmission.objects.create(form_version=version, participant=participant, event=event, status=RegistrationSubmission.Status.SUBMITTED, answers=answers, answer_snapshot={"questions": questions, "answers": answers}, age_classification=classification, submitted_at=timezone.now())
            ConsentAcceptance.objects.create(participant=participant, submission=submission, document=approved)
        return render(request, "participants/registration_complete.html", {"participant": participant, "submission": submission, "legal_pending": False})
    return render(request, "participants/registration_form.html", {"form": form, "event": event, "version": version, "consent_document": approved, "progress": 50})

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
