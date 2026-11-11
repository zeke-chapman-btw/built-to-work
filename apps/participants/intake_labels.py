"""Plain names for where an intake answer is saved on the participant profile."""

GROUPS = (
    ("Contact", (("first_name", "First name"), ("last_name", "Last name"), ("preferred_name", "Preferred name"),
                 ("email", "Email"), ("phone", "Phone"), ("street_address", "Street address"), ("city", "City"),
                 ("state", "State"), ("postal_code", "ZIP code"), ("dob", "Date of birth"), ("consent", "Consent"))),
    ("Education", (("education_level", "Highest education"), ("school_name", "School name"),
                   ("graduation", "Graduation"), ("training", "Training"), ("school_activities", "School activities"))),
    ("Work", (("current_situation", "Current situation"), ("current_industry", "Current industry"),
              ("job_title", "Job title"), ("job_responsibilities", "Job duties"),
              ("employment_duration", "Time at current job"), ("prior_employment", "Past jobs"),
              ("hands_on", "Hands-on experience"), ("industry_other", "Other industry"),
              ("experiences", "Work experience list"))),
    ("Licenses & certifications", (("license_status", "Driver's license"), ("license_class", "License class"),
                                   ("certifications", "Has certifications"), ("certification_choices", "Certifications"))),
    ("Career goals", (("industry_interests", "Trade interests"), ("interest_other", "Other trade interest"),
                      ("opportunity_types", "Looking for"), ("opportunity_other", "Other opportunity"),
                      ("career_priorities", "What matters most"), ("career_timeline", "Ready to start"),
                      ("travel_willingness", "Willing to travel"), ("travel_range", "Travel distance"),
                      ("desired_pay", "Desired pay"))),
    ("Other", (("updates_preference", "How they want updates"), ("referral_source", "How they heard about us"))),
)
CHILD_GROUP = ("Work experience entry", (("area", "Trade"), ("source", "Where they learned it"), ("paid", "Paid or unpaid"),
                                         ("duration", "How long"), ("proficiency", "Skill level"), ("skills", "Skills"),
                                         ("description", "Description")))
CHILD_KEYS = {key for key, _ in CHILD_GROUP[1]}
SUBMISSION_ONLY = ("submission_only", "Don't save to profile (keep with submission)")

LABELS = {key: label for _, items in (*GROUPS, CHILD_GROUP) for key, label in items}
SECTIONS = {key: title for title, items in (*GROUPS, CHILD_GROUP) for key, _ in items}


def label(key):
    return LABELS.get(key, str(key or "").replace("_", " ").capitalize())


def destination_text(key):
    """Short display such as 'Contact · Phone'; empty when not saved to the profile."""
    if not key or key == "submission_only":
        return ""
    return f"{SECTIONS[key]} · {LABELS[key]}" if key in SECTIONS else label(key)


def destination_choices(allowed, *, child=False, current=None):
    """Every allowed code stays selectable; entry fields are listed first for experience follow-ups."""
    groups = [CHILD_GROUP, *GROUPS] if child else [*GROUPS, CHILD_GROUP]
    seen, result = set(), [SUBMISSION_ONLY]
    for title, items in groups:
        options = [(k, l) for k, l in items if k in allowed]
        seen.update(k for k, _ in options)
        if options:
            result.append((title, options))
    extra = [(k, label(k)) for k in sorted(allowed) if k not in seen]
    if extra:
        result.append(("More", extra))
    return result
