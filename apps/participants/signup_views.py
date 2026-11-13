"""Where each sign-up form is used, QR codes for printing, and the trailer sign link."""
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.events.models import Event, TrailerInstance
from apps.internal_ui.record_editors import editor_response, editor_success
from .intake_config_views import admin_page
from .intake_lifecycle import assign, unassign
from .models import IntakeFormAssignment, RegistrationForm
from . import signup_links as links

GENERAL = "general"


def trailer_start(request, trailer_id):
    """Permanent trailer sign: open today's event for this trailer, or the general form."""
    trailer = get_object_or_404(TrailerInstance, pk=trailer_id, is_active=True)
    event = links.trailer_event(trailer)
    return redirect(links.event_path(event) if event else links.general_path())


def _target(kind, obj_id):
    if kind == GENERAL:
        return None, "General online sign-up", links.general_path()
    if kind == "event":
        event = get_object_or_404(Event, pk=obj_id)
        return event, event.name, links.event_path(event)
    if kind == "trailer":
        trailer = get_object_or_404(TrailerInstance, pk=obj_id, is_active=True)
        return trailer, f"{trailer} sign-up", links.trailer_path(trailer)
    raise Http404


@admin_page
def qr_image(request, kind, obj_id=None):
    from apps.events.ticket_renderer import qr_png_bytes
    _, title, path = _target(kind, obj_id)
    response = HttpResponse(qr_png_bytes(request.build_absolute_uri(path), scale=12), content_type="image/png")
    if request.GET.get("download"):
        name = "".join(ch if ch.isalnum() else "-" for ch in title.lower()).strip("-")[:60] or "sign-up"
        response["Content-Disposition"] = f'attachment; filename="{name}-qr.png"'
    response["Cache-Control"] = "private, no-store"
    return response


@admin_page
def qr_sheet(request, kind, obj_id=None):
    target, title, path = _target(kind, obj_id)
    event = target if kind == "event" else None
    return render(request, "participants/signup_qr_sheet.html", {
        "title": title, "kind": kind, "event": event, "trailer": target if kind == "trailer" else None,
        "url": request.build_absolute_uri(path),
        "image": reverse("participants:signup-qr-image", args=[kind] + ([obj_id] if obj_id else []))})


class WhereForm(forms.Form):
    form = forms.ChoiceField(label="Form to use", widget=forms.RadioSelect(attrs={"class": "btw-chip-choices"}))

    def __init__(self, *args, allow_general=False, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "Use the general online form")] if allow_general else []
        for item in RegistrationForm.objects.filter(is_smart_intake=True, is_active=True).order_by("name"):
            published = item.versions.filter(status="published").order_by("-version").first()
            if published:
                choices.append((str(published.pk), f"{item.name} (version {published.version})"))
        self.fields["form"].choices = choices
        self.fields["form"].required = not allow_general
        if not allow_general:
            self.fields["form"].help_text = "Only published forms can be used. Publish a draft first to see it here."
        else:
            self.fields["form"].help_text = "Only published forms are listed."


@admin_page
@never_cache
@require_http_methods(["GET", "POST"])
def where_edit(request, kind, obj_id=None):
    target, title, _ = _target(kind, obj_id)
    if kind == "trailer":
        raise Http404
    event = target if kind == "event" else None
    channel = "event" if event else "online"
    current = IntakeFormAssignment.objects.filter(channel=channel, event=event, trailer=None, is_active=True).first()
    form = WhereForm(request.POST or None, allow_general=event is not None,
                     initial={"form": str(current.active_version_id) if current else ""})
    back = reverse("participants:intake-config-index")
    if request.method == "POST" and form.is_valid():
        try:
            if form.cleaned_data["form"]:
                assign(actor=request.user, version_id=int(form.cleaned_data["form"]), channel=channel, event=event)
            elif event is not None:
                unassign(actor=request.user, channel="event", event=event)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Saved. People signing up now see this form.")
            return editor_success(request, back)
    return editor_response(request, template="participants/signup_where_page.html", panel="internal/ui/record_form_panel.html", context={
        "form": form, "page_title": "Choose the sign-up form", "editor_context": title, "back": back,
        "save_label": "Save changes", "fields_template": "internal/ui/section_fields.html",
        "full_page_editor": request.headers.get("X-BTW-Editor") != "1"})
