"""Private intake sessions and staff review UI. Never expose matched people publicly."""
import secrets
from datetime import date
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.events.models import Event, OfflineEventPreparation, ParticipantMatchReview
from .models import IntakeFormAssignment, IntakeSession, Participant
from .intake_submission import start_session, complete_session
from .intake_wizard import (browser_key, session_for, session_revision, IntakeStepForm,
                            save_step, STEP_NAMES)
from .intake_review import require_review, decide, review_revision, retry_registration
from .intake_admin_guards import full_dob_allowed
from .workspace_views import context


def _errors(form, exc):
    if hasattr(exc, "message_dict"):
        for key, values in exc.message_dict.items():
            for value in values:
                form.add_error(key if key in form.fields else None, value)
    else:
        form.add_error(None, exc)


def assigned(channel, *, event=None, trailer=None):
    return IntakeFormAssignment.objects.filter(
        channel=channel, event=event, trailer=trailer, is_active=True).first()


@never_cache
@require_http_methods(["GET", "POST"])
def start(request, assignment_id):
    assignment = get_object_or_404(IntakeFormAssignment, pk=assignment_id, is_active=True)
    preparation = None
    if assignment.channel == "trailer":
        preparation = get_object_or_404(OfflineEventPreparation, event=assignment.event,
                                       trailer=assignment.trailer, status="active")
    if assignment.channel in {"staff", "trailer"}:
        from .intake_submission import _staff_channel
        _staff_channel(request.user, assignment.channel)
    error = ""
    if request.method == "POST":
        try:
            session = start_session(assignment=assignment, browser_key=browser_key(request),
                                    actor=request.user, event=assignment.event, preparation=preparation)
        except ValidationError as exc:
            error = "; ".join(exc.messages)
        else:
            return redirect("participants:intake-session", session_id=session.pk)
    return render(request, "participants/intake_start.html", {
        "assignment": assignment, "event": assignment.event, "error": error})


@never_cache
@require_http_methods(["GET", "POST"])
def wizard(request, session_id):
    try:
        session = session_for(request, session_id)
    except IntakeSession.DoesNotExist:
        raise Http404
    if session.state == "submitted":
        result = {"event": session.event}
        if request.user.is_authenticated and request.user.is_staff:
            source = session.submission
            review = ParticipantMatchReview.objects.filter(source_submission=source).first()
            if review and review.status in {"open", "later"} and request.user.has_perms(
                    ("participants.view_participant", "events.view_participantmatchreview")):
                result["intake_review"] = review
            registration_id = ((review.evidence.get("registration_id") if review else None)
                               or source.intake_metadata.get("registration_id"))
            if registration_id and request.user.has_perms(("events.view_eventregistration", "events.view_qrticket")):
                from apps.events.models import EventRegistration
                result["registration"] = EventRegistration.objects.filter(pk=registration_id, event=session.event).first()
        return render(request, "participants/intake_complete.html", result)
    try:
        step = int(request.GET.get("step", session.current_step))
    except ValueError:
        raise Http404
    if step not in STEP_NAMES or step > session.current_step:
        raise Http404
    form = IntakeStepForm(request.POST if request.method == "POST" else None,
        contract=session.form_version.snapshot, step=step, answers=session.answers,
        event=session.event, context=session.context)
    if request.method == "POST" and form.is_valid():
        try:
            session = save_step(session=session, step=step, values=form.payload(),
                extras=form.extras(), expected_revision=request.POST.get("revision", ""))
            if step == 4:
                complete_session(session_id=session.pk, actor=request.user)
                return redirect("participants:intake-session", session_id=session.pk)
        except ValidationError as exc:
            _errors(form, exc)
        else:
            return redirect("participants:intake-session", session_id=session.pk)
    return render(request, "participants/intake_step.html", {
        "session": session, "event": session.event, "form": form, "step": step,
        "step_name": STEP_NAMES[step], "progress": step * 25,
        "questions": form.display_questions(), "experience_rows": form.display_rows(),
        "experience_question": form.experience_question,
        "event_fields": [form[name] for name in form.event_fields],
        "contract": session.form_version.snapshot,
        "prior_answers": session.answers, "revision": session_revision(session),
        "previous_step": step - 1})


def review_page(view):
    @login_required(login_url="admin:login")
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        require_review(request.user)
        return view(request, *args, **kwargs)
    return wrapped


