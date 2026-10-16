from django import forms
from .dashboard import MAX_QUICK_ACTIONS


class QuickActionsForm(forms.Form):
    actions = forms.MultipleChoiceField(required=False)

    def __init__(self, *args, available, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["actions"].choices = [("", "No action")] + [(a["id"], a["label"]) for a in available]

    def clean_actions(self):
        values = self.cleaned_data["actions"]
        if len(values) > MAX_QUICK_ACTIONS:
            raise forms.ValidationError("Select no more than nine actions.")
        selected = [value for value in values if value]
        if len(set(selected)) != len(selected):
            raise forms.ValidationError("Select each action only once.")
        return selected
