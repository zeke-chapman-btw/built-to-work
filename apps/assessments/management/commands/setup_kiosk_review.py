"""Create isolated, repeatable development content for the real kiosk UI."""
from datetime import timedelta
from pathlib import Path
from pathlib import Path

from django.core.files.base import ContentFile
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.assessments.models import AssessmentCategory, EventAssessmentCategory, Question, QuestionChoice, QuestionSet, QuestionSetItem
from apps.events.models import Attendance, Event, EventRegistration
from apps.stations.kiosk_review import TEST_EVENT_CODE
from apps.stations.models import EventStation, ExperienceSession, Station


class Command(BaseCommand):
    help = "Set up development-only kiosk review content without real participants or changing the active station Event."

    def add_arguments(self, parser):
        parser.add_argument("--station", default="KIOSK-01")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Kiosk review setup is development-only (DEBUG must be enabled).")
        station = Station.objects.filter(code=options["station"], station_type=Station.Type.KIOSK, is_active=True).first()
        if station is None:
            raise CommandError("An existing active Kiosk station is required.")
        now = timezone.now()
        event, created = Event.objects.get_or_create(code=TEST_EVENT_CODE, defaults={
            "name": "Kiosk Development UI Review", "description": "Development Test Mode only; synthetic UI questions, not a validated assessment.",
            "start_at": now - timedelta(days=1), "end_at": now + timedelta(days=3650), "status": Event.Status.UPCOMING,
        })
        if EventRegistration.objects.filter(event=event).exists() or Attendance.objects.filter(event=event).exists() or ExperienceSession.objects.filter(event=event, mode=ExperienceSession.Mode.OFFICIAL).exists():
            raise CommandError("Reserved review Event contains official records; no changes made.")
        # This assignment is deliberately NOT the kiosk's active official Event context.
        assignment, _ = EventStation.objects.get_or_create(event=event, station=station, defaults={"enabled": True, "is_active_context": False})
        if not assignment.enabled or assignment.is_active_context:
            raise CommandError("The reserved review assignment must be enabled and separate from the active official context.")
        categories = ["Auto Mechanic", "Civil Construction", "Electrical", "Plumbing", "Welding", "Construction Labor", "HVAC", "Warehouse", "Heavy Equipment Technician"]
        for order, name in enumerate(categories, start=1):
            category, _ = AssessmentCategory.objects.get_or_create(slug="kiosk-review-" + slugify(name), defaults={"name": "UI Review: " + name, "display_order": order})
            question_set, new_set = QuestionSet.objects.get_or_create(category=category, version=1, defaults={"name": "Synthetic kiosk UI review"})
            if new_set:
                words = ["Hammer", "Wrench", "Gloves", "Helmet", "Tape measure"]
                for difficulty in Question.Difficulty.values:
                    for index, word in enumerate(words):
                        question = Question.objects.create(category=category, text=f"UI review: Which answer says '{word}'?", difficulty=difficulty)
                        for choice_order, answer in enumerate([word, "Screwdriver", "Safety glasses", "Pliers"]):
                            QuestionChoice.objects.create(question=question, text=answer, is_correct=choice_order == 0, display_order=choice_order)
                        QuestionSetItem.objects.create(question_set=question_set, question=question)
                question_set.publish()
            if not question_set.readiness()["ready"]:
                raise CommandError("An existing review question set is not ready; it was not overwritten.")
            config, _ = EventAssessmentCategory.objects.get_or_create(event=event, category=category, defaults={"question_set": question_set, "display_order": order, "display_name": name})
            if not config.enabled or config.question_set_id != question_set.pk:
                raise CommandError("An existing review category differs from expected setup; it was not overwritten.")
        # Attach local, synthetic image examples only to the Development/Test Event review bank.
        image_review_questions = (
            ('Auto Mechanic', 'kiosk-test-engine.svg'),
            ('Civil Construction', 'kiosk-test-site.svg'),
        )
        for category_name, asset_name in image_review_questions:
            category = AssessmentCategory.objects.get(slug='kiosk-review-' + slugify(category_name))
            question = Question.objects.filter(category=category, text__startswith='UI review:').order_by('difficulty', 'created_at').first()
            if question and not question.image:
                asset_path = Path(settings.BASE_DIR) / 'static' / 'kiosk' / 'images' / 'test-mode' / asset_name
                question.image.save(asset_name, ContentFile(asset_path.read_bytes()), save=True)

        self.stdout.write(self.style.SUCCESS(f"Ready: {len(categories)} categories, 15 questions each, 5 per difficulty. Active official Event context preserved."))
