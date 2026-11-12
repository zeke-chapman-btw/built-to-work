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


CITIES = (("Augusta", "30901"), ("Savannah", "31401"), ("Macon", "31201"), ("Evans", "30809"), ("Aiken", "29801"), ("Statesboro", "30458"))
TRADES = ("welding", "electrical", "hvac", "equipment", "plumbing", "diesel", "carpentry", "maintenance")
TRADE_LABELS = {"welding": "Welding", "electrical": "Electrical", "hvac": "HVAC", "equipment": "Heavy equipment",
    "plumbing": "Plumbing", "diesel": "Diesel mechanic", "carpentry": "Carpentry", "maintenance": "Industrial maintenance"}
CREDENTIALS = (("OSHA 10", "SAMPLE OSHA Training Institute"), ("Forklift operator", "SAMPLE Augusta Tech"),
    ("CPR / First aid", "SAMPLE Red Cross"), ("AWS D1.1 welding", "SAMPLE American Welding Society"), ("Class A CDL", "SAMPLE Georgia DDS"),
    ("NCCER Core", "SAMPLE NCCER"), ("EPA 608 (HVAC)", "SAMPLE EPA"), ("OSHA 30", "SAMPLE OSHA Training Institute"))
SKILLS = (("Blueprint reading", ""), ("MIG welding", ""), ("Hand and power tools", ""), ("Equipment maintenance", ""),
    ("Electrical wiring basics", ""), ("TIG welding", ""), ("Crew leadership", ""), ("Measuring and layout", ""))
TRAINING = (("Welding certificate program", "SAMPLE Augusta Technical College"), ("HVAC program", "SAMPLE Savannah Technical College"),
    ("Construction CTE pathway", "SAMPLE Richmond County high school"), ("Carpentry training", "SAMPLE Job Corps"))
NOTES = ("Very interested in welding. Asked about evening classes so he can keep his current job.",
    "Prefers texts over calls. Best time to reach is after 3 PM.",
    "Came to the trailer with a friend. Scored well on the simulator and wants to try again.",
    "Looking for something with benefits. Open to a 30–45 minute commute.",
    "Has reliable transportation. Can start within two weeks.",
    "Finishing high school in May. Parent asked for apprenticeship info.",
    "Recently moved to the area. Has forklift experience from a warehouse job.",
    "Wants to work outdoors. Not interested in shop or factory work.")
JOBS = ((("Regional Fabrication", "Shop assistant", "Augusta, GA", 2, 3, False, "Assisted with layout, tool care and material handling."),),
    (("Peach State Logistics", "Warehouse associate", "Savannah, GA", 1.5, 0, True, "Loads and unloads trucks; forklift certified."),),
    (("Main Street Diner", "Line cook", "Macon, GA", 1, 4, False, "Fast-paced kitchen work; opened and closed the line."),
     ("Big Box Home Store", "Sales associate", "Macon, GA", 2, 0, True, "Lumber and tools department.")),
    (("Garden City Builders", "Laborer", "Savannah, GA", 3, 0, True, "Framing, concrete prep and site cleanup."),),
    (("Quick Lube Plus", "Lube technician", "Evans, GA", 1, 6, False, "Oil changes, tire rotations and basic inspections."),),
    (("Southeast Poultry", "Maintenance helper", "Statesboro, GA", 2.5, 0, True, "Helps mechanics with conveyor repairs and PM work."),))
