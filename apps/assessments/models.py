import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify


class AssessmentCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display_order", "name")
        verbose_name_plural = "assessment categories"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class QuestionSet(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        RETIRED = "retired", "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(AssessmentCategory, on_delete=models.PROTECT, related_name="question_sets")
    version = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=160)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("category__name", "-version")
        constraints = [models.UniqueConstraint(fields=("category", "version"), name="assessment_category_version_uniq")]

    def __str__(self):
        return f"{self.category.name} — {self.name} v{self.version}"

    def difficulty_counts(self):
        return {difficulty: self.items.filter(is_active=True, question__is_active=True, question__difficulty=difficulty).count()
                for difficulty, _ in Question.Difficulty.choices}

    def readiness(self):
        counts = self.difficulty_counts()
        missing = {key: max(0, 5 - value) for key, value in counts.items()}
        return {"ready": self.status == self.Status.PUBLISHED and not any(missing.values()),
                "counts": counts, "missing": missing}

    def publish(self):
        report = self.readiness()
        if any(count < 5 for count in report["counts"].values()):
            raise ValidationError("A question set needs at least 5 active questions at each difficulty before it can be published.")
        if self.status != self.Status.DRAFT:
            raise ValidationError("Only a Draft question set can be published.")
        self.status = self.Status.PUBLISHED
        self.published_at = timezone.now()
        self.save(update_fields=("status", "published_at"))

    def retire(self):
        if self.status != self.Status.PUBLISHED:
            raise ValidationError("Only a Published question set can be retired.")
        self.status = self.Status.RETIRED
        self.retired_at = timezone.now()
        self.save(update_fields=("status", "retired_at"))


class Question(models.Model):
    class Difficulty(models.TextChoices):
        EASY = "easy", "Easy"
        MEDIUM = "medium", "Medium"
        HARD = "hard", "Hard"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(AssessmentCategory, on_delete=models.PROTECT, related_name="questions")
    text = models.TextField()
    image = models.FileField(upload_to="assessment_questions/", blank=True, validators=[FileExtensionValidator(("png", "jpg", "jpeg", "webp"))])
    difficulty = models.CharField(max_length=8, choices=Difficulty.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("category__name", "difficulty", "created_at")

    def clean(self):
        super().clean()
        # The form saves the related choices after saving a new question.
        # Validate the cross-model invariant once the question has a primary key.
        if self._state.adding:
            return
        choices = self.choices.all()
        if choices.count() != 4 or choices.filter(is_correct=True).count() != 1:
            raise ValidationError("A question must have exactly four choices and one correct answer.")


class QuestionChoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("display_order", "id")

    def clean(self):
        if self.question_id and self.question.set_memberships.filter(question_set__status__in=(QuestionSet.Status.PUBLISHED, QuestionSet.Status.RETIRED)).exists():
            raise ValidationError("Choices in a Published or Retired set are locked.")
        if not self.text.strip():
            raise ValidationError("Enter text for all four answer choices.")


class QuestionSetItem(models.Model):
    question_set = models.ForeignKey(QuestionSet, on_delete=models.PROTECT, related_name="items")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="set_memberships")
    is_active = models.BooleanField(default=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("question_set", "question"), name="assessment_set_question_uniq")]

    def clean(self):
        if self.question_set_id and self.question_set.status != QuestionSet.Status.DRAFT:
            raise ValidationError("Only Draft question sets can be edited.")
        if self.question_id and self.question_set_id and self.question.category_id != self.question_set.category_id:
            raise ValidationError("The question and question set must use the same category.")


class EventAssessmentCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="assessment_categories")
    category = models.ForeignKey(AssessmentCategory, on_delete=models.PROTECT, related_name="event_configurations")
    question_set = models.ForeignKey(QuestionSet, on_delete=models.PROTECT, related_name="event_configurations")
    enabled = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    display_name = models.CharField(max_length=120, blank=True)
    description = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ("display_order", "category__name")
        constraints = [models.UniqueConstraint(fields=("event", "category"), name="event_assessment_category_uniq")]

    def clean(self):
        if self.category_id and self.question_set_id:
            if self.question_set.category_id != self.category_id:
                raise ValidationError({"question_set": "Choose a question set for this category."})
            if self.question_set.status != QuestionSet.Status.PUBLISHED:
                raise ValidationError({"question_set": "Only Published question sets may be assigned to an Event."})
            report = self.question_set.readiness()
            if not all(report["counts"].get(level, 0) >= 5 for level in ("easy", "medium", "hard")):
                raise ValidationError({"question_set": "This question set is not ready: it needs at least 5 active questions at each difficulty."})

    @property
    def participant_name(self):
        return self.display_name or self.category.name

    def __str__(self):
        return f"{self.event} — {self.participant_name}"


class QuizAttempt(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "In progress"
        COMPLETE = "complete", "Complete"
        VOID = "void", "Voided"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experience_session = models.ForeignKey("stations.ExperienceSession", on_delete=models.PROTECT, related_name="quiz_attempts")
    participant = models.ForeignKey("participants.Participant", on_delete=models.PROTECT, related_name="quiz_attempts", null=True, blank=True)
    is_official = models.BooleanField(default=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    selected_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="voided_quiz_attempts")
    replacement_for = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="replacements")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("experience_session",), condition=Q(is_official=True, voided_at__isnull=True), name="one_current_official_quiz_per_session")]
        ordering = ("-created_at",)

    @property
    def total_correct(self):
        return sum(section.correct_count for section in self.sections.all())

    @property
    def total_answered(self):
        return sum(section.answered_count for section in self.sections.all())

    @property
    def max_score(self):
        return sum(len(section.questions_snapshot or []) for section in self.sections.all())


class AssessmentSection(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        COMPLETE = "complete", "Complete"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name="sections")
    category = models.ForeignKey(AssessmentCategory, on_delete=models.PROTECT, related_name="assessment_sections")
    question_set = models.ForeignKey(QuestionSet, on_delete=models.PROTECT, related_name="assessment_sections")
    position = models.PositiveSmallIntegerField()
    category_name_snapshot = models.CharField(max_length=120)
    questions_snapshot = models.JSONField(default=list)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    duration_seconds = models.PositiveSmallIntegerField(default=45)
    started_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("position",)
        constraints = [models.UniqueConstraint(fields=("attempt", "position"), name="assessment_attempt_section_order_uniq")]

    @property
    def answered_count(self):
        return self.responses.count()

    @property
    def correct_count(self):
        return self.responses.filter(is_correct=True).count()

    def remaining_seconds(self, now=None):
        if self.status != self.Status.ACTIVE or not self.deadline_at:
            return 0
        return max(0, int(((self.deadline_at - (now or timezone.now())).total_seconds()) + 0.999))


class AssessmentResponse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    section = models.ForeignKey(AssessmentSection, on_delete=models.CASCADE, related_name="responses")
    question_position = models.PositiveSmallIntegerField()
    selected_choice_key = models.CharField(max_length=36)
    is_correct = models.BooleanField(default=False)
    answered_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("question_position",)
        constraints = [models.UniqueConstraint(fields=("section", "question_position"), name="one_answer_per_assessment_question")]
