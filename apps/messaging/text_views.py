"""Text a participant, and the Text replies inbox."""
import json
import uuid

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from apps.crm import access as crm_access
from apps.internal_ui.record_editors import editor_response, editor_success
from apps.participants.models import Participant

from . import texting
from .models import TextMessage


def staff_choices(include_blank=False):
    from django.db.models import Q
    from apps.participants.workspace_access import can_notes
    users = (get_user_model().objects.filter(is_active=True, is_staff=True)
             .filter(Q(staff_profile__isnull=True) | Q(staff_profile__is_active=True))
             .order_by("first_name", "last_name", "username"))
    choices = [(str(u.pk), u.get_full_name() or u.get_username()) for u in users if _can_receive(u, can_notes)]
    return ([("", "No one (leave unassigned)")] if include_blank else []) + choices


def _can_receive(user, can_notes):
    try:
        return bool(can_notes(user))
    except PermissionDenied:
        return False


def _inbox_user(request):
    from apps.participants.workspace_access import can_notes, require_people
    user = crm_access.fresh_staff(request.user)
    require_people(user)
    if not can_notes(user):
        raise PermissionDenied("Your role does not include participant messages.")
    return user


class TextForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput)
    body = forms.CharField(label="Message", max_length=600, widget=forms.Textarea(attrs={"rows": 5, "maxlength": 600}),
                           help_text="Texts over 160 characters are sent as more than one text.")
    replies_to = forms.ChoiceField(label="Send replies to",
                                   help_text="When they text back, the reply shows up in this person's Inbox.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["replies_to"].choices = staff_choices()


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def text_participant(request, participant_id):
    participant = get_object_or_404(Participant, pk=participant_id, archived_at__isnull=True)
    user = crm_access.internal_context(request.user, participant=participant, write=True)
    profile = reverse("participants:staff-profile", args=[participant.pk])
    back = request.GET.get("next") or request.POST.get("next") or profile + "?section=activity"
    if not back.startswith("/"):
        back = profile
    reply_id = request.GET.get("reply")
    form = TextForm(request.POST or None, initial={"operation_id": uuid.uuid4(), "replies_to": str(user.pk)})
    if str(user.pk) not in {c[0] for c in form.fields["replies_to"].choices}:
        form.fields["replies_to"].choices = [(str(user.pk), user.get_full_name() or user.get_username())] + \
            form.fields["replies_to"].choices
    phone = texting.phone_key(participant.contact_phone)
    problem = ("This person has no mobile number on file." if not phone else
               "This number replied STOP. They need to text START before they can be texted again." if texting.opted_out(phone) else
               "Texting isn't set up yet. An administrator can set it up in Settings → Connections." if not texting.ready() else "")
    recent = list(TextMessage.objects.filter(phone=phone).select_related("sent_by", "replies_to").order_by("-created_at")[:6]) if phone else []
    context = {"form": form, "participant": participant, "problem": problem, "recent": list(reversed(recent)),
               "page_title": "Text " + participant.first_name, "back": back, "profile_url": profile,
               "editor_context": f"To {participant.contact_phone}" if phone else "No mobile number on file",
               "save_label": "Send text", "fields_template": "messaging/text_fields.html", "reply_id": reply_id}
    if request.method == "POST" and not problem and form.is_valid():
        owner = get_user_model().objects.filter(pk=form.cleaned_data["replies_to"], is_active=True, is_staff=True).first()
        try:
            texting.send_to_participant(actor=user, participant=participant, body=form.cleaned_data["body"].strip(),
                                        replies_to=owner or user, operation_id=form.cleaned_data["operation_id"])
        except texting.TextError as exc:
            form.add_error(None, str(exc))
        else:
            if reply_id:
                TextMessage.objects.filter(pk=reply_id, direction="in", phone=phone, state="new").update(
                    state="done", handled_at=timezone.now(), handled_by=user)
            who = "you" if (owner or user) == user else (owner.get_full_name() or owner.get_username())
            messages.success(request, f"Text sent to {participant.first_name}. Replies will go to {who}.")
            return editor_success(request, back)
    elif request.method == "POST" and problem:
        form.is_valid()
        form.add_error(None, problem)
    return editor_response(request, template="messaging/text_page.html", context=context, panel="messaging/text_panel.html")


TABS = (("mine", "Mine"), ("unassigned", "Unassigned"), ("all", "All replies"), ("sent", "Sent"))


@login_required(login_url="/admin/login/")
def inbox(request):
    user = _inbox_user(request)
    tab = request.GET.get("tab") or "mine"
    tab = tab if tab in dict(TABS) else "mine"
    show_done = request.GET.get("done") == "1"
    rows = TextMessage.objects.select_related("participant", "replies_to", "sent_by", "handled_by")
    base_in = rows.filter(direction=TextMessage.Direction.IN)
    counts = {"mine": base_in.filter(replies_to=user, state="new").count(),
              "unassigned": base_in.filter(replies_to__isnull=True, state="new").count(),
              "all": base_in.filter(state="new").count(), "sent": None}
    if tab == "sent":
        rows = rows.filter(direction=TextMessage.Direction.OUT)
    else:
        rows = base_in
        if tab == "mine":
            rows = rows.filter(replies_to=user)
        elif tab == "unassigned":
            rows = rows.filter(replies_to__isnull=True)
        if not show_done:
            rows = rows.filter(state="new")
    page = Paginator(rows.order_by("-created_at"), 40).get_page(request.GET.get("page"))
    return render(request, "messaging/texts.html", {
        "page_title": "Inbox", "tab": tab, "tabs": [(k, label, counts[k]) for k, label in TABS],
        "page": page, "show_done": show_done, "staff": staff_choices(include_blank=True), "me": user,
        "ready": texting.ready(), "forwarding": texting.reply_settings()[1].get("forwarding"),
    })


def _back(request):
    value = request.POST.get("next") or ""
    return value if value.startswith("/messaging/texts/") else reverse("messaging:texts")


@login_required(login_url="/admin/login/")
@require_POST
def text_handled(request, pk):
    user = _inbox_user(request)
    row = get_object_or_404(TextMessage, pk=pk, direction=TextMessage.Direction.IN)
    if request.POST.get("undo"):
        row.state, row.handled_at, row.handled_by = TextMessage.State.NEW, None, None
    else:
        row.state, row.handled_at, row.handled_by = TextMessage.State.DONE, timezone.now(), user
    row.save(update_fields=["state", "handled_at", "handled_by"])
    return redirect(_back(request))


@login_required(login_url="/admin/login/")
@require_POST
def text_reassign(request, pk):
    _inbox_user(request)
    row = get_object_or_404(TextMessage, pk=pk, direction=TextMessage.Direction.IN)
    owner_id = request.POST.get("owner") or ""
    owner = get_user_model().objects.filter(pk=owner_id, is_active=True, is_staff=True).first() if owner_id else None
    row.replies_to = owner
    row.save(update_fields=["replies_to"])
    if owner:
        texting._nudge(row)
    messages.success(request, f"Reply moved to {owner.get_full_name() or owner.get_username()}." if owner else "Reply left unassigned.")
    return redirect(_back(request))


@csrf_exempt
@require_POST
def eztexting_webhook(request, token):
    _row, values = texting.reply_settings()
    if not values.get("path_token") or token != values["path_token"]:
        return HttpResponseForbidden()
    secret_value = texting.webhook_secret()
    if secret_value and not texting.verify_signature(request.body, request.headers.get("X-Signature-256", ""), secret_value):
        return HttpResponseForbidden()
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest()
    if not isinstance(payload, dict):
        return HttpResponseBadRequest()
    kind = payload.get("type") or "inbound_text.received"
    if kind == "inbound_text.received":
        texting.receive(payload)
    return HttpResponse("ok")


def new_reply_count(user):
    return TextMessage.objects.filter(direction=TextMessage.Direction.IN, replies_to=user, state="new").count()