TOUCHES = (
    ("call", "outbound", "Intro call after trailer visit", "Talked about welding interest and next steps.", "Interested", "Send class schedule"),
    ("sms", "outbound", "Texted hiring event details", "Sent date, time and address for the hiring event.", "Replied yes", "Confirm attendance"),
    ("voicemail", "outbound", "Left voicemail about open roles", "Mentioned two openings near Augusta.", "No answer", "Try again Thursday"),
    ("email", "outbound", "Sent apprenticeship info packet", "Included program dates and pay range.", "Sent", "Check if they read it"),
    ("call", "inbound", "Called asking about CDL training", "Wants to know about cost and schedule.", "Answered questions", "Share CDL school list"),
    ("meeting", "outbound", "Met at Augusta job fair", "Good conversation; brought a resume.", "Strong fit", "Introduce to Riverbend"),
    ("recruiting", "outbound", "Discussed welding apprentice role", "Walked through role, pay and shift.", "Wants to apply", "Submit to employer"),
    ("attempt", "outbound", "Tried to reach by phone", "Line was busy.", "No contact", "Text instead"),
    ("video", "outbound", "Video check-in on training", "Halfway through the OSHA 10 course.", "On track", "Check back in two weeks"),
    ("event", "inbound", "Stopped by the trailer", "Tried the simulator and quiz.", "Registered", "Follow up next week"),
    ("interview", "outbound", "Mock interview practice", "Practiced common questions; needs work on examples.", "Improving", "Schedule a second practice"),
    ("follow_up", "outbound", "Followed up on site tour", "Tour went well; asked about start dates.", "Positive", "Arrange a site tour"))


def intake_answers(i, first, last):
    city, postal = CITIES[i % len(CITIES)]
    situation = ("unemployed", "employed_other", "employed_trade", "trade_school", "unemployed", "employed_other")[i % 6]
    answers = {"first_name": "SAMPLE " + first, "last_name": last,
        "dob": f"{1978 + (i * 3) % 26}-{1 + i % 12:02d}-{10 + i:02d}",
        "email": f"sample.person{i+1:02d}@example.test", "phone": f"70655501{i+20:02d}",
        "street_address": f"{100 + i * 17} SAMPLE {('Oak', 'Pine', 'Maple', 'Cedar')[i % 4]} St",
        "city": city, "state": "GA", "postal_code": postal, "consent": True,
        "current_situation": situation,
        "education_level": ("diploma", "ged", "some_college", "trade", "diploma", "associate")[i % 6],
        "industry_interests": [TRADES[i % len(TRADES)]] + ([TRADES[(i + 3) % len(TRADES)]] if i % 2 else []),
        "hands_on": ("yes", "yes", "not_sure")[i % 3],
        "license_status": ("valid", "valid", "permit", "none")[i % 4],
        "certifications": ("yes", "no", "yes", "not_sure")[i % 4],
        "career_timeline": ("asap", "1_3_months", "3_6_months", "exploring", "asap", "over_6_months")[i % 6],
        "opportunity_types": [("full_time", "apprenticeship", "trade_school", "full_time", "part_time", "apprenticeship")[i % 6]],
        "career_priorities": list(dict.fromkeys([("pay", "learning", "security", "advancement")[i % 4], ("balance", "enjoyment", "pay")[i % 3]])),
        "travel_willingness": ("yes", "maybe", "no")[i % 3],
        "desired_pay": ("20_24", "25_29", "15_19", "30_34", "open")[i % 5],
        "referral_source": ("social", "school", "family", "community", "search", "employer")[i % 6],
        "updates_preference": "yes"}
    if answers["hands_on"] == "yes":
        answers["experiences"] = [{"entry_id": str(uuid.uuid4()), "area": TRADES[i % len(TRADES)] if TRADES[i % len(TRADES)] != "maintenance" else "maintenance",
            "source": ("employment", "school", "personal", "training")[i % 4], "paid": "paid" if i % 4 == 0 else "unpaid",
            "duration": ("1_2_years", "under_6_months", "6_12_months", "3_5_years")[i % 4],
            "skills": [("mig", "blueprints", "tools", "fabrication")[i % 4]] if TRADES[i % len(TRADES)] == "welding" else [], "proficiency": ("intermediate", "beginner", "advanced")[i % 3],
            "description": "SAMPLE Hands-on work on small projects."}]
    if answers["license_status"] == "valid":
        answers["license_class"] = ("standard", "cdl_a", "standard", "cdl_b")[i % 4]
    if answers["certifications"] == "yes":
        answers["certification_choices"] = [("osha10", "forklift", "cpr", "aws")[i % 4]]
    if situation in ("employed_other", "employed_trade"):
        answers["current_industry"] = ("logistics", "retail", "construction", "manufacturing")[i % 4]
        answers["job_title"] = ("Warehouse associate", "Sales associate", "Laborer", "Machine operator")[i % 4]
        answers["employment_duration"] = ("1_2_years", "6_12_months", "3_5_years")[i % 3]
    if situation == "trade_school":
        answers["school_name"] = "SAMPLE Augusta Technical College"
    if answers["travel_willingness"] != "no":
        answers["travel_range"] = ("regional", "statewide", "local")[i % 3]
    return answers


