"""Message templates: saved wording for the Email and Text pop-ups."""
import re

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.accounts.roles import manager
from apps.internal_ui.record_editors import editor_response, editor_success

from .models import MessageTemplate

FIELDS = (("{first_name}", "their first name"), ("{last_name}", "their last name"), ("{my_name}", "your first name"))


def fill(text, participant=None, user=None):
    values = {"first_name": getattr(participant, "preferred_name", "") or getattr(participant, "first_name", "") or "",
              "last_name": getattr(participant, "last_name", "") or "",
              "my_name": (getattr(user, "first_name", "") or "") if user else ""}
    return re.sub(r"\{(first_name|last_name|my_name)\}", lambda m: values[m.group(1)], text or "")


def for_channel(channel):
    return MessageTemplate.objects.filter(is_active=True, channel__in=("both", channel))


def choices_for(request, channel, url):
    """Links that reload the pop-up started from each template."""
    picked = request.GET.get("template", "")
    base = re.sub(r"([?&])template=\d+&?", r"\1", url).rstrip("?&")
    return [{"name": t.name, "url": f"{base}{'&' if '?' in base else '?'}template={t.pk}", "active": str(t.pk) == picked}
            for t in for_channel(channel)[:20]]


def picked(request, channel):
    value = request.GET.get("template", "")
    return for_channel(channel).filter(pk=value).first() if value.isdigit() else None


def _manager(request):
    if not manager(request.user):
        raise PermissionDenied("Message templates are managed by managers and administrators.")


class TemplateForm(forms.ModelForm):
    class Meta:
        model = MessageTemplate
        fields = ("name", "channel", "subject", "body", "is_active")
        labels = {"name": "Template name", "channel": "Use for", "subject": "Email subject", "body": "Message",
                  "is_active": "Show this template in the Email and Text pop-ups"}
        help_texts = {"subject": "Only used for email.",
                      "body": "Fill-ins: " + ", ".join(f"{code} = {label}" for code, label in FIELDS) + ". Texts over 160 characters go as more than one text."}
        widgets = {"body": forms.Textarea(attrs={"rows": 7})}


@login_required(login_url="/admin/login/")
def template_list(request):
    _manager(request)
    return render(request, "messaging/templates_list.html", {"page_title": "Settings",
                  "templates": MessageTemplate.objects.select_related("updated_by"), "fields": FIELDS})


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def template_edit(request, pk=None):
    _manager(request)
    row = get_object_or_404(MessageTemplate, pk=pk) if pk else None
    back = reverse("messaging:templates")
    if request.method == "POST" and row and request.POST.get("remove") == "1":
        row.delete()
        messages.success(request, "Template deleted.")
        return editor_success(request, back)
    form = TemplateForm(request.POST or None, instance=row, initial=None if row else {"is_active": True})
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.updated_by = request.user
        if not row:
            item.created_by = request.user
        item.save()
        messages.success(request, "Template saved.")
        return editor_success(request, back)
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
                           context={"form": form, "page_title": "Edit template" if row else "New template",
                                    "editor_context": "Saved wording staff can start an email or text from.",
                                    "save_label": "Save template", "back": back, "back_label": "Message templates", "fields_template": "messaging/template_fields.html",
                                    "template_row": row})
