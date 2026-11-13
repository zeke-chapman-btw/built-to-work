"""Staff participant workspace; public/participant runtime routes stay separate."""
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.db.models import Count, Q, Prefetch, F
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from apps.internal_ui.people_editors import people_render as render, people_saved, profile_link
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.core.models import AuditLog
from apps.events.models import EventRegistration, QrTicket
from apps.operations.models import TaskParticipantLink
from apps.operations.task_access import tasks_for
from .models import Participant, ParticipantCareerProfile
from .workspace_models import ParticipantAcquisition, ParticipantWorkExperience, ParticipantNote, SavedParticipantView
from .workspace_access import require_people, can_notes, can_edit_note, can_analytics
from .workspace_forms import (StaffProfileForm, CareerProfileForm, StaffParticipantCreateForm, WorkExperienceForm,
    AcquisitionForm, NoteForm, SavedViewForm, RecordActionForm, ParticipantEventForm)
from .workspace_services import (profile_revision, save_profile, create_person, duplicate_fingerprint,
    correct_acquisition, save_work, save_note, archive_record, audit)
from .identity import canonical_id, identity_members, participant_candidates, identity_context
from .discovery import discover, query_string, targets_with_review
from .staff_directory_views import _staff_workspace_context
from .participant_review_views import can_view_participant_reviews
from .verification_services import can_view_qualification_approvals
from apps.event_reports.activity import activity_sources

PAGE_SIZE = 25


def workspace(view):
    @login_required(login_url="admin:login")
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        require_people(request.user)
        with identity_context():
            return view(request, *args, **kwargs)
    return wrapped


def context(request, **extra):
    result = _staff_workspace_context(request, title="Participants", description="Find, understand and manage the BTW talent pool.")
    result.update({
        "can_edit_profile": request.user.has_perm("participants.change_participant"),
        "can_add_person": request.user.has_perm("participants.add_participant"),
        "can_view_notes": can_notes(request.user),
        "can_add_note": can_notes(request.user) and request.user.has_perm("participants.add_participantnote"),
        "can_view_activity": request.user.has_perm("participants.view_participant_activity"),
        "can_manage_acquisition": request.user.has_perm("participants.manage_participant_acquisition"),
        "can_view_analytics": can_analytics(request.user),
        "can_view_simulator_results": request.user.has_perm("simulators.view_simulatorcapture"),
        "can_view_internal_review": can_view_participant_reviews(request.user),
        "can_view_verification": can_view_qualification_approvals(request.user),
        "can_view_events": request.user.has_perm("events.view_event"),
        "can_register_person": request.user.has_perms(("events.add_eventregistration", "events.view_eventregistration")),
    })
    from apps.recruiting.access import fresh_staff
    try:
        fresh_staff(request.user)
        result["can_recruiting"] = True
    except PermissionDenied:
        result["can_recruiting"] = False
    result.update(extra)
    # Pop-up editors name the person under the title, like customer editors do.
    if extra.get("participant") is not None and "editor_context" not in extra:
        result["editor_context"] = str(extra["participant"])
    return result


def person_for(pk, *, editing=False):
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        raise Http404
    person = get_object_or_404(Participant.objects.select_related("career_profile", "acquisition", "acquisition__event"), pk=pk, kind="person")
    if editing and (person.archived_at or canonical_id(person.pk) != person.pk):
        raise Http404("Open the active canonical profile before editing.")
    return person


def _criteria_from_request(request):
    values = request.GET.copy()
    previous_source = values.pop("assessment_source", None)
    if previous_source is not None and values.get("assessment", "") != previous_source[-1]:
        for key in ("score_min", "score_max", "assessment_category"):
            values.pop(key, None)
    saved_id = values.get("saved")
    if saved_id:
        try:
            saved_id = int(saved_id)
        except (ValueError, TypeError):
            raise ValidationError("Choose a valid saved view.")
        saved = get_object_or_404(SavedParticipantView.objects.filter(Q(owner=request.user) | Q(shared=True)), pk=saved_id, archived_at__isnull=True)
        if saved.schema_version != 1 or not isinstance(saved.criteria, dict):
            raise ValidationError("This saved view requires a supported criteria version.")
        values = dict(saved.criteria)
    return values


@workspace
@require_http_methods(["GET"])
def directory(request):
    return _participant_explorer(request, include_analysis=False)


