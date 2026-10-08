from apps.games.services import configured_game
from django.urls import reverse
from apps.stations.services import complete_activity
from functools import wraps
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from apps.events.models import Event
from apps.events.services import audit_action
from apps.stations.models import EventStation, ExperienceActivity, ExperienceSession, Station
from .forms import CategoryForm, EventCategoryForm, PublishQuestionSetForm, QuestionForm, QuestionSetForm
from .models import (AssessmentCategory, AssessmentResponse, AssessmentSection,
                     EventAssessmentCategory, Question, QuestionSet, QuestionSetItem, QuizAttempt)
from .services import (configuration_readiness, create_attempt, current_section, eligible_event_categories,
                       submit_answer, void_and_restart, _start_section)


def staff_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied
        if not request.user.is_active or not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def _require_admin(request):
    if not request.user.is_active or not request.user.is_superuser:
        raise PermissionDenied


def _authoring(request, template, context):
    return render(request, "assessments/" + template, context)


@staff_required
def dashboard(request):
    categories = AssessmentCategory.objects.all()
    sets = QuestionSet.objects.select_related("category").order_by("category__name", "-version")
    return _authoring(request, "dashboard.html", {"configs": categories, "question_sets": sets,
        "can_edit": request.user.is_superuser})


@staff_required
def category_list(request):
    if request.method == "POST":
        _require_admin(request)
        form = CategoryForm(request.POST)
        if form.is_valid():
            category = form.save()
            audit_action(actor=request.user, action="assessment.category.created", instance=category,
                source="quiz_admin", new_data={"name": category.name, "active": category.is_active})
            messages.success(request, "Assessment category saved.")
            return redirect("assessments:category_list")
    else:
        form = CategoryForm()
    categories = list(AssessmentCategory.objects.all())
    for category in categories:
        category.published_question_count = Question.objects.filter(category=category, is_active=True,
            set_memberships__is_active=True, set_memberships__question_set__status=QuestionSet.Status.PUBLISHED).distinct().count()
        category.draft_question_count = Question.objects.filter(category=category, is_active=True,
            set_memberships__is_active=True, set_memberships__question_set__status=QuestionSet.Status.DRAFT).distinct().count()
    return _authoring(request, "categories.html", {"categories": categories, "form": form,
        "can_edit": request.user.is_superuser})


@staff_required
def category_edit(request, category_id=None):
    _require_admin(request)
    category = get_object_or_404(AssessmentCategory, pk=category_id) if category_id else AssessmentCategory()
    form = CategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        old = {"name": category.name, "active": category.is_active, "display_order": category.display_order}
        form.save()
        audit_action(actor=request.user, action="assessment.category.updated", instance=category,
            source="quiz_admin", old_data=old,
            new_data={"name": category.name, "active": category.is_active, "display_order": category.display_order})
        messages.success(request, "Assessment category updated. Historical attempts remain unchanged.")
        return redirect("assessments:category_list")
    return _authoring(request, "category_form.html", {"form": form, "category": category,
        "historical_usage": category.assessment_sections.exists() if category.pk else False})


@staff_required
def question_set_list(request):
    if request.method == "POST":
        _require_admin(request)
        form = QuestionSetForm(request.POST)
        if form.is_valid():
            question_set = form.save()
            audit_action(actor=request.user, action="assessment.set.created", instance=question_set,
                source="quiz_admin", new_data={"version": question_set.version, "status": question_set.status})
            messages.success(request, "Draft question set created.")
            return redirect("assessments:question_set_detail", set_id=question_set.pk)
    else:
        form = QuestionSetForm()
    sets = QuestionSet.objects.select_related("category").order_by("category__name", "-version")
    return _authoring(request, "question_sets.html", {"form": form, "question_sets": sets,
        "can_edit": request.user.is_superuser})


