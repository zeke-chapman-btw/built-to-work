"""Re-sync a person to Mailchimp when the details Mailchimp shows change."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import MarketingConsent


def _mark(participant_id):
    MarketingConsent.objects.filter(participant_id=participant_id, needs_sync=False).exclude(
        status=MarketingConsent.Status.OFF, synced_email="").update(needs_sync=True)


@receiver(post_save, sender="participants.Participant", dispatch_uid="messaging-participant-sync")
def participant_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _mark(instance.pk)


@receiver(post_save, sender="participants.ParticipantCareerProfile", dispatch_uid="messaging-career-sync")
def career_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _mark(instance.participant_id)


@receiver(post_save, sender="events.EventRegistration", dispatch_uid="messaging-registration-sync")
def registration_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _mark(instance.participant_id)


@receiver(post_save, sender="reporting.PayrollApproval", dispatch_uid="messaging-statement-notices")
def payroll_approved(sender, instance, created=False, raw=False, **kwargs):
    if raw or not created:
        return
    from django.db import transaction

    def send():
        from .statements import notify
        try:
            notify(instance.pk)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("STATEMENT_NOTICES_FAILED")
    transaction.on_commit(send)
