"""Staff editing of game names and availability (replaces the raw database admin for everyday edits)."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.core.audit import record_audit
from apps.internal_ui.record_editors import editor_response, editor_success
from .models import GameDefinition


class GameForm(forms.ModelForm):
    class Meta:
        model = GameDefinition
        fields = ("display_name", "active")
        labels = {"display_name": "Name people see", "active": "Can be used at events"}
        help_texts = {"active": "Turning a game off keeps past scores. Events already using it keep it until changed."}


@login_required
@require_http_methods(["GET", "POST"])
def edit(request, game_id):
    if not request.user.has_perm("games.change_gamedefinition"):
        raise PermissionDenied
    game = get_object_or_404(GameDefinition, pk=game_id)
    before = {"display_name": game.display_name, "active": game.active}
    form = GameForm(request.POST or None, instance=game)
    back = reverse("internal:workspace", args=["operations"]) + "?resource=games"
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        record_audit(actor=request.user, action="games.definition.updated", instance=saved,
                     old_data=before, new_data={"display_name": saved.display_name, "active": saved.active})
        messages.success(request, "Game saved.")
        return editor_success(request, back)
    return editor_response(request, template="games/manage_form.html", panel="internal/ui/record_form_panel.html", context={
        "form": form, "page_title": "Edit game", "editor_context": game.display_name, "back": back,
        "save_label": "Save changes", "fields_template": "internal/ui/section_fields.html",
        "full_page_editor": request.headers.get("X-BTW-Editor") != "1"})
