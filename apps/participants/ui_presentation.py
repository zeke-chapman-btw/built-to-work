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
