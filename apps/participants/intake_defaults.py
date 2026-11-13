"""Owner-approved default content for a new, unpublished Build 11B draft."""
from .intake_contracts import SCHEMA


def choices(values):
    return [{"value": key, "label": label} for key, label in values]


def default_contract():
    questions = []
    def q(key, label, kind="short_text", step=1, required=False, options=None, rule=None, **extra):
        item = {"key": key, "label": label, "type": kind, "step": step,
                "required": required, "destination": key, "options": choices(options or [])}
        if rule:
            item["rule"] = rule
        if kind in {"short_text", "email", "phone"}:
            item["max_length"] = {"email": 254, "phone": 32, "street_address": 200,
                                  "postal_code": 24}.get(key, 100 if step == 1 else 200)
        if kind == "long_text":
            item["max_length"] = 3000
        item.update(extra)
        questions.append(item)
        return item
    def eq(field, value):
        return {"field": field, "op": "equals", "value": value}
    def within(field, values):
        return {"field": field, "op": "in", "value": values}
    def contains(field, value):
        return {"field": field, "op": "contains", "value": value}
    yesno = [("yes", "Yes"), ("no", "No")]
    uncertain = yesno + [("not_sure", "Not sure")]
    durations = [("under_6_months", "Less than 6 months"), ("6_12_months", "6–12 months"),
                 ("1_2_years", "1–2 years"), ("3_5_years", "3–5 years"),
                 ("6_10_years", "6–10 years"), ("over_10_years", "More than 10 years")]
    trades = [("electrical", "Electrical"), ("welding", "Welding/Fabrication"),
              ("hvac", "HVAC"), ("plumbing", "Plumbing/Pipefitting"),
              ("carpentry", "Carpentry"), ("equipment", "Heavy Equipment"),
              ("diesel", "Diesel/Automotive"), ("maintenance", "Industrial Maintenance"),
              ("management", "Construction Management"), ("other", "Other")]
    q("first_name", "First name", required=True)
    q("last_name", "Last name", required=True)
    q("preferred_name", "Preferred name")
    q("dob", "Date of birth", "date", required=True)
    q("email", "Email", "email", required=True)
    q("phone", "Phone", "phone", required=True)
    q("street_address", "Street address")
    q("city", "City", required=True)
    q("state", "State", required=True)
    q("postal_code", "ZIP code", required=True)
    q("consent", "I agree to the approved consent shown", "consent", required=True)
    q("current_situation", "What best describes your current situation?", "single_choice", 2, True, [
        ("high_school", "High school student"), ("college", "College student"),
        ("trade_school", "Trade school student"), ("employed_trade", "Employed in construction or a skilled trade"),
        ("employed_other", "Employed in another industry"), ("unemployed", "Unemployed"),
        ("retired", "Retired"), ("other", "Other")])
    q("education_level", "Current or highest education", "single_choice", 2, options=[
        ("some_high_school", "Some high school"), ("diploma", "High school diploma"),
        ("ged", "GED/high school equivalency"), ("some_college", "Some college"),
        ("associate", "Associate degree"), ("bachelor_plus", "Bachelor's degree or higher"),
        ("trade", "Trade school/technical certification"), ("other", "Other"),
        ("prefer_not", "Prefer not to answer")])
    student = within("current_situation", ["high_school", "college", "trade_school"])
    q("school_name", "School or training institution", step=2, rule=student)
    q("graduation", "Expected graduation or completion (year or month/year)", step=2, rule=student)
    q("school_activities", "Relevant school activities", step=2, rule=student)
    q("training", "Previous trade training", step=2)
    employed = within("current_situation", ["employed_trade", "employed_other"])
    q("current_industry", "Current employment industry", "single_choice", 2, True, [
        ("retail", "Retail/Customer Service"), ("hospitality", "Restaurant/Hospitality"),
        ("logistics", "Warehouse/Logistics"), ("manufacturing", "Manufacturing"),
        ("construction", "Construction/Skilled Trades"), ("office", "Office/Administration"),
        ("agriculture", "Agriculture/Landscaping"), ("other", "Other")], employed)
    q("industry_other", "Other industry", step=2, rule=eq("current_industry", "other"))
    q("job_title", "Job title", step=2, rule=employed)
    employment_duration = durations[:4] + [("over_5_years", "More than 5 years")]
    q("employment_duration", "Employment duration", "single_choice", 2, options=employment_duration, rule=employed)
    q("job_responsibilities", "Job responsibilities", "long_text", 2, rule=employed)
    q("prior_employment", "Previous employment history (optional)", "long_text", 2)
    q("industry_interests", "Which industries interest you? Select up to three", "multiple_choice", 3,
      options=trades + [("not_sure", "Not sure yet")], max_selections=3, exclusive=["not_sure"])
    q("interest_other", "Other industry interest", step=3, rule=contains("industry_interests", "other"))
    q("hands_on", "Do you have hands-on trade experience?", "single_choice", 3, options=uncertain)
    skills = [("mig", "MIG"), ("tig", "TIG"), ("stick", "Stick"),
              ("blueprints", "Blueprint reading"), ("fabrication", "Metal fabrication"),
              ("inspection", "Weld inspection / quality control"), ("tools", "Hand/power tools"),
              ("crew", "Crew coordination"), ("safety", "Jobsite safety"),
              ("quality", "Quality control"), ("scheduling", "Scheduling"), ("leadership", "Leadership")]
    def child(key, label, required, opts=None, kind="single_choice", **extra):
        return {"key": key, "label": label, "type": kind, "step": 3,
                "required": required, "destination": key, "options": choices(opts or []), **extra}
    q("experiences", "Hands-on experience", "repeat_group", 3, True, rule=eq("hands_on", "yes"), children=[
        child("area", "Area or trade", True, trades + [("supervision", "Construction Supervision")]),
        child("source", "Experience source", True, [
            ("employment", "Paid employment"), ("school", "School/CTE"), ("training", "Trade training"),
            ("personal", "Personal/family"), ("volunteer", "Volunteer"), ("other", "Other")]),
        child("paid", "Paid or unpaid", True, [("paid", "Paid"), ("unpaid", "Unpaid")]),
        child("duration", "Experience duration", True, durations),
        child("skills", "Practiced skills", False, skills, "multiple_choice"),
        child("proficiency", "Self-reported proficiency", False, [
            ("beginner", "Beginner"), ("intermediate", "Intermediate"),
            ("advanced", "Advanced"), ("not_sure", "Not sure")]),
        child("description", "Description (optional)", False, kind="long_text", max_length=1000)])
    q("license_status", "Driver's license", "single_choice", 3, options=[
        ("valid", "Valid"), ("permit", "Learner permit"), ("none", "None"),
        ("expired_suspended", "Expired/suspended")])
    q("license_class", "License class", "single_choice", 3, options=[
        ("standard", "Standard"), ("cdl_a", "CDL A"), ("cdl_b", "CDL B"), ("other", "Other")],
      rule=eq("license_status", "valid"))
    q("certifications", "Do you have professional certifications?", "single_choice", 3, options=uncertain)
    q("certification_choices", "Certifications (self-reported)", "multiple_choice", 3, options=[
        ("osha10", "OSHA 10"), ("osha30", "OSHA 30"), ("cpr", "CPR/First Aid"),
        ("nccer", "NCCER"), ("aws", "Welding/AWS"), ("forklift", "Forklift"),
        ("equipment", "Heavy Equipment"), ("other", "Other")], rule=eq("certifications", "yes"))
    q("career_timeline", "When would you be interested in starting a work or training opportunity?", "single_choice", 4, True, [
        ("asap", "ASAP"), ("1_3_months", "1–3 months"), ("3_6_months", "3–6 months"),
        ("over_6_months", "More than 6 months"), ("after_education", "After graduation/completing education"),
        ("exploring", "Just exploring")])
    q("opportunity_types", "What types of opportunities would you be interested in?", "multiple_choice", 4, True, [
        ("full_time", "Full-time"), ("part_time", "Part-time"), ("apprenticeship", "Paid apprenticeship/OJT"),
        ("trade_school", "Trade school/technical training"), ("internship", "Internship/work-based learning"),
        ("exploration", "Career exploration/guidance"), ("other", "Other")], min_selections=1)
    q("opportunity_other", "Other opportunity", step=4, rule=contains("opportunity_types", "other"))
    q("career_priorities", "What is most important to you in your career? Select up to three", "multiple_choice", 4, options=[
        ("pay", "Pay"), ("advancement", "Advancement"), ("learning", "Learning"),
        ("security", "Security"), ("balance", "Work-life balance"), ("enjoyment", "Enjoyment"),
        ("difference", "Making a difference"), ("other", "Other")], max_selections=3)
    q("travel_willingness", "Would you be willing to travel for work or training?", "single_choice", 4, True, [
        ("yes", "Yes"), ("no", "No"), ("maybe", "Maybe")])
    q("travel_range", "How far would you be willing to travel?", "single_choice", 4, True, [
        ("local", "Local"), ("regional", "Regional"), ("statewide", "Statewide"), ("nationwide", "Nationwide")],
      within("travel_willingness", ["yes", "maybe"]))
    q("desired_pay", "Desired hourly pay range (optional)", "single_choice", 4, options=[
        ("under_15", "Under $15"), ("15_19", "$15–19"), ("20_24", "$20–24"), ("25_29", "$25–29"),
        ("30_34", "$30–34"), ("35_39", "$35–39"), ("40_plus", "$40+"), ("open", "Not sure/open")],
      rule={"op": "adult_non_student"})
    q("updates_preference", "Receive optional BTW updates about jobs, training and events?", "single_choice", 4, options=yesno)
    q("referral_source", "How did you hear about Built to Work? (optional)", "single_choice", 4, options=[
        ("social", "Social media"), ("family", "Friend or family"), ("school", "School or training"),
        ("employer", "Employer"), ("search", "Website or online search"), ("community", "Community event"),
        ("other", "Other")])
    return {"schema": SCHEMA, "questions": questions, "area_skills": {
        "welding": ["mig", "tig", "stick", "blueprints", "fabrication", "inspection", "tools"],
        "supervision": ["crew", "safety", "quality", "scheduling", "blueprints", "leadership"]},
        "qualification_mappings": []}


