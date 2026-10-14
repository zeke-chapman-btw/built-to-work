"""Keep the shared online and onsite registration path in sync with qualifications."""
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from apps.participants.models import RegistrationSubmission
from apps.participants.qualification_services import process_registration_submission


@receiver(pre_save, sender=RegistrationSubmission)
def _remember_submission_status(sender, instance, **kwargs):
    instance._qualification_previous_status = None
    if instance.pk:
        instance._qualification_previous_status = sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first()


@receiver(post_save, sender=RegistrationSubmission)
def _map_submitted_registration(sender, instance, created, **kwargs):
    previous = getattr(instance, "_qualification_previous_status", None)
    if instance.status == "submitted" and (created or previous != "submitted"):
        process_registration_submission(instance)
