"""Small presentation-only components; callers retain domain authorization."""
from django import template, forms
register = template.Library()

@register.inclusion_tag("internal/ui/panel_heading.html")
def ui_panel_heading(title, subtitle="", action_url="", action_label=""):
    return dict(title=title, subtitle=subtitle, action_url=action_url, action_label=action_label)

@register.inclusion_tag("internal/ui/empty_state.html")
def ui_empty(title, detail="", action_url="", action_label=""):
    return dict(title=title, detail=detail, action_url=action_url, action_label=action_label)

@register.inclusion_tag("internal/ui/badge.html")
def ui_badge(label, tone="neutral"):
    if tone not in ("neutral", "info", "success", "warning", "danger"):
        tone = "neutral"
    return dict(label=label, tone=tone)

@register.inclusion_tag("internal/ui/metric.html")
def ui_metric(label, value, detail=""):
    return dict(label=label, value=value, detail=detail)

@register.inclusion_tag("internal/ui/tabs.html")
def ui_tabs(items, selected="", label="Sections", variant="primary"):
    return dict(items=items, selected=selected, label=label, variant=variant if variant in ("primary", "status", "secondary") else "primary")


ICON_PATHS = {
    "overview": [
        "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18",
        "M12 11v6",
        "M12 7h.01"
    ],
    "staff": [
        "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2",
        "M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8",
        "M17 4a4 4 0 0 1 0 8",
        "M22 21v-2a4 4 0 0 0-3-3.87"
    ],
    "operations": [
        "M14 6l4 4",
        "M21 3l-5 1-4 4 4 4 4-4 1-5",
        "M12 12l-9 9-2-2 9-9"
    ],
    "results": [
        "M3 3v18h18",
        "M7 16v-4",
        "M12 16V8",
        "M17 16V5"
    ],
    "registrations": [
        "M4 4h16v5a3 3 0 0 0 0 6v5H4v-5a3 3 0 0 0 0-6V4",
        "M14 4v3m0 3v4m0 3v3"
    ],
    "expenses": [
        "M5 3h14v18l-3-2-4 2-4-2-3 2V3",
        "M8 7h8m-8 4h8m-8 4h5"
    ],
    "report": [
        "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6",
        "M14 2v6h6",
        "M8 13h8m-8 4h8"
    ],
    "tasks": [
        "M9 5h10v16H3V5h3",
        "M9 3h6v4H9V3",
        "M7 14l3 3 6-6"
    ],
    "notes": [
        "M4 3h16v14l-4 4H4V3",
        "M16 21v-4h4",
        "M8 7h8m-8 4h8"
    ],
    "documents": [
        "M3 5h6l2 2h10v13H3V5"
    ],
    "venue": [
        "M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0",
        "M12 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6"
    ],
    "calendar": [
        "M4 5h16v16H4V5",
        "M16 3v4M8 3v4M4 11h16"
    ],
    "clock": [
        "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18",
        "M12 7v5l3 2"
    ],
    "code": [
        "M9 3L7 21M17 3l-2 18M3 9h18M2 15h18"
    ],
    "mail": [
        "M3 5h18v14H3V5",
        "M3 5l9 7 9-7"
    ],
    "phone": [
        "M22 16v3a2 2 0 0 1-2 2A19 19 0 0 1 3 4a2 2 0 0 1 2-2h3l2 5-2 2a15 15 0 0 0 7 7l2-2 5 2"
    ],
    "housing": [
        "M3 11l9-8 9 8",
        "M5 10v11h14V10M9 21v-7h6v7"
    ],
    "edit": [
        "M16 3l5 5-12 12-6 1 1-6L16 3",
        "M14 5l5 5"
    ]
}

# Shared-shell icons reuse the existing renderer; no additional icon dependency.
ICON_PATHS.update({
    "home": ["M3 10l9-7 9 7", "M5 9v12h14V9M9 21v-7h6v7"],
    "people": ICON_PATHS["staff"],
    "customers": ["M3 21h18M5 21V5h14v16M9 9h1m4 0h1M9 13h1m4 0h1M10 21v-4h4v4"],
    "campaigns": ["M3 10v4h4l10 5V5L7 10H3", "M7 14l2 7h3l-2-6M21 9v6"],
    "approvals": ["M12 3l8 4v6c0 5-8 9-8 9s-8-4-8-9V7l8-4", "M8 12l3 3 5-6"],
    "reports": ICON_PATHS["results"],
    "settings": ["M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3", "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6"],
    "logout": ["M9 4H4v16h5", "M9 12h12m-4-4 4 4-4 4"],
})

# Event navigation aliases leave existing receipts/other consumers unchanged.
ICON_PATHS.update({
    "event-dollar": ["M12 2v20", "M17 6H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"],
    "event-more": ["M12 5v14", "M5 12h14"],
})

@register.inclusion_tag("internal/ui/icon.html")
def ui_icon(name):
    return {"icon_paths": ICON_PATHS.get(name, ICON_PATHS["overview"])}


@register.inclusion_tag("internal/ui/field.html")
def ui_field(field, field_id=""):
    """Render a bound field without changing its POST name or validation."""
    field_id = field_id or field.id_for_label or field.auto_id or field.html_name
    is_group = isinstance(field.field.widget, (forms.CheckboxSelectMultiple, forms.RadioSelect))
    described_by = field.field.widget.attrs.get("aria-describedby", "").split()
    if field.help_text:
        described_by.append(f"{field_id}_help")
    if field.errors:
        described_by.append(f"{field_id}_errors")
    attrs = {"id": field_id}
    if is_group:
        attrs.update({"aria-labelledby": f"{field_id}_label", "role": "group"})
    if described_by:
        attrs["aria-describedby"] = " ".join(dict.fromkeys(described_by))
    if field.errors:
        attrs["aria-invalid"] = "true"
    return {"field": field, "field_id": field_id, "is_group": is_group, "widget": field.as_widget(attrs=attrs)}
