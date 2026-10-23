"""Admin-only intake configuration in the established internal workspace."""
from copy import deepcopy
from datetime import date
from django.utils import timezone
from functools import wraps
import uuid
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .models import RegistrationForm, RegistrationFormVersion
from .intake_lifecycle import (require_admin, create_form, duplicate_form, save_draft, publish,
                               retire, assign, revision)
from .intake_config_forms import (QuestionConfigForm, SkillRelationsForm, QualificationMappingsForm, AssignmentForm)
from .intake_contracts import validate_contract, PROTECTED
from .intake_wizard import IntakeStepForm, STEP_NAMES, UI_STEP_NAMES, contract_step_for
from .workspace_views import context
from apps.internal_ui.section_editors import has_edit_conflict
from .ui_presentation import intake_editor_groups


def admin_page(view):
    @login_required(login_url="admin:login")
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        require_admin(request.user)
        return view(request, *args, **kwargs)
    return wrapped


def _form(pk):
    return get_object_or_404(RegistrationForm, pk=pk, is_smart_intake=True)


def _latest(form):
    return form.versions.order_by("-version").first()


def _confirmed(request):
    if request.POST.get("confirmed") != "yes":
        raise ValidationError("Confirm the displayed action before proceeding.")


def _message(exc):
    return "; ".join(exc.messages)


@admin_page
@never_cache
@require_http_methods(["GET", "POST"])
def index(request):
    error = ""
    if request.method == "POST":
        try:
            version = create_form(actor=request.user, name=request.POST.get("name"))
        except ValidationError as exc:
            error = _message(exc)
        else:
            return redirect("participants:intake-config-detail", form_id=version.form_id)
    from apps.events.models import CentralEventDownload
    staged = CentralEventDownload.objects.exclude(config={}).order_by("-updated_at")[:50]
    return render(request, "participants/intake_config_index.html", context(request,
        page_title="Form Builder", forms=RegistrationForm.objects.filter(is_smart_intake=True).prefetch_related(
            "versions").order_by("name"), staged_downloads=staged, error=error))


@admin_page
@never_cache
@require_http_methods(["GET", "POST"])
def detail(request, form_id):
    model = _form(form_id)
    latest = _latest(model)
    error = ""
    assignment_form = AssignmentForm(request.POST if request.POST.get("action") == "assign" else None)
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "duplicate":
                draft = duplicate_form(actor=request.user, source_version_id=latest.pk,
                                       name=request.POST.get("name"))
                return redirect("participants:intake-config-detail", form_id=draft.form_id)
            if action == "publish":
                _confirmed(request)
                publish(actor=request.user, version_id=latest.pk, expected_revision=request.POST.get("revision"))
            elif action == "retire":
                _confirmed(request)
                if request.POST.get("revision") != revision(latest):
                    raise ValidationError("This form changed. Reload before retiring.")
                retire(actor=request.user, form_id=model.pk)
            elif action == "assign":
                if not assignment_form.is_valid():
                    raise ValidationError("Check the assignment fields.")
                chosen = get_object_or_404(model.versions, pk=request.POST.get("version_id"), status="published")
                values = assignment_form.cleaned_data
                assign(actor=request.user, version_id=chosen.pk, channel=values["channel"],
                       event=values["event"], trailer=values["trailer"])
            else:
                raise ValidationError("Choose a supported form action.")
        except ValidationError as exc:
            error = _message(exc)
        else:
            messages.success(request, "Form action completed. Trailer downloads still require explicit local Admin confirmation.")
            return redirect("participants:intake-config-detail", form_id=model.pk)
    return render(request, "participants/intake_config_detail.html", context(request,
        page_title=model.name, intake_form=model, version=latest, revision=revision(latest),
        questions=latest.snapshot["questions"], versions=model.versions.order_by("-version"),
        published=model.versions.filter(status="published"), assignments=model.intake_assignments.select_related(
            "event", "trailer", "active_version"), assignment_form=assignment_form, error=error))