@staff_required
def question_set_detail(request, set_id):
    question_set = get_object_or_404(QuestionSet.objects.select_related("category"), pk=set_id)
    publish_form = PublishQuestionSetForm(request.POST if request.POST.get("action") == "publish" else None)
    if request.method == "POST":
        _require_admin(request)
        action = request.POST.get("action")
        try:
            if action == "publish":
                if publish_form.is_valid():
                    question_set.publish(effective_at=publish_form.cleaned_data["effective_at"])
                    audit_action(actor=request.user, action="assessment.set.published", instance=question_set,
                        source="quiz_admin", new_data={"version": question_set.version,
                            "effective_at": question_set.effective_at.isoformat()})
                    messages.success(request, "Question set published or scheduled.")
            elif action == "retire":
                question_set.retire()
                audit_action(actor=request.user, action="assessment.set.retired", instance=question_set,
                    source="quiz_admin", new_data={"version": question_set.version})
                messages.success(request, "Question set retired.")
            elif action == "new_version":
                with transaction.atomic():
                    new = QuestionSet.objects.create(category=question_set.category,
                        version=(QuestionSet.objects.filter(category=question_set.category).order_by("-version")
                            .values_list("version", flat=True).first() or 0) + 1, name=question_set.name)
                    for item in question_set.items.select_related("question").prefetch_related("question__choices"):
                        source = item.question
                        clone = Question.objects.create(category=source.category, text=source.text,
                            image=source.image, difficulty=source.difficulty, is_active=source.is_active)
                        for choice in source.choices.all():
                            clone.choices.create(text=choice.text, is_correct=choice.is_correct,
                                display_order=choice.display_order)
                        QuestionSetItem.objects.create(question_set=new, question=clone, is_active=item.is_active)
                    audit_action(actor=request.user, action="assessment.set.version_created", instance=new,
                        source="quiz_admin", new_data={"source_version": question_set.version, "version": new.version})
                messages.success(request, f"Created Draft version {new.version}.")
                return redirect("assessments:question_set_detail", set_id=new.pk)
            else:
                raise ValidationError("Choose a supported content action.")
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        if action != "publish" or publish_form.is_valid():
            return redirect("assessments:question_set_detail", set_id=question_set.pk)
    report = question_set.readiness()
    used_ids = {row.get("source_question_id") for snapshot in
        AssessmentSection.objects.filter(question_set=question_set).values_list("questions_snapshot", flat=True)
        for row in (snapshot or []) if isinstance(row, dict)}
    items = list(question_set.items.select_related("question").order_by("question__difficulty", "question__created_at"))
    for item in items:
        item.historically_used = str(item.question_id) in used_ids
    return _authoring(request, "question_set_detail.html", {"question_set": question_set,
        "report": report, "publish_form": publish_form, "can_edit": request.user.is_superuser,
        "items": items})


@staff_required
def question_create(request, set_id):
    _require_admin(request)
    question_set = get_object_or_404(QuestionSet.objects.select_related("category"), pk=set_id)
    if question_set.status != QuestionSet.Status.DRAFT:
        raise PermissionDenied
    form = QuestionForm(request.POST or None, request.FILES or None, category=question_set.category)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            question = form.save()
            QuestionSetItem.objects.create(question_set=question_set, question=question)
            audit_action(actor=request.user, action="assessment.question.created", instance=question,
                source="quiz_admin", new_data={"set_version": question_set.version,
                    "difficulty": question.difficulty, "has_image": bool(question.image)})
        messages.success(request, "Question added to the Draft set.")
        return redirect("assessments:question_set_detail", set_id=question_set.pk)
    return _authoring(request, "question_form.html", {"form": form, "question_set": question_set})


@staff_required
def question_edit(request, set_id, question_id):
    _require_admin(request)
    question_set = get_object_or_404(QuestionSet, pk=set_id)
    item = get_object_or_404(QuestionSetItem, question_set=question_set, question_id=question_id)
    if question_set.status != QuestionSet.Status.DRAFT:
        raise PermissionDenied
    form = QuestionForm(request.POST or None, request.FILES or None, instance=item.question,
        category=question_set.category)
    if request.method == "POST" and form.is_valid():
        old = {"text": item.question.text, "difficulty": item.question.difficulty,
            "image": item.question.image.name, "active": item.question.is_active}
        with transaction.atomic():
            question = form.save()
            item.is_active = request.POST.get("membership_active") == "on"
            item.save(update_fields=("is_active",))
            audit_action(actor=request.user, action="assessment.question.updated", instance=question,
                source="quiz_admin", old_data=old,
                new_data={"text": question.text, "difficulty": question.difficulty,
                    "image": question.image.name, "active": question.is_active, "membership_active": item.is_active})
        messages.success(request, "Draft question updated.")
        return redirect("assessments:question_set_detail", set_id=question_set.pk)
    return _authoring(request, "question_form.html", {"form": form, "question_set": question_set, "item": item})


