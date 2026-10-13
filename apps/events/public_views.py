"""Public event links and authorized registration-link management."""
from io import BytesIO

import qrcode
from django import forms
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.utils.text import slugify

from apps.participants.models import ConsentDocumentVersion, RegistrationFormVersion
from apps.participants.registration_views import registration_form
from .models import Event, EventGroup, EventPublicSlug, EventRegistration, OperationalIncident, OfflineRegistrationEnvelope
from .services import audit_action
from .views import staff_required


class PublicEventSettingsForm(forms.Form):
    slug = forms.SlugField(max_length=100, required=False, help_text="Leave blank to generate from the event name.")
    status = forms.ChoiceField(choices=Event._meta.get_field("public_registration_status").choices)
    consent_document = forms.ModelChoiceField(
        queryset=ConsentDocumentVersion.objects.none(), required=False,
        label="Approved event consent document",
    )

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.fields["consent_document"].queryset = ConsentDocumentVersion.objects.filter(is_approved=True).order_by("-published_at", "-version")

    def clean_slug(self):
        slug = (self.cleaned_data["slug"] or slugify(self.event.name)).lower()
        if not slug:
            raise forms.ValidationError("Enter a public URL slug.")
        if EventPublicSlug.objects.filter(slug=slug).exclude(event=self.event).exists():
            raise forms.ValidationError("This public URL is already in use.")
        return slug

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") == "open":
            if self.event.status != Event.Status.UPCOMING:
                self.add_error("status", "Only upcoming events can open public registration.")
            if not self.event.registration_is_open or self.event.end_at < timezone.now():
                self.add_error("status", "Update the event registration dates before opening this link.")
            document = cleaned.get("consent_document")
            if (not document or not document.is_approved or not document.body.strip()
                or (document.effective_at and document.effective_at > timezone.now())):
                self.add_error("consent_document", "Select an approved consent document with reviewed text.")
            if not RegistrationFormVersion.objects.filter(status=RegistrationFormVersion.Status.PUBLISHED).exists():
                self.add_error("status", "Publish the standard registration form before opening this link.")
        return cleaned


def _current_link(event):
    return event.public_slugs.filter(is_current=True).first()


@staff_required
def public_settings(request, event_id):
    if not request.user.has_perm("events.change_event"):
        raise PermissionDenied
    event = get_object_or_404(Event, pk=event_id)
    current = _current_link(event)
    initial = {"slug": current.slug if current else "", "status": event.public_registration_status,
               "consent_document": event.public_consent_document_id}
    form = PublicEventSettingsForm(request.POST or None, event=event, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            event = Event.objects.select_for_update().get(pk=event.pk)
            previous = _current_link(event)
            slug = form.cleaned_data["slug"]
            if previous is None or previous.slug != slug:
                EventPublicSlug.objects.filter(event=event, is_current=True).update(is_current=False)
                alias = EventPublicSlug.objects.filter(slug=slug).first()
                if alias and alias.event_id != event.pk:
                    form.add_error("slug", "This public URL is already in use.")
                elif alias:
                    alias.is_current = True
                    alias.save(update_fields=["is_current"])
                else:
                    try:
                        EventPublicSlug.objects.create(event=event, slug=slug, is_current=True)
                    except IntegrityError:
                        form.add_error("slug", "This public URL is already in use.")
            if form.errors:
                # Keep the existing canonical slug if the update fails.
                transaction.set_rollback(True)
            else:
                old = {"status": event.public_registration_status, "slug": previous.slug if previous else None,
                       "consent_document_id": event.public_consent_document_id}
                event.public_registration_status = form.cleaned_data["status"]
                event.public_consent_document = form.cleaned_data["consent_document"]
                event.save(update_fields=["public_registration_status", "public_consent_document", "updated_at"])
                audit_action(actor=request.user, action="event.public_registration.updated", instance=event,
                             old_data=old, new_data={"status": event.public_registration_status,
                                                     "slug": slug, "consent_document_id": event.public_consent_document_id})
                return redirect("events:public-settings", event_id=event.pk)
    current = _current_link(event)
    url = request.build_absolute_uri(reverse("public-event-registration", kwargs={"slug": current.slug})) if current else ""
    groups = EventGroup.objects.filter(event=event).annotate(active_count=Count("registrations", filter=Q(registrations__status=EventRegistration.Status.ACTIVE)))
    counts = EventRegistration.objects.filter(event=event, status=EventRegistration.Status.ACTIVE)
    return render(request, "events/public_settings.html", {
        "event": event, "form": form, "public_url": url, "current_slug": current,
        "total_count": counts.count(), "online_count": counts.filter(source=EventRegistration.Source.ONLINE).count(),
        "trailer_count": OfflineRegistrationEnvelope.objects.filter(preparation__event=event).values("registration_id").distinct().count(), "groups": groups,
        "issues": OperationalIncident.objects.filter(event=event, status=OperationalIncident.Status.OPEN).order_by("-last_seen_at")[:10],
    })


@staff_required
def public_link_qr(request, event_id):
    if not request.user.has_perm("events.change_event"):
        raise PermissionDenied
    event = get_object_or_404(Event, pk=event_id)
    current = _current_link(event)
    if not current:
        raise Http404
    url = request.build_absolute_uri(reverse("public-event-registration", kwargs={"slug": current.slug}))
    output = BytesIO()
    qrcode.make(url).save(output, format="PNG")
    response = HttpResponse(output.getvalue(), content_type="image/png")
    response["Content-Disposition"] = f'attachment; filename="{current.slug}-registration-qr.png"'
    return response


def public_event_registration(request, slug):
    link = EventPublicSlug.objects.select_related("event", "event__public_consent_document").filter(slug=slug).first()
    if not link:
        raise Http404
    preview_allowed = request.method == "GET" and request.GET.get("preview") == "1" and request.user.is_authenticated and request.user.is_staff and request.user.has_perm("events.change_event")
    if not preview_allowed and (link.event.status != Event.Status.UPCOMING or link.event.public_registration_status == "disabled"):
        raise Http404
    current = _current_link(link.event)
    if not current:
        raise Http404
    if link.pk != current.pk:
        return redirect("public-event-registration", slug=current.slug)
    event = link.event
    preview = request.method == "GET" and request.GET.get("preview") == "1" and request.user.is_authenticated and request.user.is_staff and request.user.has_perm("events.change_event")
    if preview:
        return registration_form(request, fixed_event=event, public=True)
    if event.public_registration_status != "open" or not event.registration_is_open or event.end_at < timezone.now():
        reason = "Public registration is paused for this event." if event.public_registration_status == "paused" else "Public registration is closed for this event."
        return render(request, "participants/registration_unavailable.html", {"event": event, "reason": reason}, status=403)
    if event.registration_capacity is not None and EventRegistration.objects.filter(event=event, status=EventRegistration.Status.ACTIVE).count() >= event.registration_capacity:
        return render(request, "participants/registration_unavailable.html", {"event": event, "reason": "This event is at capacity."}, status=403)
    if (not event.public_consent_document or not event.public_consent_document.is_approved
            or not event.public_consent_document.body.strip()
            or (event.public_consent_document.effective_at and event.public_consent_document.effective_at > timezone.now())):
        return render(request, "participants/registration_unavailable.html", {"event": event,
                      "reason": "Registration is temporarily unavailable while BTW updates its approved consent information."}, status=503)
    return registration_form(request, fixed_event=event, public=True)