@workspace
@require_http_methods(["GET"])
def profile(request, participant_id):
    person = person_for(participant_id)
    root = canonical_id(person.pk)
    if root != person.pk:
        if request.GET.get("from"):
            return redirect(profile_link(request,root))
        return redirect("participants:staff-profile", participant_id=root)
    member_ids = identity_members(person.pk)
    section = request.GET.get("section", "overview")
    from apps.accounts.operational_access import general_recruiter
    if section == "acquisition" and general_recruiter(request.user) and not request.user.has_perm("participants.view_registrationsubmission"):
        raise PermissionDenied
    if section == "acquisition" and request.headers.get('X-BTW-Editor') == '1' and not request.user.has_perm('participants.view_registrationsubmission'):
        raise PermissionDenied
    permissions = {"source-event":"events.view_event", "documents": "participants.view_participantdocument", "qualifications": "participants.view_participantqualification",
                   "events": "events.view_eventregistration", "notes": "participants.view_participantnote",
                   "activity": "participants.view_participant_activity"}
    if section not in ("overview", "relationship", "recruiting", "career", "qualifications", "documents", "assessments", "events", "source-event", "acquisition", "notes", "activity", "tasks"):
        raise Http404
    if section in permissions and not request.user.has_perm(permissions[section]):
        raise PermissionDenied
    if section == "notes" and not can_notes(request.user):
        raise PermissionDenied
    ctx = context(request, participant=person, display_name=person.preferred_name or person.first_name,
        page_title=str(person), section=section, alias_count=len(member_ids)-1, active_participant_tab="directory")
    from apps.crm.navigation import previous
    ctx["crm_previous_url"] = previous(request)
    from apps.crm.presentation import participant_navigation
    ctx.update(participant_navigation(request.user, person))
    from apps.crm.navigation import with_origin
    ctx['crm_new_url'] = with_origin(ctx['crm_new_url'], request.get_full_path())
    if section == "relationship" and not (ctx["crm_btw_allowed"] or ctx["crm_employer_allowed"]):
        raise PermissionDenied
    if section == "recruiting" and not ctx["can_recruiting"]:
        raise PermissionDenied
    if section in ("relationship", "recruiting"):
        ctx["career"] = ParticipantCareerProfile.objects.filter(participant=person).first()
        ctx["current_interests"] = getattr(ctx["career"], "career_interests", "")
        from .ui_presentation import profile_preferences
        ctx.update(profile_preferences(ctx))
        from apps.crm.presentation import history_context
        ctx.update(history_context(request, participant=person))
        if section == "recruiting":
            from apps.recruiting.read_models import participant_context
            ctx.update(participant_context(request.user, person, page=request.GET.get('recruiting_page')))
            recruiting_query = request.GET.copy()
            recruiting_query.pop('recruiting_page', None)
            ctx['profile_recruiting_query'] = recruiting_query.urlencode()
        return render(request, "participants/staff_profile.html", ctx)
    if section == "assessments" and not any(ctx[key] for key in ("can_view_quiz_results", "can_view_game_results", "can_view_historical_scores", "can_view_simulator_results")):
        raise PermissionDenied
    registrations = EventRegistration.objects.filter(participant_id__in=member_ids).select_related("event", "group", "participant").order_by("-registered_at", "-pk") if ctx["can_view_registrations"] else EventRegistration.objects.none()
    ctx["registration_total"] = registrations.count() if ctx["can_view_registrations"] else None
    ctx["registrations"] = Paginator(registrations, PAGE_SIZE).get_page(request.GET.get("page")) if section == "events" else registrations[:5]
    ticket_rows = QrTicket.objects.filter(registration__in=registrations, is_current=True, revoked_at__isnull=True).select_related("registration") if ctx["can_view_tickets"] else QrTicket.objects.none()
    ctx["registration_tickets"] = {ticket.registration_id: ticket for ticket in ticket_rows[:100]}
    pop = Participant.objects.filter(pk=person.pk)
    if ctx["can_view_qualifications"]:
        qualifications = targets_with_review("qualification", pop).order_by("-created_at", "-pk")
        ctx["qualifications"] = Paginator(qualifications, PAGE_SIZE).get_page(request.GET.get("page")) if section == "qualifications" else qualifications[:5]
        ctx["qualification_total"] = qualifications.count()
        if section == "overview":
            from .ui_presentation import qualification_preview_groups
            ctx["profile_qualification_groups"] = qualification_preview_groups(qualifications)
    if ctx["can_view_documents"]:
        documents = targets_with_review("document", pop).order_by("-uploaded_at", "-pk")
        ctx["documents"] = Paginator(documents, PAGE_SIZE).get_page(request.GET.get("page")) if section == "documents" else documents[:5]
        ctx["document_total"] = documents.count()
        if section == "overview":
            # Resume is an established category, never inferred from a filename.
            resume = documents.filter(category__slug="resume").first()
            recent = documents.exclude(pk=resume.pk) if resume else documents
            ctx["profile_documents"] = ([resume] if resume else []) + list(recent[:2 if resume else 3])
    if ctx["can_view_internal_review"]:
        ctx["internal_review"] = person.internal_reviews.select_related("reviewer").first()
    ctx["career"] = ParticipantCareerProfile.objects.filter(participant=person).first()
    ctx["work_history"] = ParticipantWorkExperience.objects.filter(participant_id__in=member_ids, archived_at__isnull=True).select_related("created_by", "updated_by").order_by("-is_current", "-started_on", "-pk")
    ctx["acquisition_record"] = ParticipantAcquisition.objects.filter(participant=person).select_related("event", "source_submission", "recorded_by").first()
    from .workspace_models import ParticipantReference
    ctx["profile_references"] = list(ParticipantReference.objects.filter(participant=person, archived_at__isnull=True).select_related("checked_by"))
    ctx["profile_references_checked"] = sum(1 for item in ctx["profile_references"] if item.result != "not_checked")
    ctx["profile_source_campaign"] = ""
    if ctx.get("can_recruiting"):
        from apps.recruiting.models import CampaignSource
        source = CampaignSource.objects.filter(participant=person).select_related("campaign").order_by("created_at").first()
        ctx["profile_source_campaign"] = source.campaign.name if source else ""
    if section == "acquisition":
        acq = ctx["acquisition_record"]
        if acq:
            ctx["acquisition_changes"] = AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(ParticipantAcquisition), object_id=str(acq.pk)).select_related("actor", "actor__staff_profile")
        ctx["submissions"] = person.registration_submissions.select_related("event", "form_version").order_by("-submitted_at")[:50]
    source = activity_sources()
    if ctx["can_view_registrations"] and section in ("overview", "events"):
        participation_limit = 5 if section == "overview" else 100
        ctx["recorded_sessions"] = source["sessions"].filter(participant_id__in=member_ids).select_related("event").order_by("-started_at")[:participation_limit]
        ctx["legacy_attendance"] = source["attendance"].filter(participant_id__in=member_ids).select_related("event").order_by("-checked_in_at")[:participation_limit]
    assessment_limit = 5 if section == "overview" else 50
    if ctx["can_view_quiz_results"]:
        quizzes=source['quizzes']
        if section=='assessments':
            from apps.assessments.models import QuizAttempt
            quizzes=QuizAttempt.objects.filter(participant_id__in=member_ids,experience_session__participant_id__in=member_ids,
                experience_session__registration__participant_id__in=member_ids,
                experience_session__registration__event_id=F('experience_session__event_id'))
        ctx["quiz_attempts"] = quizzes.filter(participant_id__in=member_ids).select_related("experience_session__event").prefetch_related("sections__responses").order_by("-selected_at")
        ctx["quiz_page"] = Paginator(ctx["quiz_attempts"], 25).get_page(request.GET.get("quiz_page")) if section == "assessments" else None
        ctx["quiz_attempts"] = ctx["quiz_page"] if section == "assessments" else ctx["quiz_attempts"][:assessment_limit]
        ctx["quiz_attempts"] = list(ctx["quiz_attempts"])
        for attempt in ctx["quiz_attempts"]:
            attempt.workspace_correct = sum(1 for section_row in attempt.sections.all() for response_row in section_row.responses.all() if response_row.is_correct)
    if ctx["can_view_game_results"]:
        from apps.games.models import GameSession
        ctx["game_sessions"] = GameSession.objects.filter(experience_session__participant_id__in=member_ids).select_related("game", "event", "experience_session__event").order_by("-created_at")
        ctx["game_page"] = Paginator(ctx["game_sessions"], 25).get_page(request.GET.get("game_page")) if section == "assessments" else None
        ctx["game_sessions"] = ctx["game_page"] if section == "assessments" else ctx["game_sessions"][:assessment_limit]
    if ctx["can_view_simulator_results"]:
        from apps.simulators.models import SimulatorCapture
        ctx["simulator_results"] = SimulatorCapture.objects.filter(participant_id__in=member_ids).select_related("event", "station").order_by("-captured_at")
        ctx["simulator_page"] = Paginator(ctx["simulator_results"], 25).get_page(request.GET.get("simulator_page")) if section == "assessments" else None
        ctx["simulator_results"] = ctx["simulator_page"] if section == "assessments" else ctx["simulator_results"][:assessment_limit]
    if ctx["can_view_historical_scores"]:
        from .models import HistoricalSimulatorScore, HistoricalEventRecord
        ctx["historical_scores"] = HistoricalSimulatorScore.objects.filter(participant_id__in=member_ids).select_related("event_record").order_by("-scored_at", "-pk")
        ctx["historical_page"] = Paginator(ctx["historical_scores"], 25).get_page(request.GET.get("historical_page")) if section == "assessments" else None
        ctx["historical_scores"] = ctx["historical_page"] if section == "assessments" else ctx["historical_scores"][:assessment_limit]
        ctx["historical_events"] = HistoricalEventRecord.objects.filter(participant_id__in=member_ids).select_related("event").order_by("-event_date", "-pk")[:assessment_limit]
    if ctx["can_view_notes"]:
        notes = ParticipantNote.objects.filter(participant_id__in=member_ids, audience="btw_internal").select_related("author", "author__staff_profile", "edited_by", "edited_by__staff_profile")
        if request.GET.get("archived") != "1":
            notes = notes.filter(archived_at__isnull=True)
        note_page = Paginator(notes, PAGE_SIZE).get_page(request.GET.get("page"))
        for note in note_page:
            note.can_edit = can_edit_note(request.user, note)
        ctx["notes_page"] = note_page
        ctx["notes_query"] = "section=notes" + ("&archived=1" if request.GET.get("archived") == "1" else "")
        if section == "overview":
            active_notes = notes.filter(archived_at__isnull=True)
            ctx["profile_note_total"] = active_notes.count()
            ctx["profile_notes"] = list(active_notes[:3])
            for note in ctx['profile_notes']:
                note.can_edit = can_edit_note(request.user, note)
    ctx["related_tasks"] = TaskParticipantLink.objects.filter(participant_id__in=member_ids, task__in=tasks_for(request.user)).select_related("task", "task__assigned_staff").order_by("-created_at")[:25]
    if section == "activity":
        ctx["timeline"] = factual_timeline(request.user, person, member_ids, ctx)
    if section == "overview":
        ctx["profile_work_total"] = ctx["work_history"].count()
        ctx["profile_work_history"] = list(ctx["work_history"][:5])
    from .intake_workspace import workspace_intake_context
    ctx.update(workspace_intake_context(request.user, person, member_ids, ctx["career"]))
    from .ui_presentation import profile_preferences
    ctx.update(profile_preferences(ctx))
    from .profile_followup import followup_context
    followup_context(request, ctx, member_ids, source)
    if section == "overview":
        from .profile_overview import overview_context
        ctx.update(overview_context(request, person, ctx))
    return render(request, "participants/staff_profile.html", ctx)