@admin_page
@never_cache
@require_http_methods(["GET", "POST"])
def edit(request, form_id, key=None, child=False, mode="question"):
    model = _form(form_id)
    version = _latest(model)
    contract = deepcopy(version.snapshot)
    classes = {"question": QuestionConfigForm, "skills": SkillRelationsForm, "mappings": QualificationMappingsForm}
    if mode not in classes:
        raise ValidationError("Unsupported configuration section.")
    kwargs = {"contract": contract}
    if mode == "question":
        kwargs.update(key=key, child=child)
        if key:
            pool = (next(q for q in contract["questions"] if q["key"] == "experiences")["children"]
                    if child else contract["questions"])
            if not any(q["key"] == key for q in pool):
                from django.http import Http404
                raise Http404
    form = classes[mode](request.POST if request.method == "POST" else None, **kwargs)
    if request.method == "POST" and form.is_valid():
        try:
            new_key = "custom_" + uuid.uuid4().hex[:12] if key is None else None
            result = form.updated_contract(new_key=new_key) if mode == "question" else form.updated_contract()
            draft = save_draft(actor=request.user, form_id=model.pk, contract=result,
                               expected_revision=request.POST.get("revision"))
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Draft saved. Review and publish it before assigning.")
            return redirect("participants:intake-config-detail", form_id=draft.form_id)
    return render(request, "participants/intake_config_edit.html", context(request,
        page_title={"question": "Edit intake question", "skills": "Approved trade skills",
                    "mappings": "Qualification field mappings"}[mode],
        intake_form=model, version=version, form=form, revision=request.POST.get("revision") if form.is_bound else revision(version), save_conflict=has_edit_conflict(form),
        is_protected=key in PROTECTED or child, mode=mode, editor_groups=intake_editor_groups(form,mode)))


@admin_page
@never_cache
@require_http_methods(["POST"])
def reorder(request, form_id, key):
    model = _form(form_id)
    latest = _latest(model)
    contract = deepcopy(latest.snapshot)
    try:
        index = next(i for i, q in enumerate(contract["questions"]) if q["key"] == key)
        direction = request.POST.get("direction")
        if direction == "remove":
            del contract["questions"][index]
        else:
            other = index + (-1 if direction == "up" else 1)
            if direction not in {"up", "down"} or not 0 <= other < len(contract["questions"]):
                raise ValidationError("No question can move in that direction.")
            if contract["questions"][index]["step"] != contract["questions"][other]["step"]:
                raise ValidationError("Use the question editor to change steps.")
            contract["questions"][index], contract["questions"][other] = contract["questions"][other], contract["questions"][index]
        validate_contract(contract)
        save_draft(actor=request.user, form_id=model.pk, contract=contract,
                   expected_revision=request.POST.get("revision"))
    except (ValidationError, StopIteration) as exc:
        messages.error(request, _message(exc) if isinstance(exc, ValidationError) else "Question unavailable.")
    else:
        messages.success(request, "Draft question order updated.")
    return redirect("participants:intake-config-detail", form_id=model.pk)


@admin_page
@never_cache
@require_http_methods(["GET", "POST"])
def preview(request, version_id):
    version = get_object_or_404(RegistrationFormVersion.objects.select_related("form"),
                               pk=version_id, form__is_smart_intake=True)
    try:
        step = int(request.GET.get("step", 1))
    except ValueError:
        step = 1
    if step not in UI_STEP_NAMES:
        step = 1
    persona = request.GET.get("persona", "alex")
    examples = {
        "alex": {"first_name": "SYNTHETIC Alex", "age": 17, "current_situation": "high_school",
                 "hands_on": "yes", "school_name": "SYNTHETIC CTE", "industry_interests": ["welding"]},
        "jordan": {"first_name": "SYNTHETIC Jordan", "age": 22, "current_situation": "employed_other",
                   "current_industry": "retail", "hands_on": "no", "industry_interests": ["electrical"]},
        "chris": {"first_name": "SYNTHETIC Chris", "age": 43, "current_situation": "employed_trade",
                  "current_industry": "construction", "hands_on": "yes", "industry_interests": ["welding"]}}
    if persona not in examples:
        persona = "alex"
    answers = dict(examples[persona])
    age = answers.pop("age")
    answers.update(last_name="Example", dob=date(timezone.localdate().year-age, 1, 1).isoformat(),
        email=persona+"@example.test", phone="55501000"+str(age), city="Example City", state="GA", postal_code="00000",
        career_timeline="exploring", opportunity_types=["exploration"], travel_willingness="maybe",
        travel_range="regional", license_status="none", certifications="not_sure")
    if answers["hands_on"] == "yes":
        answers["experiences"] = [{"entry_id": str(uuid.uuid4()), "area": "welding",
            "source": "school" if persona == "alex" else "employment",
            "paid": "unpaid" if persona == "alex" else "paid",
            "duration": "6_12_months" if persona == "alex" else "over_10_years",
            "skills": ["mig"], "proficiency": "beginner" if persona == "alex" else "advanced"}]
    form = IntakeStepForm(request.POST if request.method == "POST" else None,
                          contract=version.snapshot, step=contract_step_for(step), answers=answers,
                          consent_only=(step == 2) if step <= 2 else None)
    # Preview has no canonical or session writes. Conditions are handled by the same browser module.
    return render(request, "participants/intake_config_preview.html", context(request,
        page_title="Draft preview — " + version.form.name, version=version, form=form,
        questions=form.display_questions(), experience_rows=form.display_rows(),
        experience_question=form.experience_question, contract=version.snapshot,
        step=step, step_name=UI_STEP_NAMES[step], prior_answers=answers, persona=persona))
