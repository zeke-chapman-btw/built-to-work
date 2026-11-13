"""Where a consent document is shown to participants."""
from django import template
from django.utils import timezone

register = template.Library()


@register.simple_tag
def consent_usage(document):
    from apps.events.models import Event, OfflineEventPreparation
    from apps.participants.models import ConsentDocumentVersion
    current_general = ConsentDocumentVersion.objects.filter(key="btw-intake", is_approved=True).order_by("-effective_at", "-version").first()
    events = list(Event.objects.filter(public_consent_document=document, status="upcoming",
                                       end_at__gte=timezone.now()).order_by("start_at")[:20])
    trailers = list(OfflineEventPreparation.objects.filter(consent_document=document).exclude(status="closed")
                    .select_related("event", "trailer")[:20])
    return {"general": current_general is not None and current_general.pk == document.pk,
            "events": events, "trailers": trailers,
            "unused": not (current_general and current_general.pk == document.pk) and not events and not trailers}