@staff_required
def event_configuration(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    locked = event.is_current or event.status in (Event.Status.COMPLETED, Event.Status.ARCHIVED)
    if request.method == "POST":
        _require_admin(request)
        if locked and (request.POST.get("override_active") != "on" or not request.POST.get("override_reason", "").strip()):
            raise PermissionDenied("A locked Event requires an explicit Admin override and reason.")
    config_id = request.GET.get("edit")
    instance = get_object_or_404(EventAssessmentCategory, pk=config_id, event=event) if config_id else None
    form = EventCategoryForm(request.POST or None, instance=instance, event=event)
    if request.method == "POST" and form.is_valid():
        old = None if instance is None else {"set_id": str(instance.question_set_id),
            "pool_mode": instance.pool_mode, "enabled": instance.enabled}
        try:
            with transaction.atomic():
                config = form.save(commit=False)
                config.event = event
                config.full_clean()
                config.save()
                form.save_m2m()
                readiness = configuration_readiness(config)
                if not readiness["ready"]:
                    raise ValidationError("This Event pool needs at least 15 valid active questions.")
                audit_action(actor=request.user,
                    action="assessment.event_config.overridden" if locked else "assessment.event_config.saved",
                    instance=config, source="quiz_admin", old_data=old,
                    new_data={"event_id": str(event.pk), "set_id": str(config.question_set_id),
                        "pool_mode": config.pool_mode, "curated_count": config.curated_questions.count()},
                    reason=request.POST.get("override_reason", "").strip() if locked else "")
            messages.success(request, "Event quiz configuration saved.")
            return redirect("assessments:event_configuration", event_id=event.pk)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, "; ".join(getattr(exc, "messages", [str(exc)])))
    configs = EventAssessmentCategory.objects.filter(event=event).select_related("category", "question_set")
    rows = [(config, configuration_readiness(config)) for config in configs]
    return _authoring(request, "event_configuration.html", {"event": event, "form": form,
        "configs": configs, "rows": rows, "locked": locked, "can_edit": request.user.is_superuser})


@staff_required
@require_GET
def question_preview(request, question_id):
    question = get_object_or_404(Question.objects.select_related("category").prefetch_related("choices"), pk=question_id)
    from types import SimpleNamespace
    image_url = question.image.url if question.image else ""
    payload = {"position": 1, "text": question.text, "image_url": image_url}
    choices = [{"key": str(choice.pk), "text": choice.text} for choice in question.choices.all()]
    section = SimpleNamespace(category_name_snapshot=question.category.name, position=1)
    return render(request, "assessments/question.html", {"question": payload,
        "choices": choices, "section": section, "remaining_seconds": 45,
        "preview_mode": True, "station_code": "PREVIEW", "countdown_active": False})


def _kiosk_session(request, station_code, session_id):
    session_key = str(session_id)
    is_test_mode = request.session.get("test_kiosk_session") == session_key
    is_participant_mode = request.session.get("participant_kiosk_session") == session_key
    if not (is_test_mode or is_participant_mode):
        raise Http404
    station = get_object_or_404(Station, code=station_code, station_type=Station.Type.KIOSK, is_active=True)
    session = get_object_or_404(ExperienceSession.objects.select_related("event", "participant"), pk=session_id)
    if is_test_mode:
        if session.mode != ExperienceSession.Mode.STAFF_TEST or session.registration_id or session.participant_id:
            raise Http404
        from apps.participants.models import TestParticipantRun
        if TestParticipantRun.objects.filter(experience_session=session, reset_at__isnull=False).exists():
            raise Http404
    elif session.mode != ExperienceSession.Mode.OFFICIAL or session.registration_id is None:
        raise Http404
    if not EventStation.objects.filter(event=session.event, station=station, enabled=True).exists():
        raise Http404
    return station, session


def _session_key(session_id):
    return f"quiz_selection_{session_id}"


def _current_attempt(session):
    return QuizAttempt.objects.filter(experience_session=session, is_official=(session.mode == ExperienceSession.Mode.OFFICIAL), voided_at__isnull=True).prefetch_related("sections__responses").first()


def _participant_first_name(session):
    participant = getattr(session, "participant", None)
    if participant is None:
        return ""
    return (getattr(participant, "preferred_name", "") or getattr(participant, "first_name", "") or "").split(" ")[0]


