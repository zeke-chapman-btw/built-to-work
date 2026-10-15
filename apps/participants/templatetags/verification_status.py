from django import template
from apps.participants.qualification_models import ParticipantQualification
from apps.participants.verification_services import document_verification_summary, qualification_verification_summary

register = template.Library()

@register.filter
def verification_summary(target):
    if isinstance(target, ParticipantQualification):
        return qualification_verification_summary(target)
    return document_verification_summary(target)