def factual_timeline(user, person, member_ids, ctx):
    """Bounded factual chronology; no copied activity store or invented attendance."""
    from apps.crm.actor_names import compact_actor
    rows = []
    def add(at, label, detail, source, key, source_action="", actor=None):
        if at:
            group='staff' if actor and actor.is_staff else 'participant' if actor and hasattr(actor,'participant_account') else 'customer' if actor and hasattr(actor,'customer_identity') else 'unknown'
            rows.append(dict(at=at, label=label, detail=detail, source=source, key=str(key), source_action=source_action,
                actor=compact_actor(actor) if actor else 'Not recorded', actor_group=group))
    for row in Participant.objects.filter(pk__in=member_ids):
        add(row.created_at, "Participant record created", "Stored identity retained", "Participant", row.pk)
    if ctx["can_view_registrations"]:
        for row in EventRegistration.objects.filter(participant_id__in=member_ids).select_related("event").order_by("-registered_at")[:100]:
            add(row.registered_at, "Event registration", f"{row.event.name} · {row.get_status_display()}", "Registration", row.pk)
    if ctx["can_view_registrations"]:
        source = activity_sources()
        for row in source["sessions"].filter(participant_id__in=member_ids).select_related("event", "created_by", "created_by__staff_profile").order_by("-started_at")[:100]:
            add(row.started_at, "Official experience started", row.event.name, "Experience session", row.pk, actor=row.created_by)
        for row in source["attendance"].filter(participant_id__in=member_ids).select_related("event").order_by("-checked_in_at")[:100]:
            add(row.checked_in_at, "Retained legacy check-in", row.event.name, "Legacy attendance", row.pk)
    if ctx["can_view_documents"]:
        from .participant_document_models import ParticipantDocument
        for row in ParticipantDocument.objects.filter(participant_id__in=member_ids).select_related('uploaded_by','uploaded_by__staff_profile').order_by("-uploaded_at")[:100]:
            add(row.uploaded_at, "Document uploaded", row.original_filename, "Private document record", row.pk, actor=row.uploaded_by)
    if ctx["can_view_quiz_results"]:
        for row in ctx.get("quiz_attempts", []):
            add(row.completed_at or row.selected_at, "Quiz " + row.get_status_display().lower(), "Stored attempt; no score recalculation", "Assessment", row.pk)
    if ctx["can_view_game_results"]:
        for row in ctx.get("game_sessions", []):
            add(row.completed_at or row.created_at, "Game " + row.get_status_display().lower(), str(row.game), "Game record", row.pk)
    if ctx["can_view_simulator_results"]:
        for row in ctx.get("simulator_results", []):
            add(row.captured_at, "Simulator result recorded", row.get_status_display(), "Simulator evidence", row.pk)
    if ctx["can_view_verification"]:
        from .verification_models import ParticipantVerificationRecord
        for row in ParticipantVerificationRecord.objects.filter(participant_id__in=member_ids).select_related("reviewer").order_by("-created_at")[:100]:
            add(row.created_at, "Credential review", f"{row.get_status_display()} · {row.reviewer or 'System'}", "Verification history", row.pk, actor=row.reviewer)
    if ctx["can_view_internal_review"]:
        for row in person.internal_reviews.select_related("reviewer").order_by("-created_at")[:50]:
            add(row.reviewed_at, "Internal participant review", f"{row.get_status_display()} · {row.reviewer}", "Internal review", row.pk, actor=row.reviewer)
    types = [(Participant, member_ids), (ParticipantWorkExperience, ParticipantWorkExperience.objects.filter(participant_id__in=member_ids).values_list("pk", flat=True)),
             (ParticipantAcquisition, ParticipantAcquisition.objects.filter(participant_id__in=member_ids).values_list("pk", flat=True))]
    if ctx["can_view_notes"]:
        types.append((ParticipantNote, ParticipantNote.objects.filter(participant_id__in=member_ids).values_list("pk", flat=True)))
    for model, ids in types:
        logs = AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(model), object_id__in=[str(pk) for pk in ids]).select_related("actor", "actor__staff_profile").order_by("-timestamp")[:100]
        for row in logs:
            add(row.timestamp, row.action.replace("participant.workspace.", "").replace(".", " ").title(),
                f"{compact_actor(row.actor) if row.actor else 'System'} · {row.reason}", "Audited staff change", row.pk, source_action=row.action, actor=row.actor)
    return sorted(rows, key=lambda r: (r["at"], r["source"], r["key"]), reverse=True)[:200]


