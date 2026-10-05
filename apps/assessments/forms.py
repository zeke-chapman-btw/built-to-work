from django import forms
from django.core.exceptions import ValidationError
from .models import AssessmentCategory, EventAssessmentCategory, Question, QuestionSet


class CategoryForm(forms.ModelForm):
    class Meta:
        model = AssessmentCategory
        fields = ("name", "description", "is_active", "display_order")


class QuestionSetForm(forms.ModelForm):
    class Meta:
        model = QuestionSet
        fields = ("category", "name")

    def save(self, commit=True):
        obj = super().save(commit=False)
        if obj._state.adding:
            obj.version = (QuestionSet.objects.filter(category=obj.category).order_by("-version").values_list("version", flat=True).first() or 0) + 1
        if commit:
            obj.save()
        return obj


class QuestionForm(forms.ModelForm):
    choice_a = forms.CharField(label="Answer A", max_length=500)
    choice_b = forms.CharField(label="Answer B", max_length=500)
    choice_c = forms.CharField(label="Answer C", max_length=500)
    choice_d = forms.CharField(label="Answer D", max_length=500)
    correct_choice = forms.ChoiceField(label="Correct answer", choices=(("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")), widget=forms.RadioSelect)

    class Meta:
        model = Question
        fields = ("text", "image", "difficulty", "is_active")
        widgets = {"text": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, category=None, **kwargs):
        self.category = category
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            choices = list(self.instance.choices.all())
            for name, choice in zip(("choice_a", "choice_b", "choice_c", "choice_d"), choices):
                self.fields[name].initial = choice.text
                if choice.is_correct:
                    self.fields["correct_choice"].initial = name[-1]

    def clean(self):
        cleaned = super().clean()
        if not self.category and not self.instance.pk:
            raise ValidationError("Choose a category for the question.")
        for field in ("choice_a", "choice_b", "choice_c", "choice_d"):
            if not (cleaned.get(field) or "").strip():
                self.add_error(field, "Enter text for all four answers.")
        return cleaned

    def save(self, commit=True):
        question = super().save(commit=False)
        if self.category:
            question.category = self.category
        if commit:
            question.save()
            # This form is only offered for questions in Draft sets. Preserve choice order and one key.
            if question.choices.exists():
                question.choices.all().delete()
            for index, letter in enumerate(("a", "b", "c", "d"), start=1):
                question.choices.create(text=self.cleaned_data[f"choice_{letter}"].strip(),
                    is_correct=self.cleaned_data["correct_choice"] == letter, display_order=index)
        return question


class EventCategoryForm(forms.ModelForm):
    class Meta:
        model = EventAssessmentCategory
        fields = ("category", "question_set", "enabled", "display_order", "display_name", "description")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = AssessmentCategory.objects.filter(is_active=True)
        self.fields["question_set"].queryset = QuestionSet.objects.filter(status=QuestionSet.Status.PUBLISHED).select_related("category")

    def clean(self):
        data = super().clean()
        category, qset = data.get("category"), data.get("question_set")
        if category and qset and category.pk != qset.category_id:
            self.add_error("question_set", "Choose a Published question set for the selected category.")
        return data
