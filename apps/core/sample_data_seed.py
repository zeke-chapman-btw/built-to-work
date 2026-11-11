"""Small fictional screen-review dataset, composed from existing workflows."""
import csv
import hashlib
import io
import secrets
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import CommandError
from django.utils import timezone

from apps.core.audit import record_audit
from apps.core.sample_data import SOURCE, USERNAMES, WHY

SAMPLE_NAMES = ("Avery Johnson", "Jordan Davis", "Morgan Lee", "Taylor Williams", "Casey Brown", "Riley Wilson",
    "Cameron Moore", "Skyler Martin", "Drew Thompson", "Reese White", "Parker Harris", "Quinn Clark",
    "Rowan Lewis", "Jamie Walker", "Dakota Hall", "Alex Allen", "Finley Young", "Emerson King",
    "Harper Wright", "Logan Scott", "Payton Green", "Sage Adams", "Blake Baker", "Charlie Nelson", "Empty Profile")


def audited(model, audit_actor, **values):
    """For configuration/evidence which has no standalone creation service.

Business workflows below use domain services instead. Every direct insert is
validated and explicitly audited, rather than relying on save signals.
"""
    item = model(**values)
    item.full_clean()
    item.save()
    record_audit(actor=audit_actor, action="sample_data.record.created", instance=item,
                 source=SOURCE, new_data={"sample": True}, reason=WHY)
    return item


def saved(pair):
    item, form = pair
    if item is None:
        raise CommandError("SAMPLE workflow validation failed: " + str(form.errors.as_json()))
    return item


def accounts():
    from apps.accounts.roles import ROLE_GROUPS, permission_names
    from apps.accounts.staff_services import save_account, set_active, revision
    from apps.staffing.services import save_profile, link_account
    User = get_user_model()
    password = secrets.token_urlsafe(24)
    admin = User.objects.create_superuser(username=USERNAMES[0], email="sample.admin@example.test",
        password=password, first_name="SAMPLE", last_name="Administrator")
    record_audit(actor=admin, action="sample_data.admin.created", instance=admin, source=SOURCE, reason=WHY)
    credentials = [(admin.username, password)]
    # Standard role configuration is shared reference data. Create a missing
    # standard group, but never rewrite permissions on an existing group.
    for role, name in ROLE_GROUPS.items():
        group, created = Group.objects.get_or_create(name=name)
        if created:
            keys = permission_names(role)
            group.permissions.set([p for p in Permission.objects.select_related("content_type")
                if f"{p.content_type.app_label}.{p.codename}" in keys])
            record_audit(actor=admin, action="sample_data.role_reference.created", instance=group, source=SOURCE, reason=WHY)
    profile = saved(save_profile(actor=admin, data={"first_name": "SAMPLE", "last_name": "Administrator",
        "email": admin.email, "phone": "7065550100", "shirt_size": "L", "is_active": True, "internal_notes": WHY}))
    saved(link_account(actor=admin, profile_id=profile.pk,
        data={"revision": profile.updated_at.isoformat(), "user": admin.pk, "confirm": True}))
    users = [admin]
    for index, (username, roles) in enumerate(zip(USERNAMES[1:],
            [("manager", "event_staff"), ("recruiter", "event_staff"), ("recruiter", "event_staff")]), 1):
        user = save_account(actor=admin, data={"username": username, "email": f"sample.staff{index}@example.test",
            "first_name": "SAMPLE", "last_name": ("Event lead", "Staff", "Staff without documents")[index-1],
            "roles": list(roles), "confirm_identity": True, "reason": WHY})
        set_active(actor=admin, user_id=user.pk, active=True, expected_revision=revision(user), reason=WHY)
        user.refresh_from_db()
        password = secrets.token_urlsafe(24)
        user.set_password(password)
        user.save(update_fields=["password"])
        record_audit(actor=admin, action="sample_data.password.initialized", instance=user, source=SOURCE, reason=WHY)
        if index == 2:
            # The ordinary Staff example can review documents; the final login
            # has the same recruiting role without these explicit grants.
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label="participants",
                codename__in=["view_participantdocument", "view_participantdocumentcategory", "view_participantdocumentaudit"]))
            record_audit(actor=admin, action="sample_data.document_access.granted", instance=user, source=SOURCE, reason=WHY)
        credentials.append((username, password))
        users.append(user)
    return users, credentials