# Questions dropped from the recommended set: they repeated another question or were rarely useful.
RETIRED_FROM_RECOMMENDED = ("street_address", "school_activities", "training", "job_responsibilities",
                            "prior_employment", "certifications")


def recommended_contract():
    """The slimmer Nov 2026 sign-up: same saved answers, fewer overlapping questions, plainer wording."""
    contract = default_contract()
    by_key = {q["key"]: q for q in contract["questions"]}
    def relabel(key, label, **extra):
        by_key[key]["label"] = label
        by_key[key].update(extra)
    relabel("preferred_name", "Preferred name (if different)")
    relabel("current_situation", "What best describes you right now?")
    relabel("education_level", "Highest education so far")
    relabel("school_name", "School name")
    relabel("graduation", "When do you finish? (month/year)")
    # Only people working outside the trades need to name their industry.
    relabel("current_industry", "What industry do you work in?", rule={"field": "current_situation", "op": "in", "value": ["employed_other"]},
            options=[o for o in by_key["current_industry"]["options"] if o["value"] != "construction"])
    relabel("industry_other", "Which industry?")
    relabel("job_title", "Current job title")
    relabel("employment_duration", "How long have you worked there?")
    relabel("industry_interests", "Which trades interest you? Pick up to 3")
    relabel("interest_other", "Other trade")
    relabel("hands_on", "Have you done any hands-on trade work?")
    group = by_key["experiences"]
    group["label"] = "Tell us about it"
    group["children"] = [c for c in group["children"] if c["key"] not in ("paid", "description")]
    child_labels = {"area": "Trade", "source": "Where did you learn it?", "duration": "For how long?",
                    "skills": "Skills you used", "proficiency": "How comfortable are you with it?"}
    for c in group["children"]:
        c["label"] = child_labels.get(c["key"], c["label"])
    relabel("license_status", "Driver's license")
    relabel("license_class", "License type")
    # One list with "None yet" replaces the yes/no question plus a second list.
    cert = by_key["certification_choices"]
    cert.pop("rule", None)
    relabel("certification_choices", "Any certifications? Pick all that apply", required=False,
            options=cert["options"] + [{"value": "none", "label": "None yet"}], exclusive=["none"])
    relabel("career_timeline", "When could you start?")
    relabel("opportunity_types", "What are you looking for? Pick all that apply")
    relabel("opportunity_other", "Something else")
    relabel("career_priorities", "What matters most to you in a job? Pick up to 3",
            help_text="This helps us match you with jobs you'll actually want.",
            options=[{"value": v, "label": l} for v, l in (
                ("pay", "Good pay"), ("benefits", "Benefits"), ("advancement", "Room to grow"),
                ("learning", "Learning new skills"), ("security", "Steady, reliable work"),
                ("balance", "A schedule that fits my life"), ("close_to_home", "Close to home"),
                ("enjoyment", "Work I enjoy"), ("difference", "Making a difference"))])
    relabel("desired_pay", "What hourly pay are you hoping for?")
    relabel("travel_willingness", "Would you travel for work or training?")
    relabel("travel_range", "How far?")
    relabel("referral_source", "How did you hear about us?")
    relabel("updates_preference", "Want updates about jobs, training and events?")
    step4 = ["career_timeline", "opportunity_types", "opportunity_other", "career_priorities", "desired_pay",
             "travel_willingness", "travel_range", "referral_source", "updates_preference"]
    kept = [q for q in contract["questions"] if q["key"] not in RETIRED_FROM_RECOMMENDED and q["step"] != 4]
    contract["questions"] = kept + [by_key[key] for key in step4]
    return contract