def fill_staff_profile(admin, person, i):
    from apps.participants.workspace_services import save_profile, profile_revision
    city, postal = CITIES[i % len(CITIES)]
    trade = TRADE_LABELS[TRADES[i % len(TRADES)]]
    save_profile(actor=admin, participant_id=person.pk, revision=profile_revision(person), reason=WHY,
        person_data={"first_name": person.first_name, "last_name": person.last_name, "preferred_name": "",
            "contact_email": person.contact_email or f"sample.person{i+1:02d}@example.test",
            "contact_phone": person.contact_phone or (f"70655501{i+20:02d}" if i % 3 else ""),
            "city": city, "state": "GA", "postal_code": postal},
        career_data={"employment_status": ("Unemployed", "Employed, not in a trade", "Part-time")[i % 3],
            "employment_interest": "", "career_interests": trade, "skills_interests": "Hands-on work, " + trade.lower(),
            "years_of_experience": Decimal(str(i % 6)), "education_training": ("High school diploma", "GED", "Some college")[i % 3],
            "work_availability": ("Weekdays", "Any shift", "Evenings and weekends")[i % 3], "willing_to_travel": None,
            "preferred_locations": city + " area", "preferred_work_type": ("full_time", "any", "part_time")[i % 3]})


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
            session.answers = intake_answers(i, first, last)
            session.current_step = 4
            session.consent_accepted_at = timezone.now()
            session.save()
            person = complete_session(session_id=session.pk, actor=admin, invite_participant=False).participant
        else:
            person = create_person(actor=admin, data={"first_name": "SAMPLE " + first, "last_name": last,
                "audit_reason": WHY, **({"contact_email": f"sample.person{i+1:02d}@example.test", "city": "Augusta", "state": "GA"} if i % 2 and i != 24 else {})})
        if 12 <= i < 21:
            fill_staff_profile(admin, person, i)
        if i != 24:
            person.refresh_from_db()
            recruiting = RecruitingProfile.objects.filter(participant=person).first()
            if recruiting is None:
                recruiting = save_preferences(actor=admin, participant_id=person.pk, expected_revision=0, data={}, why=WHY)
            save_preferences(actor=admin, participant_id=person.pk, expected_revision=recruiting.revision if recruiting else 0,
                data={"employment_interest": ("seeking", "open", "not_seeking")[i % 3],
                    "willing_to_travel": (True, "maybe", False)[i % 3], "fingerprint": profile_fingerprint(person)}, why=WHY)
        if i < 20:
            for note in [NOTES[i % len(NOTES)]] + ([NOTES[(i + 3) % len(NOTES)]] if i % 3 == 0 else []):
                save_note(actor=admin, participant_id=person.pk, data={"content": "SAMPLE " + note, "request_key": uuid.uuid4(), "audit_reason": WHY})
        if i < 18:
            for k, (employer, role, place, years, months_ago, current, text) in enumerate(JOBS[i % len(JOBS)]):
                save_work(actor=admin, participant_id=person.pk, data={"employer": "SAMPLE " + employer, "role": role,
                    "location": place, "started_on": timezone.localdate()-timedelta(days=int(365*years)+30*months_ago),
                    "ended_on": None if current else timezone.localdate()-timedelta(days=30*months_ago),
                    "is_current": current, "description": text, "source_reference": WHY, "audit_reason": WHY})
        result.append(person)
    return result