def station_start(request, station_code, session_id):
    station, session = _kiosk_session(request, station_code, session_id)
    attempt = _current_attempt(session)
    if attempt:
        if attempt.status == QuizAttempt.Status.COMPLETE:
            return redirect("assessments:results", station_code=station.code, session_id=session.pk)
        return redirect("assessments:attempt", station_code=station.code, session_id=session.pk)
    if request.method == "POST":
                return redirect("assessments:category_selection", station_code=station.code, session_id=session.id)
    categories = eligible_event_categories(session.event)
    return render(request, "assessments/instructions.html", {"station": station, "session": session, "category_count": len(categories), "first_name": _participant_first_name(session), "game_configuration": configured_game(session.event)})


def category_selection(request, station_code, session_id):
    station, session = _kiosk_session(request, station_code, session_id)
    if _current_attempt(session):
        return redirect("assessments:attempt", station_code=station.code, session_id=session.pk)
    categories = eligible_event_categories(session.event)
    if len(categories) < 2:
        return render(request, "assessments/not_ready.html", {"station": station, "session": session}, status=503)
    if request.method == "POST":
        selected = request.POST.getlist("categories")
        first, second = (selected if len(selected) == 2 else (None, None)) if selected else (request.POST.get("first_category"), request.POST.get("second_category"))
        available = {str(config.pk): config for config in categories}
        if not first or not second or first == second or first not in available or second not in available:
            messages.error(request, "Choose two different areas to continue.")
        else:
            try:
                create_attempt(
                    session,
                    [first, second],
                    is_official=(session.mode == ExperienceSession.Mode.OFFICIAL),
                    actor=None,
                    start_first_section=False,
                )
                request.session.pop(_session_key(session.pk), None)
                return redirect("assessments:attempt", station_code=station.code, session_id=session.pk)
            except (ValidationError, IntegrityError) as exc:
                messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    return render(request, "assessments/category_selection.html", {"station": station, "configs": categories})


def ready(request, station_code, session_id):
    station, session = _kiosk_session(request, station_code, session_id)
    if _current_attempt(session):
        return redirect("assessments:attempt", station_code=station.code, session_id=session.pk)
    selected = request.session.get(_session_key(session.pk), [])
    configs = eligible_event_categories(session.event)
    available = {str(c.pk): c for c in configs}
    if len(selected) != 2 or any(item not in available for item in selected):
        return redirect("assessments:category_selection", station_code=station.code, session_id=session.pk)
    chosen = [available[item] for item in selected]
    if request.method == "POST":
        try:
            attempt = create_attempt(session, selected, is_official=(session.mode == ExperienceSession.Mode.OFFICIAL), actor=None, start_first_section=False)
            del request.session[_session_key(session.pk)]
            return redirect("assessments:attempt", station_code=station.code, session_id=session.pk)
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", ["Assessment setup needs staff assistance."])))
            return redirect("assessments:category_selection", station_code=station.code, session_id=session.pk)
    return render(request, "assessments/ready.html", {"station": station, "chosen": chosen})


def _participant_question_payload(question):
    public_question = {
        key: value
        for key, value in question.items()
        if key not in {"source_question_id", "id", "choices"}
    }
    public_question["choices"] = [
        {"key": str(index), "text": choice["text"]}
        for index, choice in enumerate(question["choices"])
    ]
    return public_question


def _snapshot_choice_key(question, public_choice_key):
    return next(
        (choice["key"] for index, choice in enumerate(question["choices"]) if str(index) == public_choice_key),
        "",
    )


def attempt_view(request, station_code, session_id):
    station, session = _kiosk_session(request, station_code, session_id)
    attempt = _current_attempt(session)
    if not attempt:
        return redirect("assessments:station_start", station_code=station.code, session_id=session.pk)
    if attempt.status == QuizAttempt.Status.COMPLETE:
        return redirect("assessments:results", station_code=station.code, session_id=session.pk)
    section = current_section(attempt)
    countdown_active = False
    if section is None:
        section = attempt.sections.filter(status=AssessmentSection.Status.PENDING).order_by("position").first()
        if section is None:
            return redirect("assessments:results", station_code=station.code, session_id=session.pk)
        countdown_active = True
    answered = set(section.responses.values_list("question_position", flat=True))
    question = next((item for item in section.questions_snapshot if item["position"] not in answered), None)
    if request.method == "POST" and not countdown_active:
        if request.POST.get("action") == "expire":
            current_section(attempt)
            return redirect("assessments:attempt", station_code=station.code, session_id=session.pk)
        if request.POST.get("action") == "answer" and question:
            submit_answer(section, request.POST.get("question_position"), _snapshot_choice_key(question, request.POST.get("choice_key", "")))
            return redirect("assessments:attempt", station_code=station.code, session_id=session.pk)
    if question is None:
        return redirect("assessments:results", station_code=station.code, session_id=session.pk)
    public_question = _participant_question_payload(question)
    remaining = 45 if countdown_active else max(0, int((section.deadline_at - timezone.now()).total_seconds() + .999))
    return render(request, "assessments/question.html", {
        "station": station, "attempt": attempt, "section": section,
        "question": public_question, "choices": public_question["choices"],
        "remaining_seconds": remaining, "answered": len(answered),
        "total": len(section.questions_snapshot), "countdown_active": countdown_active,
        "countdown_start_url": reverse("assessments:attempt_start", kwargs={"station_code": station.code, "session_id": session.pk}) if countdown_active else "",
    })


