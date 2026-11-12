"""Event housing: add, edit and remove the places staff stay, from pop-up editors."""
from django import forms
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from apps.core.audit import record_audit
from apps.internal_ui.record_editors import event_return, editor_response, editor_success
from .models import Event, EventLodging
from .views import staff_permission_required

FIELDS = ("kind", "name", "address_line_1", "city", "state", "postal_code", "phone",
          "check_in", "check_out", "confirmation_number", "rooms", "booked_under", "notes")


class LodgingForm(forms.ModelForm):
    class Meta:
        model = EventLodging
        fields = FIELDS
        widgets = {"check_in": forms.DateInput(attrs={"type": "date"}),
                   "check_out": forms.DateInput(attrs={"type": "date"}),
                   "notes": forms.Textarea(attrs={"rows": 3}),
                   "phone": forms.TextInput(attrs={"type": "tel"})}


def _snapshot(item):
    return {name: str(getattr(item, name) or "") for name in FIELDS}


def _form_response(request, event, form, *, title, back, can_remove=False):
    return editor_response(request, template="events/section_form.html", panel="internal/ui/record_form_panel.html", context={
        "event": event, "form": form, "page_title": title, "editor_context": event.name, "back": back,
        "save_label": "Save housing", "editor_return_label": "event", "fields_template": "events/housing_fields.html",
        "can_remove": can_remove, "full_page_editor": request.headers.get("X-BTW-Editor") != "1"})


@staff_permission_required("events.view_event", "events.change_event")
@require_http_methods(["GET", "POST"])
def add(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    back = event_return(request, event)
    initial = {}
    if not event.lodgings.exists():
        initial = {"check_in": event.setup_date or event.local_start_at.date(), "check_out": event.return_date or event.local_end_at.date(),
                   "city": event.city, "state": event.state}
    form = LodgingForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            item = form.save(commit=False)
            item.event = event
            item.created_by = request.user
            item.full_clean()
            item.save()
            record_audit(actor=request.user, action="event.housing.added", instance=event, source="event_housing", new_data=_snapshot(item))
        return editor_success(request, back)
    return _form_response(request, event, form, title="Add housing", back=back)


@staff_permission_required("events.view_event", "events.change_event")
@require_http_methods(["GET", "POST"])
def edit(request, event_id, lodging_id):
    event = get_object_or_404(Event, pk=event_id)
    item = get_object_or_404(EventLodging, pk=lodging_id, event=event)
    back = event_return(request, event)
    before = _snapshot(item)
    if request.method == "POST" and request.POST.get("remove") == "1":
        with transaction.atomic():
            record_audit(actor=request.user, action="event.housing.removed", instance=event, source="event_housing", old_data=before)
            item.delete()
        return editor_success(request, back)
    form = LodgingForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            record_audit(actor=request.user, action="event.housing.updated", instance=event, source="event_housing",
                         old_data=before, new_data=_snapshot(item))
        return editor_success(request, back)
    return _form_response(request, event, form, title="Edit housing", back=back, can_remove=True)
