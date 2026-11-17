"""Staff changes to marketing email consent, Mailchimp setup and its webhook."""
import hmac

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from apps.internal_ui.record_editors import editor_response, editor_success, local_return
from apps.participants.models import Participant

from . import mailchimp, marketing
from .connections import _admin
from .models import MARKETING_WORDING, MarketingConsent


class ConsentForm(forms.Form):
    status = forms.ChoiceField(label="Marketing email", widget=forms.RadioSelect,
                               choices=[("on", "On — they agreed to job and event news by email"), ("off", "Off")])
    note = forms.CharField(label="How did they agree, or why is it changing?", max_length=300, required=False,
                           help_text="Required when turning it on, for example “Paper sign-up form at the Augusta event, Nov 12”.")

    def clean(self):
        data = super().clean()
        if data.get("status") == "on" and not (data.get("note") or "").strip():
            self.add_error("note", "Say how they agreed, so there's a record.")
        return data


def _can_edit(user, participant):
    if not (user.is_active and user.is_staff):
        return False
    return user.has_perm("participants.change_participant") or user.has_perm("participants.add_participantnote")


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def edit_consent(request, participant_id):
    participant = get_object_or_404(Participant, pk=participant_id, archived_at__isnull=True)
    if not _can_edit(request.user, participant):
        raise PermissionDenied
    profile = reverse("participants:staff-profile", args=[participant.pk])
    back = request.POST.get("return_to") or request.GET.get("return_to") or profile
    back = back if back.startswith(profile) else profile
    row = marketing.current(participant)
    form = ConsentForm(request.POST or None, initial={"status": "on" if row and row.status == "on" else "off"})
    if request.method == "POST" and form.is_valid():
        status = form.cleaned_data["status"]
        marketing.set_status(participant=participant, status=status, source=MarketingConsent.Source.STAFF,
                             actor=request.user, note=form.cleaned_data["note"],
                             wording=MARKETING_WORDING if status == "on" else "")
        messages.success(request, "Marketing email turned " + ("on." if status == "on" else "off."))
        return editor_success(request, back)
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/consent_panel.html", context={
        "form": form, "page_title": "Marketing email", "save_label": "Save", "back": back,
        "editor_context": f"{participant.first_name} {participant.last_name} · {participant.contact_email or 'no email on file'}",
        "fields_template": "messaging/consent_fields.html", "row": row, "history": marketing.history(participant),
        "wording": MARKETING_WORDING})


class MailchimpKeyForm(forms.Form):
    api_key = forms.CharField(label="Mailchimp API key", max_length=200, widget=forms.PasswordInput(render_value=False),
                              help_text="In Mailchimp: Profile → Extras → API keys → Create a key.")


class MailchimpAudienceForm(forms.Form):
    audience_id = forms.ChoiceField(label="Audience to use", widget=forms.RadioSelect)


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def mailchimp_setup(request):
    _admin(request)
    home = reverse("messaging:connections") + "#mailchimp"
    pending = request.session.get("btw_mailchimp_key", "")
    if request.method == "POST" and request.POST.get("step") == "audience" and pending:
        from . import crypto
        api_key = crypto.decrypt(pending, "mailchimp-pending")
        try:
            options = mailchimp.audiences(api_key)
        except mailchimp.MailchimpError as exc:
            options = []
            messages.error(request, str(exc))
        form = MailchimpAudienceForm(request.POST)
        form.fields["audience_id"].choices = [(i, f"{n} ({c} contacts)") for i, n, c in options]
        if form.is_valid():
            try:
                mailchimp.connect(api_key=api_key, audience_id=form.cleaned_data["audience_id"], actor=request.user, request=request)
            except mailchimp.MailchimpError as exc:
                form.add_error(None, str(exc))
            else:
                request.session.pop("btw_mailchimp_key", None)
                messages.success(request, "Mailchimp connected. People with marketing email On will sync within a few minutes.")
                return editor_success(request, home)
        return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
            context={"form": form, "page_title": "Choose the Mailchimp audience", "save_label": "Connect", "back": home,
                     "editor_context": "People who said yes to marketing email will be added here.",
                     "fields_template": "messaging/audience_fields.html"})
    form = MailchimpKeyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        api_key = form.cleaned_data["api_key"].strip()
        try:
            options = mailchimp.audiences(api_key)
        except mailchimp.MailchimpError as exc:
            form.add_error("api_key", str(exc))
        else:
            if not options:
                form.add_error("api_key", "That account has no audiences yet. Create one in Mailchimp first.")
            else:
                from . import crypto
                request.session["btw_mailchimp_key"] = crypto.encrypt(api_key, "mailchimp-pending")
                audience = MailchimpAudienceForm(initial={"audience_id": options[0][0]})
                audience.fields["audience_id"].choices = [(i, f"{n} ({c} contacts)") for i, n, c in options]
                return editor_response(request, template="messaging/connection_edit_page.html",
                    panel="messaging/connection_edit_panel.html", context={
                        "form": audience, "page_title": "Choose the Mailchimp audience", "save_label": "Connect",
                        "back": home, "editor_context": "People who said yes to marketing email will be added here.",
                        "fields_template": "messaging/audience_fields.html"})
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
        context={"form": form, "page_title": "Connect Mailchimp", "save_label": "Next", "back": home,
                 "editor_context": "The key is stored encrypted and never shown again.",
                 "fields_template": "internal/ui/form_fields.html"})


@login_required(login_url="/admin/login/")
@require_POST
def mailchimp_sync_now(request):
    _admin(request)
    if request.POST.get("full") == "1":
        mailchimp.mark_all_for_sync()
    try:
        done = mailchimp.sync_pending(limit=500)
    except mailchimp.MailchimpError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Synced {done} contact(s) with Mailchimp.")
    return redirect(reverse("messaging:connections") + "#mailchimp")


@login_required(login_url="/admin/login/")
@require_POST
def mailchimp_disconnect(request):
    _admin(request)
    from . import store
    store.save(key="mailchimp", values={}, actor=request.user, new_secret="")
    messages.success(request, "Mailchimp disconnected. Nothing more will sync. Contacts already in Mailchimp stay there.")
    return redirect(reverse("messaging:connections") + "#mailchimp")


@csrf_exempt
@require_http_methods(["GET", "POST"])
def mailchimp_webhook(request, secret):
    row = mailchimp.settings_row()
    expected = (row.values.get("webhook_secret") if row else "") or ""
    if not expected or not hmac.compare_digest(expected, secret):
        return HttpResponseNotFound()
    if request.method == "GET":
        return HttpResponse("ok")  # Mailchimp checks the address before saving the webhook.
    mailchimp.handle_webhook(request.POST)
    return HttpResponse("ok")


@login_required(login_url="/admin/login/")
@require_POST
def bounce_clear(request, participant_id):
    """Staff say a bounced address is fixed (or worth trying again)."""
    from apps.crm import access as crm_access
    from apps.participants.models import Participant
    from .bounces import clear
    participant = get_object_or_404(Participant, pk=participant_id)
    user = crm_access.internal_context(request.user, participant=participant, write=True)
    clear(participant.contact_email, user)
    messages.success(request, "Automatic emails to this address will be sent again.")
    return redirect(reverse("participants:staff-profile", args=[participant.pk]))