def events(admin, users):
    from apps.events.event_management import save_event
    from apps.events.models import Event
    from apps.staffing.services import save_assignment, save_plan
    from apps.workdays.services import save_role, checkin, save_workday
    zone = ZoneInfo("America/New_York")
    today = timezone.localdate(timezone.now(), zone)
    result = []
    for offset, label in [(-21, "Career day"), (-7, "Skills roadshow"), (0, "Workforce showcase"), (21, "Hiring open house")]:
        day = today + timedelta(days=offset)
        event = saved(save_event(actor=admin, data={"name": "SAMPLE " + label, "description": WHY,
            "event_type": "other", "event_type_other": "SAMPLE screen review", "timezone_name": str(zone),
            "start_at": day.isoformat()+"T00:00", "end_at": day.isoformat()+"T23:59:59",
            "location_name": "SAMPLE Training Center", "address_line_1": "100 SAMPLE Way", "city": "Augusta",
            "state": "GA", "postal_code": "30901", "country": "US", "group_mode": "disabled", "group_label": "Group",
            "contact_name": "SAMPLE Coordinator", "contact_email": "sample.events@example.test", "contact_phone": "7065550110",
            "action": "publish"}))
        saved(save_plan(actor=admin, event_id=event.pk, data={"revision": "new", "required_staff": 2,
            "required_drivers": 0, "is_published": True, "need_"+day.isoformat(): 2, "drivers_"+day.isoformat(): 0}))
        for i, user in enumerate(users[1:3]):
            saved(save_assignment(actor=admin, event_id=event.pk, data={"staff": user.staff_profile.pk,
                "coverage_role": "staff_lead" if i == 0 else "staff", "role": "SAMPLE event support",
                "reason": WHY, "revision": "new", "day_"+day.isoformat(): True, "start_"+day.isoformat(): "08:00", "end_"+day.isoformat(): "17:00"}))
        result.append(event)
    role = saved(save_role(actor=admin, data={"name": "SAMPLE Event support", "is_active": True, "per_diem_eligible": "unknown", "reason": WHY, "revision": "new"}))
    for user in users[1:3]:
        saved(checkin(actor=user, event_id=result[2].pk, data={"work_date": today, "role": role.pk, "drove": False}))
        day = today-timedelta(days=7)
        saved(save_workday(actor=admin, event_id=result[1].pk, data={"staff": user.staff_profile.pk,
            "role": role.pk, "work_date": day, "effective_checkin_at": datetime.combine(day, time(8), zone).strftime("%Y-%m-%dT%H:%M"),
            "per_diem": "review", "drove": False, "reason": WHY, "revision": "new"}))
    return result


