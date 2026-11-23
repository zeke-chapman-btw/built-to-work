"""Archive, restore or delete a whole participant. Admins and managers only, always with a confirmation.

Archive hides the person from lists, searches, counts and messages; Restore brings them back.
Delete removes the person and everything recorded about them, for test entries and junk records.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.accounts.roles import manager
from apps.core.audit import record_audit
from apps.internal_ui.record_editors import editor_response, editor_success

from .models import Participant


def _allowed(user):
    if not manager(user):
        raise PermissionDenied("Only administrators and managers can archive or delete participants.")


def linked_counts(person):
    """What else is recorded about this person, for the delete confirmation."""
    counts = []
    for rel in person._meta.related_objects:
        if not (rel.one_to_many or rel.one_to_one):
            continue
        n = rel.related_model._base_manager.filter(**{rel.field.name: person}).count()
        if n:
            counts.append((str(rel.related_model._meta.verbose_name_plural), n))
    return sorted(counts, key=lambda item: -item[1])


def _remove(obj, seen):
    key = (obj._meta.label, obj.pk)
    if key in seen:
        return
    seen.add(key)
    for rel in obj._meta.related_objects:
        if not (rel.one_to_many or rel.one_to_one) or rel.on_delete in (models.CASCADE, models.SET_NULL, models.DO_NOTHING):
            continue
        related = rel.related_model._base_manager.filter(**{rel.field.name: obj})
        if rel.field.null:
            related.update(**{rel.field.name: None})  # shared records stay; they just no longer point here
        else:
            for child in list(related):
                _remove(child, seen)
    # Some history models refuse delete(); a confirmed participant delete removes them too.
    type(obj)._base_manager.filter(pk=obj.pk).delete()


def delete_participant(*, actor, person):
    _allowed(actor)
    with transaction.atomic():
        person = Participant.objects.select_for_update().get(pk=person.pk)
        summary = {"name": f"{person.first_name} {person.last_name}".strip(), "email": person.contact_email,
                   "linked": dict(linked_counts(person))}
        record_audit(actor=actor, action="participants.participant_deleted", instance=person, source="staff",
                     old_data=summary, reason="Deleted by staff from the profile.")
        _remove(person, set())


class ConfirmForm(forms.Form):
    confirm = forms.BooleanField(required=True)

    def __init__(self, *args, label, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["confirm"].label = label


def _name(person):
    return f"{person.first_name} {person.last_name}".strip() or f"Participant {person.pk}"


@login_required
@require_http_methods(["GET", "POST"])
def archive(request, participant_id, restore=False):
    _allowed(request.user)
    person = get_object_or_404(Participant, pk=participant_id)
    back = reverse("participants:staff-profile", args=[person.pk])
    label = (f"Restore {_name(person)}" if restore else
             f"Archive {_name(person)}. They'll be hidden from lists, searches and messages until restored.")
    form = ConfirmForm(request.POST or None, label=label)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked = Participant.objects.select_for_update().get(pk=person.pk)
            before = locked.archived_at.isoformat() if locked.archived_at else None
            locked.archived_at = None if restore else timezone.now()
            locked.save(update_fields=["archived_at", "updated_at"])
            record_audit(actor=request.user, action="participants.participant_restored" if restore else "participants.participant_archived",
                         instance=locked, source="staff", old_data={"archived_at": before},
                         new_data={"archived_at": locked.archived_at.isoformat() if locked.archived_at else None},
                         reason="Restored by staff." if restore else "Archived by staff.")
        messages.success(request, f"{_name(person)} {'restored' if restore else 'archived'}.")
        return editor_success(request, back)
    return editor_response(request, template="participants/removal_page.html", panel="internal/ui/record_form_panel.html",
        context={"form": form, "page_title": ("Restore " if restore else "Archive ") + _name(person), "editor_context": "Participant",
                 "back": back, "save_label": "Restore" if restore else "Archive", "fields_template": "participants/removal_fields.html",
                 "full_page_editor": request.headers.get("X-BTW-Editor") != "1", "person": person, "mode": "restore" if restore else "archive"})


@login_required
@require_http_methods(["GET", "POST"])
def delete(request, participant_id):
    _allowed(request.user)
    person = get_object_or_404(Participant, pk=participant_id)
    form = ConfirmForm(request.POST or None, label=f"Delete {_name(person)} and everything recorded about them. This can't be undone.")
    if request.method == "POST" and form.is_valid():
        try:
            delete_participant(actor=request.user, person=person)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"{_name(person)} deleted.")
            return editor_success(request, reverse("participants:staff-directory"))
    return editor_response(request, template="participants/removal_page.html", panel="internal/ui/record_form_panel.html",
        context={"form": form, "page_title": "Delete " + _name(person), "editor_context": "Participant",
                 "back": reverse("participants:staff-profile", args=[person.pk]), "save_label": "Delete for good",
                 "fields_template": "participants/removal_fields.html", "full_page_editor": request.headers.get("X-BTW-Editor") != "1",
                 "person": person, "mode": "delete", "linked": linked_counts(person)})
