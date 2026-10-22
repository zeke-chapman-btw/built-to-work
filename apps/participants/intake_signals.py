"""Manual changes protect individual fields; intake writes retain provenance."""
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from .models import Participant, ParticipantCareerProfile
from .intake_model_guards import _profile_write
from .intake_projection import (PERSON_FIELDS, LEGACY_GROUPS, protect_manual_person,
                                protect_manual_career)


@receiver(pre_save, sender=Participant)
def _before_person_save(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw or _profile_write.get() or not instance.pk or instance.kind != "person":
        return
    old = sender.objects.filter(pk=instance.pk).values(*set(PERSON_FIELDS.values())).first()
    instance._intake_manual_person_changes = {
        destination for destination, field in PERSON_FIELDS.items()
        if old and (update_fields is None or field in update_fields) and old[field] != getattr(instance, field)}


@receiver(post_save, sender=Participant)
def _person_manual_save(sender, instance, created, raw=False, **kwargs):
    if not raw and not created and not _profile_write.get() and instance.kind == "person":
        protect_manual_person(instance, getattr(instance, "_intake_manual_person_changes", set()))


@receiver(pre_save, sender=ParticipantCareerProfile)
def _before_career_save(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw or _profile_write.get() or not instance.pk:
        return
    old = sender.objects.filter(pk=instance.pk).values("intake_data", *set(LEGACY_GROUPS.values())).first()
    if not old:
        return
    prior = old["intake_data"] or {}
    changes = {key for key in set(prior) | set(instance.intake_data)
               if (update_fields is None or "intake_data" in update_fields)
               and not key.startswith("_") and prior.get(key) != instance.intake_data.get(key)}
    legacy_values = {destination: getattr(instance, field) for destination, field in LEGACY_GROUPS.items()
                     if (update_fields is None or field in update_fields) and old[field] != getattr(instance, field)}
    changes.update(legacy_values)
    instance._intake_manual_legacy_values = legacy_values
    instance._intake_manual_career_changes = changes


@receiver(post_save, sender=ParticipantCareerProfile)
def _career_manual_save(sender, instance, created, raw=False, **kwargs):
    if raw or _profile_write.get():
        return
    if created:
        changes = {key for key in instance.intake_data if not key.startswith("_")}
        changes.update(destination for destination, field in LEGACY_GROUPS.items()
                       if getattr(instance, field) not in (None, ""))
    else:
        changes = getattr(instance, "_intake_manual_career_changes", set())
    legacy_values = ({destination: getattr(instance, field) for destination, field in LEGACY_GROUPS.items()
                      if getattr(instance, field) not in (None, "")} if created else
                     getattr(instance, "_intake_manual_legacy_values", {}))
    protect_manual_career(instance, changes, legacy_values=legacy_values)