def people(admin, event):
    from apps.participants.models import ConsentDocumentVersion
    from apps.participants.intake_lifecycle import create_form, publish, revision, assign
    from apps.participants.intake_submission import start_session, complete_session
    from apps.participants.workspace_services import create_person, save_note, save_work
    from apps.recruiting.foundation import save_preferences, profile_fingerprint
    from apps.recruiting.models import RecruitingProfile
    from apps.participants.models import ParticipantCareerProfile
    document = audited(ConsentDocumentVersion, admin, key="sample-intake", version="SAMPLE-v1-"+uuid.uuid4().hex[:8],
        title="SAMPLE review consent", body="SAMPLE fictional consent for local screen review only.",
        content_hash=hashlib.sha256(b"SAMPLE fictional consent for local screen review only.").hexdigest(), is_approved=True)
    event.public_consent_document = document
    event.save(update_fields=["public_consent_document", "updated_at"])
    record_audit(actor=admin, action="sample_data.event_consent.configured", instance=event, source=SOURCE, reason=WHY)
    draft = create_form(actor=admin, name="SAMPLE Review intake")
    version = publish(actor=admin, version_id=draft.pk, expected_revision=revision(draft))
    assignment = assign(actor=admin, version_id=version.pk, channel="staff", event=event)
    result = []
    for i, name in enumerate(SAMPLE_NAMES):
        first, last = name.split(" ", 1)
        if i < 12:
            session = start_session(assignment=assignment, browser_key=secrets.token_hex(32), actor=admin, event=event)
            session.answers = {"first_name": "SAMPLE " + first, "last_name": last, "dob": "2002-04-15",
                "email": f"sample.person{i+1:02d}@example.test", "phone": f"70655501{i+20:02d}",
                "city": ("Augusta", "Savannah", "Macon")[i % 3], "state": "GA", "postal_code": "30901",
                "consent": True, "current_situation": "unemployed", "career_timeline": "exploring",
                "opportunity_types": ["exploration"], "travel_willingness": ("yes", "maybe", "no")[i % 3]}
            if i % 3 != 2:
                session.answers["travel_range"] = "regional"
            session.current_step = 4
            session.consent_accepted_at = timezone.now()
            session.save()
            person = complete_session(session_id=session.pk, actor=admin, invite_participant=False).participant
        else:
            person = create_person(actor=admin, data={"first_name": "SAMPLE " + first, "last_name": last,
                "audit_reason": WHY, **({"contact_email": f"sample.person{i+1:02d}@example.test", "city": "Augusta", "state": "GA"} if i % 2 and i != 24 else {})})
        if i != 24:
            person.refresh_from_db()
            recruiting = RecruitingProfile.objects.filter(participant=person).first()
            if recruiting is None:
                recruiting = save_preferences(actor=admin, participant_id=person.pk, expected_revision=0, data={}, why=WHY)
            save_preferences(actor=admin, participant_id=person.pk, expected_revision=recruiting.revision if recruiting else 0,
                data={"employment_interest": ("seeking", "open", "not_seeking")[i % 3],
                    "willing_to_travel": (True, "maybe", False)[i % 3], "fingerprint": profile_fingerprint(person)}, why=WHY)
        if i < 8:
            save_note(actor=admin, participant_id=person.pk, data={"content": "SAMPLE Interested in a site tour; prefers an afternoon call.", "request_key": uuid.uuid4(), "audit_reason": WHY})
            save_work(actor=admin, participant_id=person.pk, data={"employer": "SAMPLE Regional Fabrication", "role": "SAMPLE Shop assistant",
                "location": "SAMPLE Augusta site", "started_on": timezone.localdate()-timedelta(days=730), "ended_on": timezone.localdate()-timedelta(days=90),
                "is_current": False, "description": "SAMPLE Assisted with layout, tool care and material handling.", "source_reference": WHY, "audit_reason": WHY})
        result.append(person)
    return result


def qualifications(admin, persons):
    from apps.participants.models import ParticipantQualification
    from apps.participants.verification_models import ParticipantVerificationRecord
    from apps.participants.verification_services import qualification_snapshot
    for person in persons[:10]:
        for kind, label in [("skill", "Blueprint reading"), ("training", "Technical college welding"), ("credential", "OSHA 10"), ("credential", "Class B driving license")]:
            item = audited(ParticipantQualification, admin, participant=person, custom_name="SAMPLE " + label,
                reported_kind=kind, reported_provider="SAMPLE Technical College", source="staff",
                experience_details="SAMPLE Review evidence", notes=WHY, created_by=admin, updated_by=admin)
            if kind == "credential" and person in persons[:4]:
                audited(ParticipantVerificationRecord, admin, participant=person, qualification=item, status="verified",
                    method="staff_confirmed", evidence_notes="SAMPLE Fictional certificate confirmed for screen review.", reason=WHY,
                    reviewer=admin, reviewed_at=timezone.now(), qualification_snapshot=qualification_snapshot(item))


def touchpoints(admin, staff, persons):
    from apps.crm.services import create_internal, create_followup
    for i, kind in enumerate(("call", "sms", "email", "meeting")):
        interaction = create_internal(actor=admin, participant_id=persons[0].pk, operation_id=uuid.uuid4(), data={
            "activity_type": kind, "direction": "outbound", "summary": "SAMPLE " + kind.title() + " about next steps",
            "notes": "SAMPLE Manual observation only; no message was sent.", "occurred_at": timezone.now()-timedelta(days=i),
            "outcome": "SAMPLE Discussed a site visit", "next_step": "SAMPLE Arrange a tour", "responsible_staff": staff.staff_profile})
        if i == 0:
            saved(create_followup(actor=admin, interaction_id=interaction.pk, operation_id=uuid.uuid4(), data={
                "title": "SAMPLE Arrange Avery's site tour", "description": WHY, "assigned_staff": staff.staff_profile.pk,
                "due_date": timezone.localdate(), "priority": "normal", "reason": WHY, "revision": "new"}))


