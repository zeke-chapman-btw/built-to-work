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
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods
from .models import RegistrationForm, RegistrationFormVersion
from .intake_lifecycle import (require_admin, create_form, duplicate_form, save_draft, publish,
                               retire, assign, revision)
from .intake_labels import destination_text
from .intake_config_forms import (QuestionConfigForm, SkillRelationsForm, QualificationMappingsForm, AssignmentForm)
from .intake_contracts import validate_contract, PROTECTED
from .intake_wizard import IntakeStepForm, STEP_NAMES, UI_STEP_NAMES, contract_step_for
from .workspace_views import context
from apps.internal_ui.section_editors import has_edit_conflict
from .ui_presentation import intake_editor_groups


def participant_step(question):
    source_step = question.get('step')
    return 2 if question.get('type') == 'consent' else 1 if source_step == 1 else source_step + 1 if isinstance(source_step, int) else 0


def _rule_text(rule, by_key):
    """'Only if' wording for a conditional question."""
    if not rule:
        return ""
    if rule.get("op") == "adult_non_student":
        return "Only if: an adult who isn't a student"
    parent = by_key.get(rule.get("field"))
    if not parent:
        return "Only sometimes"
    options = {o["value"]: o["label"] for o in parent.get("options", [])}
    values = rule.get("value") if isinstance(rule.get("value"), list) else [rule.get("value")]
    if parent["key"] == "current_situation":
        known = {frozenset(["high_school", "college", "trade_school"]): "a student",
                 frozenset(["employed_trade", "employed_other"]): "working",
                 frozenset(["employed_other"]): "working outside the trades"}
        if frozenset(values) in known:
            return "Only if: " + known[frozenset(values)]
    shown = " or ".join(f"“{options.get(v, str(v))}”" for v in values)
    return f"Only if {shown} is picked above"


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
            from .intake_defaults import recommended_contract
            from .intake_trades import with_trades
            version = create_form(actor=request.user, name=request.POST.get("name"), snapshot=with_trades(recommended_contract()))
        except ValidationError as exc:
            error = _message(exc)
        else:
            return redirect("participants:intake-config-detail", form_id=version.form_id)
    from apps.events.models import CentralEventDownload
    staged = CentralEventDownload.objects.exclude(config={}).order_by("-updated_at")[:50]
    places = _signup_places()
    return render(request, "participants/intake_config_index.html", context(request,
        page_title="Forms", form_rows=_form_rows(), staged_downloads=staged, error=error,
        place_rows=_place_rows(places), **places))


def _form_rows():
    """One line per form: status, size and where it's used."""
    from .models import IntakeFormAssignment
    rows = []
    live = list(IntakeFormAssignment.objects.filter(is_active=True).values_list("form_id", "channel"))
    for form in RegistrationForm.objects.filter(is_smart_intake=True).prefetch_related("versions").order_by("-is_active", "name"):
        versions = sorted(form.versions.all(), key=lambda v: v.version)
        latest = versions[-1] if versions else None
        published = [v for v in versions if v.status == "published"]
        questions = (latest.snapshot or {}).get("questions", []) if latest else []
        steps = len({participant_step(q) for q in questions})
        channels = [channel for form_id, channel in live if form_id == form.pk]
        used = []
        if "online" in channels:
            used.append("General online")
        events = channels.count("event")
        if events:
            used.append(f"{events} event{'s' if events != 1 else ''}")
        tablets = sum(1 for c in channels if c in ("staff", "trailer"))
        if tablets:
            used.append(f"{tablets} tablet{'s' if tablets != 1 else ''}")
        if not form.is_active:
            status, tone = "Retired", "neutral"
        elif published and latest.status == "draft":
            status, tone = "Live · draft open", "warning"
        elif published:
            status, tone = "Live", "success"
        else:
            status, tone = "Draft", "warning"
        rows.append({"form": form, "latest": latest, "status": status, "tone": tone,
                     "summary": f"{len(questions)} questions · {steps} steps · v{latest.version if latest else 0}",
                     "used": " · ".join(used)})
    return rows