@workspace
@require_http_methods(["GET", "POST"])
def edit_profile(request, participant_id):
    require_people(request.user, "participants.change_participant")
    person = person_for(participant_id, editing=True)
    career = ParticipantCareerProfile.objects.filter(participant=person).first()
    form = StaffProfileForm(request.POST if request.method == "POST" else None, instance=person,
                            initial={"revision": profile_revision(person)})
    career_form = CareerProfileForm(request.POST if request.method == "POST" else None, instance=career, prefix="career")
    if request.method == "POST" and form.is_valid() and career_form.is_valid():
        try:
            save_profile(actor=request.user, participant_id=person.pk, person_data=form.cleaned_data, career_data=career_form.cleaned_data,
                         revision=form.cleaned_data["revision"], reason=form.cleaned_data["audit_reason"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Profile saved.")
            return people_saved(request,"participants:staff-profile", participant_id=person.pk)
    return render(request, "participants/workspace_form.html", context(request, participant=person, form=form, career_form=career_form,
        page_title="Edit participant profile", submit_label="Save profile", return_url=reverse("participants:staff-profile", args=[person.pk])),
        status=400 if request.method == "POST" and (form.errors or career_form.errors) else 200)


@workspace
@require_http_methods(["GET", "POST"])
def add_person(request):
    require_people(request.user, "participants.add_participant")
    form = StaffParticipantCreateForm(request.POST if request.method == "POST" else None)
    matches = Participant.objects.none()
    if request.method == "POST" and form.is_valid():
        matches = participant_candidates(email=form.cleaned_data.get("contact_email"), phone=form.cleaned_data.get("contact_phone"))
        try:
            person = create_person(actor=request.user, data=form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
            if matches.exists():
                mutable = form.data.copy()
                mutable["duplicate_revision"] = duplicate_fingerprint(matches)
                form.data = mutable
        else:
            messages.success(request, "Participant added.")
            saved = people_saved(request,"participants:staff-profile", participant_id=person.pk)
            if request.headers.get("X-BTW-Editor") == "1":
                from apps.internal_ui.record_editors import editor_success
                return editor_success(request, saved.url)
            return saved
    if request.headers.get("X-BTW-Editor") == "1":
        from apps.internal_ui.record_editors import editor_response
        return editor_response(request, template="participants/workspace_form.html", panel="internal/ui/record_form_panel.html",
            context={"form": form, "possible_matches": matches, "page_title": "Add participant", "back": reverse("participants:staff-directory"),
                     "save_label": "Add participant", "fields_template": "participants/add_person_fields.html", "editor_return_label": "participants",
                     "editor_context": "Name is all you need. You can add the rest later.", "full_page_editor": False})
    return render(request, "participants/workspace_form.html", context(request, form=form, possible_matches=matches,
        page_title="Add participant", submit_label="Add participant", return_url=reverse("participants:staff-directory")),
        status=400 if form.is_bound and form.errors else 200)


@workspace
@require_http_methods(["GET", "POST"])
def work_edit(request, participant_id, object_id=None):
    require_people(request.user, "participants.change_participant")
    person = person_for(participant_id, editing=True)
    row = get_object_or_404(ParticipantWorkExperience, pk=object_id, participant=person, archived_at__isnull=True) if object_id else None
    form = WorkExperienceForm(request.POST if request.method == "POST" else None, instance=row,
                              initial={"revision": row.updated_at.isoformat() if row else ""})
    if request.method == "POST" and form.is_valid():
        try:
            save_work(actor=request.user, participant_id=person.pk, data=form.cleaned_data, object_id=object_id)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Job saved.")
            return people_saved(request,reverse("participants:staff-profile", args=[person.pk]) + "?section=career")
    return render(request, "participants/workspace_form.html", context(request, participant=person, form=form,
        page_title="Edit job" if row else "Add a job", submit_label="Save job",
        return_url=reverse("participants:staff-profile", args=[person.pk]) + "?section=career"),
        status=400 if form.is_bound and form.errors else 200)


@workspace
@require_http_methods(["GET", "POST"])
def reference_edit(request, participant_id, object_id=None):
    from .workspace_forms import ReferenceForm
    from .workspace_models import ParticipantReference
    from .workspace_services import save_reference
    require_people(request.user, "participants.change_participant")
    person = person_for(participant_id, editing=True)
    row = get_object_or_404(ParticipantReference, pk=object_id, participant=person, archived_at__isnull=True) if object_id else None
    form = ReferenceForm(request.POST if request.method == "POST" else None, instance=row,
                         initial={"revision": row.updated_at.isoformat() if row else ""})
    if request.method == "POST" and form.is_valid():
        try:
            save_reference(actor=request.user, participant_id=person.pk, data=form.cleaned_data, object_id=object_id)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Reference saved.")
            return people_saved(request, "participants:staff-profile", participant_id=person.pk)
    return render(request, "participants/workspace_form.html", context(request, participant=person, form=form,
        page_title="Edit reference" if row else "Add a reference", submit_label="Save reference",
        return_url=reverse("participants:staff-profile", args=[person.pk])),
        status=400 if form.is_bound and form.errors else 200)


@workspace
@require_http_methods(["GET", "POST"])
def acquisition_edit(request, participant_id):
    require_people(request.user, "participants.manage_participant_acquisition")
    person = person_for(participant_id, editing=True)
    row = ParticipantAcquisition.objects.filter(participant=person).first()
    form = AcquisitionForm(request.POST if request.method == "POST" else None, instance=row, participant=person, user=request.user,
                           initial={"revision": row.updated_at.isoformat() if row else "none"})
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                if "participant_type" in form.cleaned_data:
                    from .identifier_services import revision as type_revision, save_identifier
                    if form.cleaned_data["type_revision"] != type_revision(person):
                        raise ValidationError("Participant type changed while you were editing. Reload before saving.")
                    if form.cleaned_data["participant_type"] != person.primary_identifier:
                        save_identifier(actor=request.user, participant_id=person.pk, value=form.cleaned_data["participant_type"],
                                        expected_revision=form.cleaned_data["type_revision"], reason="Details card: participant type updated by staff")
                correct_acquisition(actor=request.user, participant_id=person.pk, data=form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Saved.")
            return people_saved(request, "participants:staff-profile", participant_id=person.pk)
    return render(request, "participants/workspace_form.html", context(request, participant=person, form=form,
        page_title="Edit details", submit_label="Save changes", return_url=reverse("participants:staff-profile", args=[person.pk])),
        status=400 if form.is_bound and form.errors else 200)


@workspace
@require_http_methods(["GET", "POST"])
def note_edit(request, participant_id, object_id=None):
    person = person_for(participant_id, editing=True)
    if not can_notes(request.user):
        raise PermissionDenied
    row = get_object_or_404(ParticipantNote, pk=object_id, participant=person, archived_at__isnull=True) if object_id else None
    if row and not can_edit_note(request.user, row) or not row and not request.user.has_perm("participants.add_participantnote"):
        raise PermissionDenied
    from .workspace_services import _reject_customer_scope
    if request.method == "POST":
        _reject_customer_scope(request.POST)
    form = NoteForm(request.POST if request.method == "POST" else None, instance=row,
                    initial={"revision": row.updated_at.isoformat() if row else ""})
    if request.method == "POST" and form.is_valid():
        try:
            save_note(actor=request.user, participant_id=person.pk, data=form.cleaned_data, object_id=object_id)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Internal note saved.")
            return people_saved(request,reverse("participants:staff-profile", args=[person.pk]) + "?section=notes")
    return render(request, "participants/workspace_form.html", context(request, participant=person, form=form,
        page_title="Edit internal note" if row else "Add internal note", submit_label="Save internal note",
        form_description="Private to authorized BTW staff.",
        return_url=reverse("participants:staff-profile", args=[person.pk]) + "?section=notes"),
        status=400 if form.is_bound and form.errors else 200)


@workspace
@require_http_methods(["GET"])
def note_history(request, participant_id, object_id):
    person = person_for(participant_id)
    if not can_notes(request.user):
        raise PermissionDenied
    note = get_object_or_404(ParticipantNote, pk=object_id, participant_id__in=identity_members(person.pk))
    rows = AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(ParticipantNote), object_id=str(note.pk)).select_related("actor", "actor__staff_profile").order_by("-timestamp")
    return render(request, "participants/note_history.html", context(request, participant=person, note=note, history=Paginator(rows,PAGE_SIZE).get_page(request.GET.get("page")), page_title="Internal note history"))


@workspace
@require_http_methods(["GET", "POST"])
def record_archive(request, participant_id, kind, object_id, restore=False):
    if kind not in ("note", "work"):
        raise Http404
    person = person_for(participant_id, editing=True)
    if kind == "note" and not can_notes(request.user):
        raise PermissionDenied
    model = ParticipantNote if kind == "note" else ParticipantWorkExperience
    row = get_object_or_404(model, pk=object_id, participant=person)
    if kind == "note" and not can_edit_note(request.user, row) or kind == "work" and not request.user.has_perm("participants.change_participant"):
        raise PermissionDenied
    form = RecordActionForm(request.POST if request.method == "POST" else None, initial={"revision": row.updated_at.isoformat()})
    if request.method == "POST" and form.is_valid():
        try:
            archive_record(actor=request.user, participant_id=person.pk, kind=kind, object_id=row.pk, revision=form.cleaned_data["revision"],
                           reason=form.cleaned_data["audit_reason"], restore=restore)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Record restored." if restore else "Record archived; history retained.")
            return people_saved(request,reverse("participants:staff-profile", args=[person.pk]) + "?section=" + ("notes" if kind == "note" else "career"))
    return render(request, "participants/workspace_form.html", context(request, participant=person, form=form,
        page_title=("Restore " if restore else "Archive ") + ("internal note" if kind == "note" else "work history"),
        submit_label="Restore record" if restore else "Archive record",
        return_url=reverse("participants:staff-profile", args=[person.pk])),
        status=400 if form.is_bound and form.errors else 200)


@workspace
@require_http_methods(["GET", "POST"])
def register_person(request, participant_id):
    require_people(request.user, "events.view_eventregistration", "events.add_eventregistration")
    person = person_for(participant_id, editing=True)
    form = ParticipantEventForm(request.POST if request.method == "POST" else None, user=request.user)
    if request.method == "POST" and form.is_valid():
        from apps.events.services import register_participant
        try:
            registration, created = register_participant(event=form.cleaned_data["event"], participant=person, actor=request.user, source="staff")
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            audit(request.user, "event.registered", person, after={"registration_id": str(registration.pk), "created": created},
                  reason=form.cleaned_data["audit_reason"])
            messages.success(request, "Existing participant registered; original acquisition and identity retained." if created else "This participant already has an active registration for this event.")
            return people_saved(request,reverse("participants:staff-profile", args=[person.pk]) + "?section=events")
    return render(request, "participants/workspace_form.html", context(request, participant=person, form=form,
        page_title="Register existing participant", submit_label="Register participant",
        form_description="Uses the existing event registration and QR ticket service. This does not record attendance or change original acquisition.",
        return_url=reverse("participants:staff-profile", args=[person.pk]) + "?section=events"),
        status=400 if form.is_bound and form.errors else 200)


@workspace
@require_http_methods(["GET", "POST"])
def saved_views(request):
    form = SavedViewForm(request.POST if request.method == "POST" else None)
    if request.method == "POST":
        try:
            params = request.GET.copy()
            params.update(request.POST)
            params.pop("return_to", None)  # Navigation context is not saved search criteria.
            params.pop("shared", None)  # Visibility is not a discovery filter.
            filters, population, criteria = discover(request.user, params, strict=True)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            if not filters.is_valid():
                form.add_error(None, "The filter configuration is invalid. Correct it before saving.")
            elif form.is_valid():
                if SavedParticipantView.objects.filter(owner=request.user, name=form.cleaned_data["name"]).exists():
                    form.add_error("name", "You already have a view with this name, including retained archived views.")
                elif SavedParticipantView.objects.filter(owner=request.user).count() >= 100:
                    form.add_error(None, "This account has reached its limit of 100 retained saved views.")
                else:
                    with transaction.atomic():
                        row = SavedParticipantView.objects.create(owner=request.user, name=form.cleaned_data["name"], criteria=criteria, shared=form.cleaned_data["shared"])
                    audit(request.user, "view.saved", row, after={"name": row.name, "criteria": criteria}, reason="Owner saved participant filters")
                    messages.success(request, "Participant view saved.")
                    return people_saved(request,"participants:staff-directory")
    return render(request, "participants/saved_views.html", context(request, form=form,
        saved_views=SavedParticipantView.objects.filter(Q(owner=request.user) | Q(shared=True, archived_at__isnull=True)), page_title="Saved participant views"),
        status=400 if form.is_bound and form.errors else 200)


@workspace
@require_http_methods(["GET", "POST"])
def saved_view_archive(request, view_id, restore=False):
    row = get_object_or_404(SavedParticipantView, pk=view_id, owner=request.user)
    form = RecordActionForm(request.POST if request.method == "POST" else None, initial={"revision": row.updated_at.isoformat()})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            row = SavedParticipantView.objects.select_for_update().get(pk=row.pk, owner=request.user)
            if form.cleaned_data["revision"] != row.updated_at.isoformat():
                form.add_error(None, "This saved view changed. Reload before proceeding.")
            else:
                from django.utils import timezone
                row.archived_at = None if restore else timezone.now()
                row.save(update_fields=["archived_at", "updated_at"])
                audit(request.user, "view.restored" if restore else "view.archived", row,
                      after={"archived": not restore}, reason=form.cleaned_data["audit_reason"])
                return people_saved(request,"participants:saved-views")
    return render(request, "participants/workspace_form.html", context(request, form=form,
        page_title=("Restore " if restore else "Archive ") + "saved view", form_description=row.name,
        submit_label="Restore saved view" if restore else "Archive saved view", return_url=reverse("participants:saved-views")),
        status=400 if form.is_bound and form.errors else 200)

def _explorer_presentation(request, form, population, criteria, data, page, *, include_analysis):
    """URLs and chart decoration only; discover() remains the sole selector."""
    from urllib.parse import urlencode
    import json
    route = reverse("participants:intelligence") if include_analysis else request.path
    def target(values):
        query = query_string(values)
        return route + ("?" + query if query else "")
    def change(key, value=None):
        values = dict(criteria)
        if value in (None, ""):
            values.pop(key, None)
        else:
            values[key] = str(value)
        # Score ranges belong to the explicitly selected source's stored units.
        if key == "assessment" and str(value or "") != criteria.get(key, ""):
            values.pop("score_min", None)
            values.pop("score_max", None)
            if value != "quiz":
                values.pop("assessment_category", None)
        return target(values)
    chips = []
    for key, value in criteria.items():
        if key in {"q", "sort", "segments", "page_size"} or key not in form.fields or value in (None, ""):
            continue
        field = form[key]
        selected = form.cleaned_data.get(key, value)
        if not hasattr(selected, "_meta") and hasattr(field.field, "choices"):
            selected = dict(field.field.choices).get(str(value), value)
        label = {"employment":"Interested in work", "travel":"Travel", "channel":"Original source",
                 "experience_min":"Reported experience (minimum years)"}.get(key, field.label)
        if key == "travel":
            selected = {"yes":"Yes", "maybe":"Maybe", "no":"No", "unknown":"Unknown"}.get(str(value), selected)
        chips.append(dict(label=label, value=str(selected), url=change(key)))
    if criteria.get("q"):
        chips.insert(0, dict(label="Search", value=criteria["q"], url=change("q")))
    for dimension, bucket in json.loads(criteria.get("segments") or "[]"):
        values = dict(criteria)
        remaining = [pair for pair in json.loads(criteria["segments"]) if pair[0] != dimension]
        if remaining:
            values["segments"] = json.dumps(remaining, separators=(",", ":"))
        else:
            values.pop("segments", None)
        display = next((row["label"] for chart in data.get("charts", []) if chart["dimension"] == dimension
                        for row in chart["rows"] if row.get("bucket") == bucket), bucket.replace("_", " "))
        chips.append(dict(label=dimension.replace("_", " ").title(), value=display, url=target(values)))
    groups = []
    for key, title in [("employment","Interested in work"), ("travel","Willing to travel"),
                       ("channel","Found us"), ("assessment","Assessment"),
                       ("archived","Show")]:
        if key not in form.fields:
            continue
        options = []
        for value, label in form.fields[key].choices:
            selected = str(criteria.get(key, "")) == str(value)
            display_label = {"employment":{"":"Any", "seeking":"Yes", "open":"Maybe", "not_seeking":"No", "unknown":"Unknown"},
                             "travel":{"":"Any", "yes":"Yes", "maybe":"Maybe", "no":"No", "unknown":"Unknown"},
                             "channel":{"":"Any", "online":"Online", "event":"At an event", "unknown":"Not known"},
                             "assessment":{"":"Any", "quiz":"Quiz", "simulator":"Simulator", "game":"Game"},
                             "archived":{"":"Active participants", "active":"Active participants", "all":"Active and archived", "only":"Archived only"}}.get(key, {}).get(str(value), label)
            options.append(dict(value=str(value), label=display_label, selected=selected, url=change(key, None if selected else value)))
        groups.append(dict(key=key, title=title, options=options, errors=form[key].errors))
    definitions = [("Work preferences", ["trade_interest","work_type","relocation","employment_classification","availability_classification","preferred_location","availability"]),
                   ("Experience & qualifications", ["experience_min","experience_data","skill","qualification","qualification_kind","qualification_status","document_status"]),
                   ("Events & dates", ["event","participation","acquired_from","acquired_to","completeness"]),
                   ("Assessment scores", ["assessment_category","score_min","score_max"])]
    panels, common_panels = {}, {}
    for device in ("unified",):
        def presentation_field(key):
            bound = form[key]
            identifier = "explorer-" + device + "-" + key
            attrs = {"id":identifier}
            if bound.errors:
                attrs.update({"aria-invalid":"true", "aria-describedby":identifier + "-errors"})
            return dict(label={"trade_interest":"Trade interest"}.get(key, bound.label),
                        identifier=identifier, widget=bound.as_widget(attrs=attrs), errors=bound.errors)
        common_panels[device] = [presentation_field(key) for key in ("location",) if key in form.fields]
        type_fields = [presentation_field(key) for key in ("identifier",) if key in form.fields]
        advanced = []
        for title, names in definitions:
            fields = []
            for key in names:
                if key not in form.fields or (key == "assessment_category" and criteria.get("assessment") not in (None, "", "quiz")):
                    continue
                fields.append(presentation_field(key))
            if fields:
                advanced.append(dict(title=title, fields=fields, has_errors=any(field["errors"] for field in fields)))
        panels[device] = advanced
    hidden = [(key, value) for key, value in criteria.items() if key not in {"q","sort","segments"}]
    filter_hidden = [(key, criteria.get(key, "")) for key in
                     ("city","state")
                     if key in form.fields]
    colors = ["#d02030", "#0ea5e9", "#f59e0b", "#10b981", "#8b5cf6", "#a1a1aa"]
    total = page.paginator.count
    preferred = ["employment","travel","channel","qualification","experience","completeness","interest"]
    charts = sorted([c for c in data.get("charts", []) if c["dimension"] in preferred], key=lambda c:preferred.index(c["dimension"]))
    for chart in charts:
        stops, position = [], 0
        for index, row in enumerate(chart["rows"]):
            row["color"] = colors[index % len(colors)]
            row["percent"] = round(100 * row["count"] / total, 2) if total else 0
            end = position + row["percent"]
            stops.append(f"{row['color']} {position:.2f}% {end:.2f}%")
            position = end
        if chart["dimension"] in {"employment","travel","channel","experience","completeness"} and total and sum(r["count"] for r in chart["rows"]) == total:
            chart["gradient"] = ",".join(stops)
    origin_values = {key: value for key, value in request.GET.items() if value}
    origin_values.pop("assessment_source", None)
    origin_query = urlencode(origin_values)
    directory_origin = request.path + ("?" + origin_query if origin_query else "")
    more_keys = {k for _, names in definitions for k in names} | {g["key"] for g in groups if g["key"] not in ("employment", "travel")} | {"identifier", "trade_interest"}
    more_active = sum(1 for k in more_keys if criteria.get(k) not in (None, "", [])) + (1 if criteria.get("page_size") not in (None, "", "25", 25) else 0)
    return dict(explorer_more_active=more_active, explorer_reset=route, explorer_chips=chips, explorer_groups=groups, explorer_panels=panels, explorer_common=common_panels, explorer_type_fields=type_fields,
                explorer_hidden=hidden, explorer_filter_hidden=filter_hidden, explorer_charts=charts,
                explorer_origin=directory_origin, explorer_clear=route,
                explorer_unified=True, explorer_include_analysis=include_analysis,
                explorer_analysis_url=reverse("participants:intelligence") + "?" + query_string(criteria) + "#explorer-analysis",
                explorer_alternatives=target(dict(criteria, view="alternatives")) + "#explorer-analysis",
                explorer_current=target(criteria), explorer_details=target(dict(criteria, view="details")) + "#detailed-intelligence",
                explorer_save=reverse("participants:saved-views") + "?" + urlencode(dict(criteria, return_to=target(criteria))),
                explorer_alternative_mode=request.GET.get("view") == "alternatives")


@workspace
@require_http_methods(["GET"])
def analytics(request):
    if not can_analytics(request.user):
        raise PermissionDenied
    return _participant_explorer(request, include_analysis=True)


PARTICIPANT_LIST_TABS = {'search': 'All participants', 'activity': 'Recent activity'}


def _participant_explorer(request, *, include_analysis):
    """One discovery pipeline and presentation for the directory and opt-in analysis."""
    # Dedicated activity sections use only the existing relationship selectors.
    # Search criteria remain in their return links and do not gate these reads.
    if not include_analysis and request.GET.get('listing') in {'activity', 'followups'}:
        from apps.crm.directory_reads import directory_sections, directory_panel
        return render(request, 'participants/intelligence.html', context(request,
            page_title='Participants', active_participant_tab='directory', explorer_unified=True,
            explorer_include_analysis=False, **directory_sections(request, PARTICIPANT_LIST_TABS), **directory_panel(request, 'participants')))
    try:
        params = _criteria_from_request(request)
        form, population, criteria = discover(request.user, params, strict=bool(request.GET.get("saved")))
    except ValidationError as exc:
        return render(request, "participants/workspace_error.html", context(request, error="; ".join(exc.messages)), status=400)
    from .travel_state import with_travel_preference
    population = with_travel_preference(population)
    page_size = int(form.cleaned_data.get("page_size") or 25) if form.is_valid() else 25
    page = Paginator(population, page_size).get_page(request.GET.get("page"))
    rows = list(page.object_list)
    from .models import IntakeFieldState
    from .intake_discovery import effective_travel
    travel_states = {row.participant_id:row for row in IntakeFieldState.objects.filter(
        participant_id__in=[p.pk for p in rows], destination="travel_willingness")}
    for person in rows:
        career = getattr(person, "career_profile", None)
        person.explorer_interest = getattr(person, "_current_interest", "")
        person.explorer_skills = career.skills_interests if career else ""
        person.explorer_employment = career.get_employment_interest_display() if career and career.employment_interest else "Not recorded"
        person.explorer_travel = {"yes":"Yes","maybe":"Maybe","no":"No"}.get(
            effective_travel(career, travel_states.get(person.pk),
                (person._travel_preference_id, person._travel_preference_at, person._travel_preference_value) if person._travel_preference_id else None), "Unknown")
    page.object_list = rows
    explorer_has_type = any(person.primary_identifier for person in rows)
    from .intelligence import build_intelligence
    alternative = request.GET.get("view") == "alternatives"
    data = build_intelligence(request.user, population, criteria, current_group=not alternative) if include_analysis and form.is_valid() else {}
    presentation = _explorer_presentation(request, form, population, criteria, data, page, include_analysis=include_analysis)
    if "q" in form.fields:
        form.fields["q"].widget.attrs.setdefault("placeholder", "Search by name, phone, email or ID")
    from .list_presentation import decorate_rows, insights
    presentation.update(decorate_rows(request.user, rows))
    if not include_analysis:
        if form.is_valid():
            presentation["list_insights"] = insights(request.user, population, presentation.get("explorer_groups", []))
    from apps.crm.directory_reads import directory_panel, directory_sections
    panel = directory_sections(request, PARTICIPANT_LIST_TABS)
    if panel['directory_listing'] != 'search':
        panel.update(directory_panel(request, 'participants'))
    return render(request, "participants/intelligence.html", context(request, page_title="Participants",
        active_participant_tab="analytics" if include_analysis else "directory", filter_form=form, criteria=criteria, page_obj=page,
        total_participants=page.paginator.count, result_pages=page.paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1), query_without_page=query_string(criteria),
        directory_url=reverse("participants:staff-directory") + "?" + query_string(criteria),
        explorer_details_open=request.GET.get("view") == "details", explorer_has_type=explorer_has_type, **presentation, **data, **panel),
        status=400 if form.errors else 200)