def customers(admin, persons):
    from apps.customers import services
    from apps.customers.models import CustomerAccessPlan
    from apps.recruiting.models import RecruitingOpportunity, RecruitingStage
    from apps.recruiting.selection import select_candidate, conflict_set
    from apps.recruiting.workflow import transition
    for i, name in enumerate(("Riverbend Fabrication", "Coastal Site Services")):
        org = services.save_organization(actor=admin, data={"name": "SAMPLE " + name, "status": "active"}, why=WHY)
        contact = services.save_child(actor=admin, org_id=org.pk, expected_org_revision=org.revision,
            kind="contact", data={"first_name": "SAMPLE", "last_name": "Hiring coordinator", "email": f"sample.customer{i+1}@example.test",
                "phone": f"70655501{80+i}", "title": "SAMPLE Hiring coordinator"}, why=WHY)
        org.refresh_from_db()
        plan = org.access_plan
        services.save_access_plan(actor=admin, org_id=org.pk, expected_org_revision=org.revision,
            expected_revision=plan.revision, data={"workforce_need_creation": True}, why=WHY)
        org.refresh_from_db()
        need = services.save_hiring_draft(actor=admin, org_id=org.pk, expected_org_revision=org.revision,
            data={"title": "SAMPLE " + ("Welding apprentice" if i == 0 else "Equipment operator"), "trade": "SAMPLE Skilled trades",
                "description": WHY, "worker_quantity": 3, "employment_type": "full_time", "location_mode": "custom",
                "location_name": "SAMPLE Augusta shop", "address_line1": "100 SAMPLE Way", "city": "Augusta", "location_state": "GA", "country": "US",
                "pay_response": "provided", "pay_minimum": Decimal("20"), "pay_maximum": Decimal("26"), "pay_currency": "USD", "pay_basis": "hour",
                "overtime_response": "no", "benefits_response": "none", "per_diem_response": "none", "shift": "day", "schedule_details": "SAMPLE Monday to Friday, 8 AM to 5 PM",
                "hours_mode": "specified", "hours_per_week": Decimal("40"), "start_mode": "timeframe", "start_timeframe": "SAMPLE Within one month",
                "duration_response": "ongoing", "required_skills_response": "none", "preferred_skills_response": "none",
                "required_experience_response": "none", "preferred_experience_response": "none", "required_credentials_response": "none",
                "preferred_credentials_response": "none", "travel_response": "no", "overnight_response": "no",
                "conditions_response": "none", "equipment_response": "none", "screening_response": "none", "other_response": "none",
                "primary_contact": contact, "hiring_timeline": "SAMPLE Within one month"}, why=WHY)
        for action in ("submit", "review", "approve"):
            org.refresh_from_db(); need.refresh_from_db()
            need = services.revision_action(actor=admin, org_id=org.pk, need_id=need.pk, expected_org_revision=org.revision,
                expected_need_revision=need.revision, revision_id=need.working_revision_id, action=action, why=WHY)
        opportunity = audited(RecruitingOpportunity, admin, need=need, owner=admin)
        for j, person in enumerate(persons[i*3:i*3+3]):
            conflicts = conflict_set(admin, person, excluding=opportunity.pk)
            row = select_candidate(actor=admin, opportunity_id=opportunity.pk, participant_id=person.pk,
                expected_conflicts=conflicts["fingerprint"], expected_need_revision=need.effective_revision_id,
                acknowledged=True, why=WHY)
            stages = list(RecruitingStage.objects.filter(is_active=True).order_by("position"))
            stage = next((s for s in stages if s.semantic == ("active" if j != 2 else "not_selected") and s.pk != row.stage_id), None)
            if stage and j != 0:
                transition(actor=admin, candidate_id=row.pk, stage_key=stage.pk, expected_revision=row.revision, why=WHY)


