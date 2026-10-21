from django import forms
from apps.assessments.models import QuestionSet
from apps.games.models import GameDefinition
from apps.stations.models import Station
from .template_models import KINDS

class TemplateDraftForm(forms.Form):
    name = forms.CharField(max_length=160)
    description = forms.CharField(max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    question_sets = forms.ModelMultipleChoiceField(queryset=QuestionSet.objects.none(), required=False, help_text="Published sets only; one version per category. Applying quizzes retains the existing superuser-only editing restriction.")
    stations = forms.ModelMultipleChoiceField(queryset=Station.objects.none(), required=False, help_text="Assignments only. Applying a template never activates a physical station's event context.")
    game = forms.ModelChoiceField(queryset=GameDefinition.objects.none(), required=False, help_text="Existing Duck Hunt implementation, with its existing default rules. Detailed settings remain in the game editor.")
    registration_group_mode = forms.ChoiceField(choices=(("", "Leave registration unchanged"), ("disabled", "Disabled"), ("optional", "Optional"), ("required", "Required")), required=False)
    registration_group_label = forms.CharField(max_length=80, initial="Group", required=False)
    registration_capacity = forms.IntegerField(min_value=1, required=False)
    not_applicable = forms.MultipleChoiceField(choices=KINDS, required=False, widget=forms.CheckboxSelectMultiple, help_text="Explicitly exclude a section; this never disables an existing event resource.")
    version_token = forms.CharField(widget=forms.HiddenInput, required=False)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["question_sets"].queryset = QuestionSet.objects.filter(status="published", category__is_active=True).select_related("category")
        self.fields["stations"].queryset = Station.objects.filter(is_active=True)
        self.fields["game"].queryset = GameDefinition.objects.filter(active=True, implementation_key="duck_hunt")
    def clean(self):
        data = super().clean()
        sets = list(data.get("question_sets", []))
        if len({x.category_id for x in sets}) != len(sets): self.add_error("question_sets", "Choose only one version per assessment category.")
        if sets and len(sets) < 2: self.add_error("question_sets", "Existing kiosk delivery requires at least two assessment categories.")
        selected = {"assessment": bool(sets), "station": bool(data.get("stations")), "game": bool(data.get("game")), "registration": bool(data.get("registration_group_mode"))}
        if any(selected.get(k) for k in data.get("not_applicable", [])): self.add_error("not_applicable", "Remove resources from sections marked Not Applicable.")
        if data.get("registration_group_mode") and not data.get("registration_group_label"): self.add_error("registration_group_label", "Enter a group label.")
        if not data.get("registration_group_mode") and data.get("registration_capacity") is not None: self.add_error("registration_capacity", "Select registration settings to include a capacity.")
        return data

class ApplyTemplateForm(forms.Form):
    confirmation = forms.BooleanField(label="I reviewed this exact revision and the proposed event changes.")
    token = forms.CharField(widget=forms.HiddenInput)

class ConfirmTemplateActionForm(forms.Form):
    confirm = forms.BooleanField(label="I confirm this action.")
    token = forms.CharField(widget=forms.HiddenInput)