def qualifications(admin, persons):
    from apps.participants.models import ParticipantQualification
    from apps.participants.verification_models import ParticipantVerificationRecord
    from apps.participants.verification_services import qualification_snapshot
    for n, person in enumerate(persons[:20]):
        picks = ([("credential", CREDENTIALS[(n + k) % len(CREDENTIALS)]) for k in range((n + 3) % 4)] +
                 [("skill", SKILLS[(n * 2 + k) % len(SKILLS)]) for k in range(1 + n % 3)] +
                 ([("training", TRAINING[n % len(TRAINING)])] if n % 2 == 0 else []))
        for k, (kind, (label, provider)) in enumerate(picks):
            item = audited(ParticipantQualification, admin, participant=person, custom_name=label,
                reported_kind=kind, reported_provider=provider, source="staff",
                experience_details="SAMPLE Reported during a follow-up call", notes=WHY, created_by=admin, updated_by=admin)
            if kind == "credential" and (n + k) % 3 == 0:
                audited(ParticipantVerificationRecord, admin, participant=person, qualification=item, status="verified",
                    method="staff_confirmed", evidence_notes="SAMPLE Fictional certificate confirmed for screen review.", reason=WHY,
                    reviewer=admin, reviewed_at=timezone.now()-timedelta(days=n * 3), qualification_snapshot=qualification_snapshot(item))


def touchpoints(admin, users, persons):
    from apps.crm.services import create_internal, create_followup
    team = [u for u in users if getattr(u, "staff_profile", None)] or [admin]
    today = timezone.localdate()
    for n, person in enumerate(persons[:22]):
        count = (4, 3, 2, 5, 1, 3, 2, 1, 4, 2, 3, 1, 2, 1, 3, 1, 2, 1, 1, 2, 1, 1)[n]
        days_ago = 2 + (n * 5) % 40
        for k in range(count):
            kind, direction, summary, notes, outcome, next_step = TOUCHES[(n * 3 + k) % len(TOUCHES)]
            author = team[(n + k) % len(team)]
            owner = team[(n + k + 1) % len(team)]
            interaction = create_internal(actor=author, participant_id=person.pk, operation_id=uuid.uuid4(), data={
                "activity_type": kind, "direction": direction, "summary": summary,
                "notes": notes + " (SAMPLE, no message was sent.)",
                "occurred_at": timezone.now() - timedelta(days=days_ago, hours=(n * 7 + k * 3) % 9),
                "outcome": outcome, "next_step": next_step, "responsible_staff": owner.staff_profile if owner.staff_profile else None})
            days_ago += 6 + (n + k) % 18
            if k == 0 and n in (0, 3, 5, 8, 10, 14):
                saved(create_followup(actor=author, interaction_id=interaction.pk, operation_id=uuid.uuid4(), data={
                    "title": "SAMPLE " + next_step + " – " + person.first_name.replace("SAMPLE ", ""), "description": WHY,
                    "assigned_staff": owner.staff_profile.pk, "due_date": today + timedelta(days=(-3, 2, 5, -1, 7, 1)[(0, 3, 5, 8, 10, 14).index(n)]),
                    "priority": ("high" if n in (0, 8) else "normal"), "reason": WHY, "revision": "new"}))


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


def documents(admin, persons):
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
    for person, title in ((persons[0], "SAMPLE Certificate"), (persons[0], "SAMPLE Resume"), (persons[1], "SAMPLE Resume"),
                          (persons[3], "SAMPLE OSHA 10 card"), (persons[5], "SAMPLE Resume"), (persons[8], "SAMPLE Forklift certificate")):
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
    touchpoints(admin, users, persons)
    customers(admin, persons)
    documents(admin, persons)
    trailer_results(admin, persons, sample_events)
    historical_score(admin, persons[0])
    return credentials
