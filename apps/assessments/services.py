import random
from datetime import timedelta
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from apps.core.models import AuditLog
from apps.stations.models import ExperienceActivity
from apps.stations.services import complete_activity
from .models import (AssessmentCategory, AssessmentResponse, AssessmentSection,
                     EventAssessmentCategory, Question, QuestionSet, QuizAttempt)


BLUEPRINT = ((Question.Difficulty.EASY, 5), (Question.Difficulty.MEDIUM, 5), (Question.Difficulty.HARD, 5))
SECTION_SECONDS = 45


def eligible_event_categories(event):
    result = []
    configs = EventAssessmentCategory.objects.filter(event=event, enabled=True, category__is_active=True,
        question_set__status=QuestionSet.Status.PUBLISHED).select_related("category", "question_set").order_by("display_order", "category__name")
    for config in configs:
        counts = config.question_set.difficulty_counts()
        if all(counts.get(level, 0) >= count for level, count in BLUEPRINT):
            result.append(config)
    return result


def _snapshot_questions(question_set):
    selected = []
    for difficulty, required in BLUEPRINT:
        eligible = list(question_set.items.filter(is_active=True, question__is_active=True,
            question__difficulty=difficulty, question__category=question_set.category).select_related("question").prefetch_related("question__choices"))
        eligible = [item.question for item in eligible if item.question.choices.count() == 4 and item.question.choices.filter(is_correct=True).count() == 1]
        if len(eligible) < required:
            raise ValidationError("This assessment category is not ready. Please ask staff for help.")
        selected.extend(random.sample(eligible, required))
    random.shuffle(selected)
    snapshot = []
    for position, question in enumerate(selected, start=1):
        choices = list(question.choices.all())
        random.shuffle(choices)
        try:
            image_url = question.image.url if question.image else ""
        except ValueError:
            image_url = ""
        snapshot.append({
            "position": position,
            "source_question_id": str(question.pk),
            "text": question.text,
            "image_url": image_url,
            "difficulty": question.difficulty,
            "choices": [{"key": str(choice.pk), "text": choice.text, "correct": choice.is_correct} for choice in choices],
        })
    return snapshot

def _start_section(section, now):
    section.status = AssessmentSection.Status.ACTIVE
    section.started_at = now
    section.deadline_at = now + timedelta(seconds=section.duration_seconds)
    section.save(update_fields=("status", "started_at", "deadline_at"))
    


@transaction.atomic
def create_attempt(session, selected_config_ids, *, is_official=True, actor=None, replacement_for=None, start_first_section=True):
    session = type(session).objects.select_for_update().select_related("registration__event", "registration__participant").get(pk=session.pk)
    selected_ids = [str(value) for value in selected_config_ids]
    if len(selected_ids) != 2 or selected_ids[0] == selected_ids[1]:
        raise ValidationError("Choose exactly two different assessment categories.")
    existing = QuizAttempt.objects.filter(experience_session=session, is_official=is_official, voided_at__isnull=True).first()
    if existing:
        return existing
    event = session.event
    configs_by_id = {str(config.pk): config for config in eligible_event_categories(event)}
    if any(value not in configs_by_id for value in selected_ids):
        raise ValidationError("One or more selected areas are no longer available. Please ask staff for help.")
    configs = [configs_by_id[value] for value in selected_ids]
    attempt = QuizAttempt.objects.create(experience_session=session, participant=session.registration.participant if session.registration_id else None,
        is_official=is_official, replacement_for=replacement_for)
    now = timezone.now()
    for position, config in enumerate(configs, start=1):
        section = AssessmentSection.objects.create(attempt=attempt, category=config.category,
            question_set=config.question_set, position=position,
            category_name_snapshot=config.participant_name,
            questions_snapshot=_snapshot_questions(config.question_set), duration_seconds=SECTION_SECONDS)
        if position == 1 and start_first_section:
            _start_section(section, now)
    if is_official:
        _audit(actor, "assessment.started", attempt, {"session_id": str(session.pk), "section_count": 2})
    return attempt


def _audit(actor, action, instance, data, reason=""):
    AuditLog.objects.create(actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action, content_type=ContentType.objects.get_for_model(instance), object_id=str(instance.pk),
        new_data=data, reason=reason)