def documents(admin, person):
    from apps.participants.participant_document_models import ParticipantDocumentCategory, ParticipantDocument, ParticipantDocumentAudit
    category = audited(ParticipantDocumentCategory, admin, name="SAMPLE Certificates", slug="sample-certificates")
    # A small valid PDF: fictional text only. No real files are copied.
    stream = b"BT /F1 16 Tf 40 80 Td (SAMPLE - fictional screen review document) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 150] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", b"<< /Length " + str(len(stream)).encode()+b" >>\nstream\n"+stream+b"\nendstream"]
    data = b"%PDF-1.4\n"; offsets = [0]
    for i, value in enumerate(objects, 1):
        offsets.append(len(data)); data += str(i).encode()+b" 0 obj\n"+value+b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(f"{n:010d} 00000 n \n".encode() for n in offsets[1:])
    data += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"+str(start).encode()+b"\n%%EOF\n"
    for title in ("SAMPLE Certificate", "SAMPLE Resume"):
        item = audited(ParticipantDocument, admin, participant=person, category=category, file=ContentFile(data, name=title+".pdf"),
            original_filename=title+".pdf", content_type="application/pdf", file_size=len(data), uploaded_by=admin,
            description=title, participant_visible=True)
        audited(ParticipantDocumentAudit, admin, document=item, actor=admin, action="uploaded", reason=WHY)


