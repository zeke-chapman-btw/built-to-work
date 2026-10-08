from django import forms
from django.utils import timezone
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

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if not image or image is False or not hasattr(image, "size"):
            return image
        if image.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Use an image smaller than 5 MB.")
        # Validate a real image header without introducing a runtime font/image dependency.
        # Serving is still restricted to these three browser-safe formats.
        try:
            header = image.read(64)
            image.seek(-12, 2)
            trailer = image.read(12)
            image.seek(0)
        except (OSError, ValueError):
            raise forms.ValidationError("Upload a valid PNG, JPEG, or WebP image.")
        png = (header.startswith(bytes.fromhex("89504e470d0a1a0a"))
               and header[8:16] == bytes.fromhex("0000000d49484452")
               and int.from_bytes(header[16:20], "big") > 0
               and int.from_bytes(header[20:24], "big") > 0
               and trailer == bytes.fromhex("0000000049454e44ae426082"))
        jpeg = (header.startswith(bytes.fromhex("ffd8ff")) and trailer.endswith(bytes.fromhex("ffd9"))
                and image.size >= 100)
        webp = (header.startswith(b"RIFF") and header[8:12] == b"WEBP"
                and header[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
                and int.from_bytes(header[4:8], "little") + 8 == image.size)
        if not (png or jpeg or webp):
            raise forms.ValidationError("Upload a valid PNG, JPEG, or WebP image.")
        return image

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


class PublishQuestionSetForm(forms.Form):
    effective_at = forms.DateTimeField(required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=("%Y-%m-%dT%H:%M",))


class EventCategoryForm(forms.ModelForm):
    class Meta:
        model = EventAssessmentCategory
        fields = ("category", "pool_mode", "question_set", "curated_questions", "enabled", "display_order", "display_name", "description")

    def __init__(self, *args, event=None, **kwargs):
        self.event = event
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = AssessmentCategory.objects.filter(is_active=True)
        self.fields["question_set"].required = False
        self.fields["question_set"].queryset = QuestionSet.objects.filter(status=QuestionSet.Status.PUBLISHED).select_related("category")
        self.fields["curated_questions"].required = False
        self.fields["curated_questions"].queryset = Question.objects.filter(is_active=True).order_by("difficulty", "created_at")
        self.fields["curated_questions"].help_text = "Optional: select at least 15 questions from the chosen set, or leave blank to use its full pool."

    def clean(self):
        data = super().clean()
        category = data.get("category")
        mode = data.get("pool_mode")
        qset = data.get("question_set")
        selected = data.get("curated_questions")
        if category and mode == EventAssessmentCategory.PoolMode.DEFAULT:
            from .services import latest_effective_set
            qset = latest_effective_set(category, max(self.event.start_at, timezone.now()) if self.event else None)
            if qset is None:
                self.add_error("category", "No Published question set is effective by this Event's start.")
            else:
                data["question_set"] = qset
                data["curated_questions"] = Question.objects.none()
        elif mode == EventAssessmentCategory.PoolMode.CURATED:
            if not qset:
                self.add_error("question_set", "Choose a Published question set for the curated pool.")
            elif category and category.pk != qset.category_id:
                self.add_error("question_set", "Choose a set for the selected category.")
            if selected and selected.exists():
                if selected.count() < 15:
                    self.add_error("curated_questions", "Select at least 15 questions, or leave this blank for the full set.")
                elif qset and selected.exclude(set_memberships__question_set=qset, set_memberships__is_active=True).exists():
                    self.add_error("curated_questions", "Every curated question must belong to the selected set.")
        return data
