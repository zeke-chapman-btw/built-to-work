"""Read-only display groupings over existing, permission-scoped participant context."""


def intake_editor_groups(form, mode):
    if mode == 'question':
        groups = [
            ('Question', ('label', 'help_text', 'type', 'step', 'required', 'destination'), False),
            ('Choices', ('options',), True),
            ('Display conditions and selection limits', ('condition_mode', 'condition_field', 'condition_values', 'max_selections', 'min_selections', 'exclusive'), True),
        ]
    elif mode == 'skills':
        groups = [(field.label, (field.name,), True) for field in form.visible_fields()]
    else:
        groups = [(title, tuple(name for name in form.fields if name.startswith(prefix)), True)
                  for title, prefix in [('Practiced skills', 'skills_'), ('Driver licenses', 'license_class_'), ('Certifications', 'certification_choices_')]]
    result = []
    for title, names, optional in groups:
        fields = [form[name] for name in names if name in form.fields]
        if mode == 'mappings':
            for field in fields:
                field.label = field.label.partition(' — ')[2] or field.label
        if fields:
            result.append(dict(title=title, fields=fields, optional=optional,
                open=any(field.errors for field in fields),
                selected=sum(len(field.value() or []) for field in fields) if mode == 'skills' else None))
    return result


def profile_preferences(context):
    """Group current, already scoped facts for display; never replay intake."""
    career = context.get("career")
    definitions = [
        ("Employment interest", career.get_employment_interest_display() if career else None),
        ("Work arrangement", career.get_preferred_work_type_display() if career else None),
        ("Preferred locations", career.preferred_locations if career else None),
        ("Availability", career.work_availability if career else None),
        ("Travel", context.get("current_travel")),
    ]
    known, missing = [], []
    for label, value in definitions:
        if value not in (None, "", "Unknown", "unknown", "Not recorded"):
            known.append(dict(label=label, value=value, source="Current profile"))
        else:
            missing.append(label)
    from .intake_defaults import default_contract
    definitions = {q["destination"]: q for q in default_contract()["questions"]}
    # Use the retained destination; customized question wording stays in Details.
    rows = {row.get("destination"): row for row in context.get("intake_profile_rows", [])}
    def facts(pairs):
        output = []
        for key, label in pairs:
            row = rows.get(key)
            if row and row["value"] != "Not answered":
                output.append(dict(row, label=label))
        return output
    life_stage = getattr(context.get("participant"), "life_stage", "")
    life_labels = {o["value"]: o["label"] for o in definitions["current_situation"]["options"]}
    return dict(profile_preference_rows=known, profile_missing_preferences=missing,
                profile_life_stage=life_labels.get(life_stage, life_stage),
                profile_intake_who=facts([("education_level", "Education"), ("school_name", "School or training"), ("training", "Trade training")]),
                profile_intake_preferences=facts([("career_timeline", "Opportunity timing"), ("opportunity_types", "Opportunities"), ("career_priorities", "Career priorities"), ("travel_range", "Travel range")]))


def qualification_display_groups(records):
    """Group only supplied, authorized records; never infer or persist a kind.

    A page remains a page. Generic/legacy and contradictory classifications stay
    explicit rather than being promoted to a skill or credential by their label.
    """
    labels = ("Skills & Trades", "Education & Training", "Licenses & Certifications",
              "Other recorded qualifications")
    groups = {label: [] for label in labels}
    reported = {"skill": labels[0], "training": labels[1], "credential": labels[2]}
    catalog = {"skill": labels[0], "license": labels[2], "certification": labels[2]}
    for record in records:
        recorded_kind = getattr(record, "reported_kind", "") or ""
        entry = getattr(record, "catalog_entry", None)
        catalog_kind = getattr(entry, "kind", "") or ""
        first, second = reported.get(recorded_kind), catalog.get(catalog_kind)
        conflict = bool(first and second and first != second)
        unknown = bool(recorded_kind and not first) or bool(catalog_kind and catalog_kind not in catalog)
        label = labels[3] if conflict or unknown else (first or second or labels[3])
        note = "Recorded kinds conflict; classification unchanged." if conflict else (
            "No unambiguous recorded category; classification unchanged." if label == labels[3] else "")
        groups[label].append(dict(record=record, classification_note=note))
    return [dict(title=label, items=items) for label, items in groups.items() if items]


def qualification_preview_groups(records, limit=5):
    """Count the complete authorized collection; retain only bounded previews.

    Stream using the existing evidence-prefetch pattern. A category is never
    called empty merely because its records fall beyond a pagination boundary.
    Classification is the same read-only contract used by the detailed pages.
    """
    definitions = [("skills", "Skills & Trades"), ("education", "Education & Training"),
                   ("credentials", "Licenses & Certifications"),
                   ("other", "Other recorded qualifications")]
    groups = {title: dict(key=key, title=title, items=[], total=0)
              for key, title in definitions}
    for record in records.iterator(chunk_size=250):
        classified = qualification_display_groups([record])[0]
        group = groups[classified["title"]]
        group["total"] += 1
        if len(group["items"]) < limit:
            group["items"].extend(classified["items"])
    return list(groups.values())
