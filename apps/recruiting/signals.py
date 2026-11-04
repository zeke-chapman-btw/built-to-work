"""Track only meaningful profile fields; unrelated saves never imply confirmation."""
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils import timezone
from apps.participants.models import Participant, ParticipantCareerProfile

FIELDS = {
    Participant: ('first_name', 'last_name', 'preferred_name', 'contact_email', 'contact_phone', 'city', 'state', 'postal_code'),
    ParticipantCareerProfile: ('career_interests', 'skills_interests', 'years_of_experience', 'work_availability',
        'willing_to_travel', 'preferred_locations', 'preferred_work_type', 'employment_status', 'employment_interest', 'education_training'),
}


@receiver(pre_save, sender=Participant)
@receiver(pre_save, sender=ParticipantCareerProfile)
def capture_relevant_change(sender, instance, raw=False, **kwargs):
    if raw:
        return
    fields = FIELDS[sender]
    previous = sender.objects.filter(pk=instance.pk).values(*fields).first() if instance.pk else None
    instance._recruiting_relevant_change = previous is None or any(previous[k] != getattr(instance, k) for k in fields)


@receiver(post_save, sender=Participant)
@receiver(post_save, sender=ParticipantCareerProfile)
def record_relevant_change(sender, instance, raw=False, **kwargs):
    if raw or not getattr(instance, '_recruiting_relevant_change', False):
        return
    from .models import RecruitingProfile
    participant_id = instance.pk if sender is Participant else instance.participant_id
    # Do not create/backfill recruiting state for every legacy person or synthetic fixture.
    RecruitingProfile.objects.filter(participant_id=participant_id).update(last_relevant_update_at=timezone.now())