@review_page
@never_cache
@require_http_methods(["GET"])
def review_queue(request):
    status = request.GET.get("status", "open")
    if status not in {"open", "later", "resolved", "rejected"}:
        status = "open"
    state_filter = (Q(status="open") | Q(status__in=["resolved", "rejected"], evidence__registration_status="held")) if status == "open" else Q(status=status)
    rows = ParticipantMatchReview.objects.filter(state_filter, source_submission__isnull=False).select_related(
        "source_participant", "source_submission__event").order_by("created_at", "pk")
    return render(request, "participants/intake_review_queue.html", context(request,
        page_title="Participant Review Queue", reviews=Paginator(rows, 25).get_page(request.GET.get("page")),
        status=status))


def _comparison(user, source, target):
    answer = source.answers
    rows = []
    fields = [("first_name", "First name"), ("last_name", "Last name"),
              ("preferred_name", "Preferred name"), ("email", "Email"), ("phone", "Phone"),
              ("city", "City"), ("state", "State"), ("postal_code", "ZIP")]
    attrs = {"email": "contact_email", "phone": "contact_phone"}
    for key, label in fields:
        rows.append({"label": label, "reported": answer.get(key) or "Not answered",
                     "current": getattr(target, attrs.get(key, key), "") if target else "Choose a participant"})
    if full_dob_allowed(user):
        rows.append({"label": "Date of birth", "reported": answer.get("dob"),
                     "current": target.date_of_birth if target else "Choose a participant"})
    rows.append({"label": "Age classification", "reported": source.age_classification,
                 "current": target.age_classification if target else "Choose a participant"})
    return rows


@review_page
@never_cache
@require_http_methods(["GET", "POST"])
def review_detail(request, review_id):
    review = get_object_or_404(ParticipantMatchReview.objects.select_related(
        "source_submission__participant", "source_submission__event", "source_participant"),
        pk=review_id, source_submission__isnull=False)
    target = None
    raw_target = request.POST.get("target") if request.method == "POST" else request.GET.get("target")
    if raw_target:
        try:
            target = Participant.objects.filter(pk=int(raw_target), kind="person", archived_at__isnull=True).first()
        except ValueError:
            pass
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("action") == "retry_registration":
                retry_registration(actor=request.user, review_id=review.pk,
                    expected_revision=request.POST.get("revision"), reason=request.POST.get("reason"),
                    confirmed=request.POST.get("confirmed") == "yes")
            else:
                decide(actor=request.user, review_id=review.pk, action=request.POST.get("action"),
                    target_id=target.pk if target else None, reason=request.POST.get("reason"),
                    expected_revision=request.POST.get("revision"), confirmed=request.POST.get("confirmed") == "yes")
        except ValidationError as exc:
            error = "; ".join(exc.messages)
            review.refresh_from_db()
        else:
            messages.success(request, "Review decision saved. Established records and IDs were retained.")
            return redirect("participants:intake-review-detail", review_id=review.pk)
    candidates = list(Participant.objects.filter(pk__in=review.candidates, kind="person"))
    search = request.GET.get("q", "").strip()[:100]
    matches = []
    if search:
        matches = list(Participant.objects.filter(kind="person", archived_at__isnull=True).filter(
            Q(first_name__icontains=search) | Q(last_name__icontains=search) |
            Q(contact_email__icontains=search) | Q(contact_phone__icontains=search)).order_by("last_name")[:25])
    return render(request, "participants/intake_review_detail.html", context(request,
        page_title="Review intake identity", review=review, candidates=candidates, matches=matches,
        conflict_labels=[{
            "one_contact_only": "Only one contact matches an existing participant",
            "contacts_different_people": "Email and phone match different participants",
            "multiple_records": "More than one participant matches these contacts",
            "name_conflict": "The reported name differs",
            "dob_conflict": "The reported date of birth differs",
            "archived_identity": "A matching participant is archived",
            "identity_link_conflict": "Existing identity associations need review",
            "stable_identity_contact_conflict": "The retained identity and contact matches differ",
            "offline_registration_conflict": "Offline ticket or registration evidence needs review",
        }.get(code, "Additional identity or registration review required") for code in review.conflict_reasons],
        search=search, target=target, comparison=_comparison(request.user, review.source_submission, target),
        revision=review_revision(review), error=error,
        can_decide=request.user.has_perm("events.change_participantmatchreview"),
        can_update=request.user.has_perm("participants.change_participant"),
        can_retry_registration=request.user.has_perms(("events.change_participantmatchreview", "events.add_eventregistration")),
        decisions=review.decisions.select_related("actor").order_by("-created_at")))