def _place_rows(places):
    """Every sign-up place as one row for the filterable table."""
    from django.urls import reverse
    from apps.events.templatetags.ticket_ui import event_dates, event_place
    rows = []
    general = places["general_assignment"]
    rows.append({"kind": "online", "kind_label": "Online", "name": "General sign-up link", "sub": "Website, social posts, anyone not at an event",
                 "form": general.form.name if general else "", "status": "open" if general else "none",
                 "links": [("QR", reverse("participants:signup-qr-sheet", args=["general"]), True)]
                          + ([("Preview", reverse("participants:intake-config-preview", args=[general.active_version_id]), False)] if general else [])
                          + [("Change", reverse("participants:signup-where", args=["general"]), "editor")]})
    for place in places["event_places"]:
        event = place["event"]
        where = event_place(event)
        links = [] if place["open"] else [("Open it", reverse("events:event_detail", args=[event.pk]), False)]
        links += [("QR", reverse("participants:signup-qr-sheet", args=["event", event.pk]), True)]
        if place["own"]:
            links.append(("Preview", reverse("participants:intake-config-preview", args=[place["own"].active_version_id]), False))
        links.append(("Change", reverse("participants:signup-where", args=["event", event.pk]), "editor"))
        rows.append({"kind": "event", "kind_label": "Event", "name": event.name, "sub": event_dates(event) + (" · " + where if where else ""),
                     "form": place["own"].form.name if place["own"] else ("Same as online" if general else ""),
                     "status": "open" if place["open"] else "closed", "links": links})
    for place in places["trailer_places"]:
        rows.append({"kind": "trailer", "kind_label": "Trailer sign", "name": str(place["trailer"]),
                     "sub": ("Today: " + place["event"].name) if place["event"] else "Between events",
                     "form": "Follows the event", "status": "open",
                     "links": [("QR", reverse("participants:signup-qr-sheet", args=["trailer", place["trailer"].pk]), True)]})
    for a in places["staff_places"]:
        rows.append({"kind": "tablet", "kind_label": "Tablet", "name": (a.event.name if a.event else "All events") + (f" · {a.trailer}" if a.trailer else ""),
                     "sub": a.get_channel_display(), "form": a.form.name, "status": "open",
                     "links": [("Open form", reverse("participants:intake-config-detail", args=[a.form_id]), False)]})
    return rows