def _finalize_section(section, now=None):
    if section.status != AssessmentSection.Status.ACTIVE:
        return section
    now = now or timezone.now()
    if section.answered_count < len(section.questions_snapshot) and section.deadline_at and now < section.deadline_at:
        return section
    section.status = AssessmentSection.Status.COMPLETE
    section.completed_at = now
    section.save(update_fields=("status", "completed_at"))
    next_section = section.attempt.sections.filter(position__gt=section.position).order_by("position").first()
    if next_section:
        # The next section remains pending until its visible countdown ends.
        pass
    else:
        attempt = section.attempt
        attempt.status = QuizAttempt.Status.COMPLETE
        attempt.completed_at = now
        attempt.save(update_fields=("status", "completed_at"))
        if attempt.is_official:
            # Use the Build 3 state machine to finish the existing Kiosk activity.
            
            complete_activity(session=attempt.experience_session, activity_name=ExperienceActivity.Activity.KIOSK, actor=None)
            _audit(None, "assessment.completed", attempt, {"total_answered": attempt.total_answered, "total_correct": attempt.total_correct})
    return section


@transaction.atomic
def current_section(attempt, now=None):
    now = now or timezone.now()
    section = attempt.sections.select_for_update().filter(status=AssessmentSection.Status.ACTIVE).first()
    if section and section.deadline_at and now >= section.deadline_at:
                _finalize_section(section, now)
    section = attempt.sections.filter(status=AssessmentSection.Status.ACTIVE).first()
    return section


@transaction.atomic
def submit_answer(section, question_position, choice_key, now=None):
    now = now or timezone.now()
    section = AssessmentSection.objects.select_for_update().select_related("attempt").get(pk=section.pk)
    if section.status != AssessmentSection.Status.ACTIVE or not section.started_at:
        return False
    if section.deadline_at and now >= section.deadline_at:
        _finalize_section(section, now)
        return False
    if section.attempt.status != QuizAttempt.Status.ACTIVE:
        return False
    snapshot = next((item for item in section.questions_snapshot if item["position"] == int(question_position)), None)
    if not snapshot:
        return False
    choice = next((item for item in snapshot["choices"] if item["key"] == str(choice_key)), None)
    if not choice:
        return False
    response, created = AssessmentResponse.objects.get_or_create(section=section,
        question_position=int(question_position), defaults={"selected_choice_key": str(choice_key), "is_correct": bool(choice["correct"]), "answered_at": now})
    if not created:
        return False
    _finalize_section(section, now)
    return True


@transaction.atomic
def void_and_restart(attempt, *, actor, reason):
    if not getattr(actor, "is_staff", False) and not getattr(actor, "is_superuser", False):
        raise PermissionDenied
    if not reason or not reason.strip():
        raise ValidationError("A reason is required to restart an assessment.")
    attempt = QuizAttempt.objects.select_for_update().get(pk=attempt.pk)
    if attempt.status == QuizAttempt.Status.VOID:
        raise ValidationError("This assessment has already been voided.")
    attempt.status = QuizAttempt.Status.VOID
    attempt.voided_at = timezone.now()
    attempt.void_reason = reason.strip()
    attempt.voided_by = actor
    attempt.save(update_fields=("status", "voided_at", "void_reason", "voided_by"))
    _audit(actor, "assessment.voided", attempt, {"replacement_allowed": True}, reason.strip())
    from apps.assessments.models import EventAssessmentCategory

    ordered_sections = list(attempt.sections.order_by("position"))
    configurations = EventAssessmentCategory.objects.filter(
        event=attempt.experience_session.event,
        category_id__in=[section.category_id for section in ordered_sections],
    )
    config_ids_by_category = {
        configuration.category_id: configuration.pk
        for configuration in configurations
    }
    selected_config_ids = [
        config_ids_by_category[section.category_id]
        for section in ordered_sections
    ]
    replacement = create_attempt(
        attempt.experience_session,
        selected_config_ids,
        is_official=True,
        actor=actor,
        replacement_for=attempt,
    )
    return replacement