def trailer_results(admin, persons, sample_events):
    from apps.events.services import register_participant
    from apps.stations.models import Station, ExperienceActivity
    from apps.stations.services import assign_station, activate_event_context, process_station_scan, start_test_session
    from apps.assessments.models import AssessmentCategory, QuestionSet, Question, QuestionChoice, QuestionSetItem, EventAssessmentCategory
    from apps.assessments.services import create_attempt, submit_answer, void_and_restart, _start_section
    from apps.simulators.models import SimulatorCaptureProfile, EventSimulatorConfiguration
    from apps.simulators.services import ingest_capture
    from apps.games.models import GameDefinition, EventGameConfiguration
    from apps.games.services import resolve_and_start, complete_game_session
    event = sample_events[2]
    stations = []
    for kind in ("kiosk", "simulator", "game"):
        station = audited(Station, admin, name="SAMPLE " + kind.title(), code="SAMPLE_"+kind.upper(), station_type=kind)
        assignment, _ = assign_station(event=event, station=station, actor=admin)
        activate_event_context(assignment=assignment, actor=admin)
        stations.append(station)
    configs = []
    for label in ("Construction safety", "Mechanical reasoning"):
        category = audited(AssessmentCategory, admin, name="SAMPLE " + label, slug="sample-"+label.lower().replace(" ", "-"))
        question_set = audited(QuestionSet, admin, category=category, name="SAMPLE " + label)
        for n in range(15):
            question = audited(Question, admin, category=category, text=f"SAMPLE Question {n+1}: choose the safe procedure.", difficulty=("easy", "medium", "hard")[n//5])
            for c in range(4):
                audited(QuestionChoice, admin, question=question, text=f"SAMPLE Option {c+1}", is_correct=c == 0, display_order=c)
            audited(QuestionSetItem, admin, question_set=question_set, question=question)
        question_set.publish()
        record_audit(actor=admin, action="sample_data.question_set.published", instance=question_set, source=SOURCE, reason=WHY)
        configs.append(audited(EventAssessmentCategory, admin, event=event, category=category, question_set=question_set, display_name="SAMPLE " + label))
    profile = audited(SimulatorCaptureProfile, admin, name="SAMPLE Excavator capture", simulator_type="SAMPLE Excavator")
    audited(EventSimulatorConfiguration, admin, event=event, station=stations[1], profile=profile, simulator_type="SAMPLE Excavator")
    game = audited(GameDefinition, admin, key="sample-review-game", display_name="SAMPLE Duck Hunt", implementation_key="duck_hunt")
    audited(EventGameConfiguration, admin, event=event, game=game)
    for i, person in enumerate(persons[:6]):
        reg, _ = register_participant(event=event, participant=person, actor=admin)
        ticket = reg.tickets.get(is_current=True)
        token = str(ticket.token)
        scan = process_station_scan(station_code=stations[0].code, token=token, actor=admin)
        if "session" not in scan:
            raise CommandError("SAMPLE station scan failed: " + scan["status"])
        session = scan["session"]
        attempt = create_attempt(session, [c.pk for c in configs], actor=admin)
        finish_quiz(attempt, submit_answer, _start_section, i)
        if i == 1:
            replacement = void_and_restart(attempt, actor=admin, reason="SAMPLE Device interrupted; original test is void")
            finish_quiz(replacement, submit_answer, _start_section, i+1)
        process_station_scan(station_code=stations[2].code, token=token, actor=admin)
        game_session = resolve_and_start(experience_session=session)
        complete_game_session(game_session=game_session, raw_score=120+i, result_data={"note": WHY}, actor=admin)
        process_station_scan(station_code=stations[1].code, token=token, actor=admin)
        capture, _ = ingest_capture(submission_id=uuid.uuid4(), event_id=event.pk, station_code=stations[1].code,
            profile_id=profile.pk, profile_version=profile.profile_version, raw_identifier=ticket.ticket_number,
            raw_total_score=str(Decimal("73.125")+i), captured_at=timezone.now(), diagnostic_metadata={"note": WHY})
        if capture.status != "matched":
            raise CommandError("SAMPLE simulator capture did not attach: " + capture.status)
    practice = start_test_session(event=event, mode="demo", actor=admin)
    attempt = create_attempt(practice, [c.pk for c in configs], is_official=False, actor=admin)
    finish_quiz(attempt, submit_answer, _start_section, 2)
    for old_event in sample_events[:2]:
        for person in persons[:4]:
            reg, _ = register_participant(event=old_event, participant=person, actor=admin, override_reason=WHY)


def finish_quiz(attempt, submit_answer, start_section, offset):
    for section in attempt.sections.order_by("position"):
        if section.status == "pending":
            start_section(section, timezone.now())
        for index, question in enumerate(section.questions_snapshot):
            correct = (index+offset) % 4 != 0
            choice = next(c for c in question["choices"] if c["correct"] == correct)
            if not submit_answer(section, question["position"], choice["key"]):
                raise CommandError("SAMPLE quiz answer was rejected.")


def historical_score(admin, person):
    from apps.participants.historical import create_batch, preview_batch, resolve_review
    output = io.StringIO()
    row = {"first_name": person.first_name, "last_name": person.last_name, "identity_uuid": str(person.identity_uuid),
        "event_name": "SAMPLE Past trailer visit", "event_date": (timezone.localdate()-timedelta(days=365)).isoformat(),
        "simulator_score": "82.5", "score_method": "SAMPLE Original percent", "score_source": "SAMPLE Archived demonstration sheet"}
    writer = csv.DictWriter(output, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
    batch = create_batch(uploaded_file=SimpleUploadedFile("SAMPLE_past_scores.csv", output.getvalue().encode(), content_type="text/csv"), actor=admin)
    preview_batch(batch, {key: key for key in row}, actor=admin)
    item = batch.rows.get(row_number=2)
    resolve_review(item, actor=admin, decision="link", reason=WHY, participant=person)


def create_samples():
    # Do not let intake identity matching modify a pre-existing person. Reserved
    # sample contacts must be unused unless an ownership manifest was found.
    from django.db.models import Q
    from apps.participants.models import Participant
    contacts = Q()
    for i in range(25):
        contacts |= Q(contact_email__iexact=f"sample.person{i+1:02d}@example.test")
        contacts |= Q(contact_phone=f"70655501{i+20:02d}")
    for name in SAMPLE_NAMES:
        first, last = name.split(" ", 1)
        contacts |= Q(first_name__iexact="SAMPLE " + first, last_name__iexact=last)
    if Participant.objects.filter(contacts).exists():
        raise CommandError("A reserved SAMPLE identity or contact already belongs to an existing person; nothing changed.")
    users, credentials = accounts()
    admin = users[0]
    sample_events = events(admin, users)
    persons = people(admin, sample_events[2])
    qualifications(admin, persons)
    touchpoints(admin, users[2], persons)
    customers(admin, persons)
    documents(admin, persons[0])
    trailer_results(admin, persons, sample_events)
    historical_score(admin, persons[0])
    return credentials
