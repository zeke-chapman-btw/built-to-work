"""Reasons are optional across staff forms (owner decision, Nov 2026).

The reason box is hidden. If nobody types one, a plain default is saved so the
history still shows who changed what and when. Rejecting a participant
verification is the one place that still asks, because the participant sees it.
"""
from django import forms

DEFAULT_REASON = "No reason given"
REASON_FIELD_NAMES = ("reason", "audit_reason", "override_reason")


class OptionalReasonMixin:
    reason_default = DEFAULT_REASON

    def _apply_optional_reason(self):
        for name in REASON_FIELD_NAMES:
            field = self.fields.get(name)
            if field is None or getattr(field, "_optional_reason", False):
                continue
            field.required = False
            field.widget = forms.HiddenInput()
            if isinstance(field, forms.CharField):
                field.empty_value = self.reason_default
            field._optional_reason = True

    def __getitem__(self, name):
        self._apply_optional_reason()
        return super().__getitem__(name)

    def __iter__(self):
        self._apply_optional_reason()
        return super().__iter__()

    def full_clean(self):
        self._apply_optional_reason()
        return super().full_clean()