def _signup_places():
    """Where each form is used, for the Forms page."""
    from apps.events.models import TrailerInstance
    from .models import IntakeFormAssignment
    from . import signup_links as links
    live = list(IntakeFormAssignment.objects.filter(is_active=True).select_related("form", "active_version", "event", "trailer"))
    for a in live:
        a.label = f"{a.form.name} · version {a.active_version.version}"
    general = next((a for a in live if a.channel == "online"), None)
    own = {a.event_id: a for a in live if a.channel == "event"}
    from .intake_submission import _public_event

    def is_open(event):
        try:
            _public_event(event)
        except ValidationError:
            return False
        return True
    events = [{"event": e, "own": own.get(e.pk), "open": is_open(e)} for e in links.public_events()]
    trailers = [{"trailer": t, "event": links.trailer_event(t)} for t in TrailerInstance.objects.filter(is_active=True).order_by("display_name")]
    staffed = [a for a in live if a.channel in {"staff", "trailer"}]
    return {"general_assignment": general, "event_places": events, "trailer_places": trailers, "staff_places": staffed}


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
            if action == "prepare_identifiers":
                from .identifier_contracts import prepare_identifier_draft
                draft = prepare_identifier_draft(actor=request.user, form_id=model.pk, expected_revision=request.POST.get("revision"))
                messages.success(request, "Compatible identifier draft prepared. Nothing was published or reassigned.")
                return redirect("participants:intake-config-detail", form_id=draft.form_id)
            if action == "use_recommended":
                from .intake_defaults import recommended_contract
                from .intake_contracts import IDENTIFIER_SCHEMA
                contract = recommended_contract()
                if latest.snapshot.get("schema") == IDENTIFIER_SCHEMA:
                    from .identifier_contracts import identifier_contract
                    contract = identifier_contract(contract)
                # Keep this form's own skill lists and qualification mappings.
                contract["area_skills"] = deepcopy(latest.snapshot.get("area_skills", contract["area_skills"]))
                contract["qualification_mappings"] = deepcopy(latest.snapshot.get("qualification_mappings", []))
                from .intake_trades import with_trades
                contract = with_trades(contract)
                save_draft(actor=request.user, form_id=model.pk, contract=contract, expected_revision=request.POST.get("revision"))
                messages.success(request, "Draft made with the recommended questions. Preview it, then publish when you're happy. The live form hasn't changed.")
                return redirect("participants:intake-config-detail", form_id=model.pk)
            if action == "update_trades":
                from .intake_trades import with_trades
                save_draft(actor=request.user, form_id=model.pk, contract=with_trades(latest.snapshot),
                           expected_revision=request.POST.get("revision"))
                messages.success(request, "Trade choices updated in a draft. Preview it, then publish. The live form hasn't changed.")
                return redirect("participants:intake-config-detail", form_id=model.pk)
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
    # Display the existing five participant-facing steps without rewriting the
    # retained schema, question ordering within a step, keys or conditions.
    labels = UI_STEP_NAMES
    grouped = {number: [] for number in labels}
    kind_labels = {'short_text': 'Short answer', 'long_text': 'Long answer', 'single_choice': 'Choose one',
                   'multiple_choice': 'Choose several', 'consent': 'Consent', 'repeat_group': 'Repeating list'}
    by_key = {q['key']: q for q in latest.snapshot['questions']}
    for question in latest.snapshot['questions']:
        number = participant_step(question)
        question = {**question, 'rule_text': _rule_text(question.get('rule'), by_key)}
        # Display-only copy: the stored snapshot is never changed.
        grouped.setdefault(number, []).append({**question, 'destination_text': destination_text(question.get('destination')), 'kind_label': kind_labels.get(question.get('type'), str(question.get('type', '')).replace('_', ' ').capitalize())})
    question_groups = [{'number': number, 'title': labels.get(number, 'Other retained questions'),
                        'anchor': f'step-{number}', 'questions': grouped[number]} for number in sorted(grouped) if grouped[number]]
    from .intake_defaults import RETIRED_FROM_RECOMMENDED
    keys = set(by_key)
    can_use_recommended = model.is_active and bool(keys & set(RETIRED_FROM_RECOMMENDED))
    from .intake_trades import trades_out_of_date
    trades_stale = model.is_active and not can_use_recommended and trades_out_of_date(latest.snapshot)
    return render(request, "participants/intake_config_detail.html", context(request,
        page_title=model.name, intake_form=model, version=latest, revision=revision(latest),
        questions=latest.snapshot["questions"], question_groups=question_groups, can_use_recommended=can_use_recommended, trades_stale=trades_stale,
        version_label=("Draft · v" if latest.status == "draft" else "Live · v") + str(latest.version),
        question_total=len(latest.snapshot["questions"]), versions=model.versions.order_by("-version"),
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
        page_title={"question": "Edit question" if key else "New question", "skills": "Trade skills",
                    "mappings": "Profile qualifications"}[mode],
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
            positions = [i for i, question in enumerate(contract['questions'])
                         if participant_step(question) == participant_step(contract['questions'][index])]
            neighbor = positions.index(index) + (-1 if direction == 'up' else 1)
            if direction not in {'up', 'down'} or not 0 <= neighbor < len(positions):
                raise ValidationError('No question can move in that direction within this step. Use the question editor to change steps.')
            other = positions[neighbor]
            contract["questions"][index], contract["questions"][other] = contract["questions"][other], contract["questions"][index]
        validate_contract(contract)
        save_draft(actor=request.user, form_id=model.pk, contract=contract,
                   expected_revision=request.POST.get("revision"))
    except (ValidationError, StopIteration) as exc:
        messages.error(request, _message(exc) if isinstance(exc, ValidationError) else "Question unavailable.")
    else:
        messages.success(request, "Draft question order updated.")
    return redirect("participants:intake-config-detail", form_id=model.pk)


PERSONAS = {
    "alex": ("Alex · 17 · High school / CTE", {"first_name": "SYNTHETIC Alex", "age": 17, "current_situation": "high_school",
             "hands_on": "yes", "school_name": "SYNTHETIC CTE", "industry_interests": ["welding"]}),
    "jordan": ("Jordan · 22 · Retail, interested in electrical", {"first_name": "SYNTHETIC Jordan", "age": 22, "current_situation": "employed_other",
               "current_industry": "retail", "hands_on": "no", "industry_interests": ["electrical"]}),
    "chris": ("Chris · 43 · Welding supervisor", {"first_name": "SYNTHETIC Chris", "age": 43, "current_situation": "employed_trade",
              "current_industry": "construction", "hands_on": "yes", "industry_interests": ["welding"]}),
}


def _example_answers(persona):
    answers = dict(PERSONAS[persona][1])
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
    return answers


SCREENS = [("welcome", "Welcome")] + [(str(n), f"{n}. {name}") for n, name in UI_STEP_NAMES.items()] + [("finish", "Finish")]


@admin_page
@never_cache
def preview(request, version_id):
    """Phone-sized preview of the whole sign-up: welcome, every step and the finish screen."""
    version = get_object_or_404(RegistrationFormVersion.objects.select_related("form"),
                               pk=version_id, form__is_smart_intake=True)
    screen = request.GET.get("screen") or request.GET.get("step") or "welcome"
    if screen not in dict(SCREENS):
        screen = "welcome"
    persona = request.GET.get("persona", "alex")
    if persona not in PERSONAS:
        persona = "alex"
    others = version.form.versions.exclude(pk=version.pk).filter(status__in=["draft", "published"]).order_by("-version")
    return render(request, "participants/intake_config_preview.html", context(request,
        page_title="Preview — " + version.form.name, version=version, screen=screen, screens=SCREENS,
        persona=persona, personas=[(k, v[0]) for k, v in PERSONAS.items()], other_versions=others,
        can_edit_wording=version.form.is_active, latest=_latest(version.form)))


@xframe_options_sameorigin
@admin_page
@never_cache
def preview_screen(request, version_id):
    """One participant screen exactly as the public sees it, filled with a made-up person. Nothing is saved."""
    from types import SimpleNamespace
    from .intake_screens import public_screens
    from .models import ConsentDocumentVersion
    version = get_object_or_404(RegistrationFormVersion.objects.select_related("form"),
                               pk=version_id, form__is_smart_intake=True)
    screen = request.GET.get("screen", "welcome")
    persona = request.GET.get("persona", "alex") if request.GET.get("persona") in PERSONAS else "alex"
    screens = public_screens(version)
    from django.urls import reverse
    base = {"preview": True, "screens": screens, "event": None,
            "preview_base": reverse("participants:intake-config-preview", args=[version.pk])}
    if screen == "welcome":
        return render(request, "participants/intake_start.html", {**base, "assignment": None})
    if screen == "finish":
        return render(request, "participants/intake_complete.html", base)
    try:
        step = int(request.GET.get("step") or screen)
    except ValueError:
        step = 1
    if step not in UI_STEP_NAMES:
        step = 1
    answers = _example_answers(persona)
    consent = ConsentDocumentVersion.objects.filter(key="btw-intake", is_approved=True).order_by("-effective_at", "-version").first()
    form = IntakeStepForm(None, contract=version.snapshot, step=contract_step_for(step), answers=answers,
                          consent_only=(step == 2) if step <= 2 else None)
    fake_session = SimpleNamespace(consent_document=consent or SimpleNamespace(
        title="Consent", body="The approved consent text appears here. Add one under Operations → Consents."))
    return render(request, "participants/intake_step.html", {**base,
        "session": fake_session, "form": form, "step": step, "step_name": UI_STEP_NAMES[step],
        "progress": step * 20, "questions": form.display_questions(), "experience_rows": form.display_rows(),
        "experience_question": form.experience_question, "event_fields": [], "contract": version.snapshot,
        "prior_answers": answers, "revision": "", "previous_step": step - 1})


class ScreensForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, current=None, **kwargs):
        from .intake_screens import DEFAULTS, LABELS, LIMITS
        super().__init__(*args, **kwargs)
        for key, default in DEFAULTS.items():
            long = key.endswith("message")
            self.fields[key] = forms.CharField(label=LABELS[key], required=False, max_length=LIMITS[key],
                initial=(current or {})[key] if key in (current or {}) else default,
                widget=forms.Textarea(attrs={"rows": 3}) if long else forms.TextInput())
        self.fields["welcome_challenge"].help_text = "Leave blank to hide this line."
        self.fields["finish_message"].help_text = "Shown when there is no event ticket. With a ticket, people see their ticket instead."

    @property
    def field_groups(self):
        welcome = [self[k] for k in self.fields if k.startswith("welcome")]
        finish = [self[k] for k in self.fields if k.startswith("finish")]
        return [{"title": "Welcome screen", "fields": welcome}, {"title": "Finish screen", "fields": finish}]


@admin_page
@never_cache
@require_http_methods(["GET", "POST"])
def screens_edit(request, form_id):
    """Edit welcome and finish wording. Saved into the draft; the public sees it after publishing."""
    from django.urls import reverse
    from apps.internal_ui.record_editors import editor_response, editor_success
    from .intake_lifecycle import save_screens
    model = _form(form_id)
    latest = _latest(model)
    form = ScreensForm(request.POST or None, current=latest.screens, initial={"revision": revision(latest)})
    if request.method == "POST" and form.is_valid():
        values = dict(form.cleaned_data)
        try:
            draft = save_screens(actor=request.user, form_id=model.pk, screens=values,
                                 expected_revision=request.POST.get("revision"))
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Saved to the draft. Publish the draft when it looks right.")
            return editor_success(request, reverse("participants:intake-config-preview", args=[draft.pk]))
    return editor_response(request, template="participants/signup_where_page.html", panel="internal/ui/record_form_panel.html", context={
        "form": form, "page_title": "Welcome and finish wording", "editor_context": model.name,
        "back": reverse("participants:intake-config-preview", args=[latest.pk]), "save_label": "Save to draft",
        "fields_template": "customers/editor_fields.html", "full_page_editor": request.headers.get("X-BTW-Editor") != "1"})