@require_POST
@transaction.atomic
def start_pending_section(request, station_code, session_id):
    station, session = _kiosk_session(request, station_code, session_id)
    attempt = _current_attempt(session)
    if attempt:
        attempt = QuizAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt.status == QuizAttempt.Status.ACTIVE and not attempt.sections.filter(status=AssessmentSection.Status.ACTIVE).exists():
            pending = attempt.sections.filter(status=AssessmentSection.Status.PENDING).order_by("position").first()
            if pending and str(pending.pk) == request.POST.get("section_id"):
                _start_section(pending, timezone.now())
    return redirect("assessments:attempt", station_code=station.code, session_id=session.pk)

def results(request, station_code, session_id):
    station, session = _kiosk_session(request, station_code, session_id)
    attempt = _current_attempt(session)
    if not attempt or attempt.status != QuizAttempt.Status.COMPLETE:
        return redirect("assessments:attempt", station_code=station.code, session_id=session.pk) if attempt else redirect("assessments:station_start", station_code=station.code, session_id=session.pk)
    sections = list(attempt.sections.order_by("position"))
    if session.mode == ExperienceSession.Mode.OFFICIAL:
        complete_activity(session=session, activity_name=ExperienceActivity.Activity.KIOSK, actor=None)
    return render(request, "assessments/results.html", {"station": station, "attempt": attempt, "sections": sections, "session": session, "first_name": _participant_first_name(session), "game_configuration": configured_game(session.event)})


def game_handoff(request, station_code, session_id):
    station, session = _kiosk_session(request, station_code, session_id)
    attempt = _current_attempt(session)
    if not attempt or attempt.status != QuizAttempt.Status.COMPLETE:
        return redirect("assessments:station_start", station_code=station.code, session_id=session.pk)
    if configured_game(session.event):
        return redirect("games:launch", station_code=station.code, session_id=session.pk)
    return redirect("games:simulator_next", station_code=station.code, session_id=session.pk)

def finish(request, station_code, session_id):
    station, session = _kiosk_session(request, station_code, session_id)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    request.session.pop("participant_kiosk_session", None)
    return redirect("stations:kiosk", station_code=station.code)


@staff_required
def staff_restart(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt.objects.select_related("experience_session"), pk=attempt_id)
    if request.method == "POST":
        try:
            reason = request.POST.get("reason", "")
            attempt = void_and_restart(attempt, actor=request.user, reason=reason)
            messages.success(request, "Assessment voided and replacement started.")
            return redirect("assessments:staff_restart", attempt_id=attempt.pk)
        except (ValidationError, PermissionDenied, IntegrityError) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", ["Assessment could not be restarted."])))
    return _authoring(request, "restart.html", {"attempt": attempt})


@staff_required
def question_set_new(request, category_id):
    _require_admin(request)
    category = get_object_or_404(AssessmentCategory, pk=category_id)
    data = request.POST.copy() if request.method == "POST" else None
    if data is not None:
        data["category"] = str(category.pk)
    form = QuestionSetForm(data, initial={"category": category})
    if request.method == "POST" and form.is_valid():
        question_set = form.save()
        audit_action(actor=request.user, action="assessment.set.created", instance=question_set,
            source="quiz_admin", new_data={"version": question_set.version, "status": question_set.status})
        return redirect("assessments:question_set_detail", set_id=question_set.pk)
    return _authoring(request, "question_set_form.html", {"form": form, "category": category})


# Compatibility for callers that used the earlier Duck-specific route name.
duck_handoff = game_handoff
