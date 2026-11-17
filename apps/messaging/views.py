"""Connect your Gmail, and email a participant from your own address."""
import logging
import uuid

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMessage  # noqa: F401
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.roles import active_staff
from apps.internal_ui.record_editors import editor_response, editor_success, local_return
from apps.participants.models import Participant

from . import google, signatures
from .models import GmailConnection, SentEmail

SESSION_STATE = "btw_gmail_oauth_state"
SESSION_NEXT = "btw_gmail_oauth_next"


def _staff(request):
    if not active_staff(request.user):
        raise PermissionDenied


log = logging.getLogger(__name__)


def _callback_uri(request):
    return request.build_absolute_uri(reverse("messaging:gmail-callback"))


def _next(request, value):
    mine = reverse("my:schedule")
    return local_return(value or "", fallback=mine, allowed={mine: set()}) if not (value or "").startswith("/participants/") else value.split("#")[0][:300]


@login_required(login_url="/admin/login/")
@require_POST
def gmail_connect(request):
    _staff(request)
    if not google.configured():
        messages.error(request, "Gmail connection isn't set up on this site yet.")
        return redirect(_next(request, request.POST.get("next")))
    state = google.new_state()
    request.session[SESSION_STATE] = state
    request.session[SESSION_NEXT] = _next(request, request.POST.get("next"))
    hint = getattr(request.user, "email", "") or ""
    from .email_replies import site_enabled
    return redirect(google.authorization_url(redirect_uri=_callback_uri(request), state=state, login_hint=hint,
                                             read=site_enabled()))


@login_required(login_url="/admin/login/")
@require_POST
def gmail_replies_toggle(request):
    """A staff member turns reply copying on or off for their own Gmail."""
    _staff(request)
    connection = GmailConnection.objects.filter(user=request.user).first()
    if connection:
        connection.track_replies = request.POST.get("on") == "1"
        connection.save(update_fields=["track_replies"])
        messages.success(request, "Replies will be copied to the person's activity." if connection.track_replies
                         else "Replies will stay in your Gmail only.")
    return redirect(_next(request, request.POST.get("next")))


@login_required(login_url="/admin/login/")
@require_GET
def gmail_callback(request):
    _staff(request)
    expected = request.session.pop(SESSION_STATE, None)
    destination = request.session.pop(SESSION_NEXT, None) or reverse("my:schedule")
    if not expected or request.GET.get("state") != expected:
        log.warning("GMAIL_CONNECT_FAILED user=%s reason=state_mismatch had_state=%s", request.user.pk, bool(expected))
        messages.error(request, "Gmail was not connected: that Google sign-in didn't match. Please try connecting again.")
        return redirect(destination)
    if request.GET.get("error") or not request.GET.get("code"):
        log.warning("GMAIL_CONNECT_FAILED user=%s reason=%s", request.user.pk, request.GET.get("error", "no_code")[:60])
        messages.error(request, "Gmail was not connected: Google sign-in was cancelled or refused.")
        return redirect(destination)
    try:
        email, refresh, scopes = google.exchange_code(code=request.GET["code"], redirect_uri=_callback_uri(request))
    except google.GmailError as exc:
        log.warning("GMAIL_CONNECT_FAILED user=%s reason=%s redirect=%s", request.user.pk, str(exc)[:120], _callback_uri(request))
        messages.error(request, f"Gmail was not connected: {exc}.")
        return redirect(destination)
    GmailConnection.objects.update_or_create(user=request.user, defaults={
        "google_email": email, "refresh_token_encrypted": google.encrypt(refresh), "scopes": scopes[:500],
        "needs_reconnect": False, "last_error": "", "connected_at": timezone.now()})
    messages.success(request, f"Gmail connected. Emails you send from the app will come from {email}.")
    return redirect(destination)


@login_required(login_url="/admin/login/")
@require_POST
def gmail_disconnect(request):
    _staff(request)
    connection = GmailConnection.objects.filter(user=request.user).first()
    if connection:
        google.revoke(connection)
        connection.delete()
        messages.success(request, "Gmail disconnected. The app can no longer send email as you.")
    return redirect(_next(request, request.POST.get("next")))


class ParticipantEmailForm(forms.Form):
    operation_id = forms.UUIDField(widget=forms.HiddenInput)
    subject = forms.CharField(max_length=200)
    body = forms.CharField(label="Message", max_length=6000, widget=forms.Textarea(attrs={"rows": 8}))


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def email_participant(request, participant_id):
    _staff(request)
    from apps.crm import access as crm_access
    participant = get_object_or_404(Participant, pk=participant_id, archived_at__isnull=True)
    try:
        crm_access.internal_context(request.user, participant=participant, write=True)
    except PermissionDenied:
        raise
    profile = reverse("participants:staff-profile", args=[participant.pk])
    back = profile + "?section=activity"
    connection = GmailConnection.objects.filter(user=request.user).first()
    form = ParticipantEmailForm(request.POST or None, initial={"operation_id": uuid.uuid4()})
    ready = google.configured() and connection is not None and connection.active and bool(participant.contact_email)
    context = {"form": form, "participant": participant, "connection": connection, "ready": ready,
               "gmail_configured": google.configured(), "back": back, "page_title": "Email " + participant.first_name,
               "editor_context": f"To {participant.contact_email}" if participant.contact_email else "No email address on file",
               "save_label": "Send email", "fields_template": "messaging/email_fields.html",
               "has_signature": bool(signatures.personal(request.user)),
               "profile_url": profile}
    if request.method == "POST" and ready and form.is_valid():
        try:
            outcome = _send_participant_email(request, connection, participant, form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc.messages[0] if exc.messages else "The email was not sent.")
        else:
            messages.success(request, outcome)
            return editor_success(request, back)
    elif request.method == "POST" and not ready:
        form.is_valid()
        form.add_error(None, "Connect your Gmail before sending." if participant.contact_email else "This person has no email address on file.")
    return editor_response(request, template="messaging/email_page.html", context=context, panel="messaging/email_panel.html")


def _send_participant_email(request, connection, participant, data):
    sender_name = request.user.get_full_name() or request.user.get_username()
    try:
        with transaction.atomic():
            row = SentEmail.objects.create(operation_id=data["operation_id"], sender=request.user,
                from_address=connection.google_email, participant=participant, to_address=participant.contact_email,
                subject=data["subject"])
    except IntegrityError:
        existing = SentEmail.objects.filter(operation_id=data["operation_id"], sender=request.user).first()
        if existing and existing.status == SentEmail.Status.SENT:
            return "That email was already sent."
        raise ValidationError("This email is already being sent. Check the person's activity before trying again.")
    from .signatures import build, personal
    message = build(data["subject"], data["body"], f"{sender_name} <{connection.google_email}>", [participant.contact_email],
                    signature_html=personal(request.user))
    try:
        sent_info = {}
        reference = google.send(connection, message, result=sent_info)
    except google.GmailError as exc:
        row.status = SentEmail.Status.UNCERTAIN if exc.uncertain else SentEmail.Status.FAILED
        row.error = str(exc)[:240]
        row.save(update_fields=["status", "error"])
        if exc.reconnect:
            connection.needs_reconnect = True
            connection.last_error = str(exc)[:240]
            connection.save(update_fields=["needs_reconnect", "last_error"])
        if exc.uncertain:
            raise ValidationError("Gmail didn't confirm the send. Check your Gmail Sent folder before trying again.")
        raise ValidationError(f"The email was not sent: {exc}.")
    row.status, row.provider_reference, row.sent_at = SentEmail.Status.SENT, reference[:160], timezone.now()
    row.thread_id = (sent_info.get("thread_id") or "")[:80]
    row.save(update_fields=["status", "provider_reference", "sent_at", "thread_id"])
    from apps.crm.services import record_app_message
    try:
        interaction = record_app_message(actor=request.user, participant_id=participant.pk, activity_type="email",
            summary=data["subject"], notes=data["body"], provider="gmail", reference=f"gmail:{reference}",
            operation_id=row.operation_id)
        row.interaction = interaction
        row.save(update_fields=["interaction"])
    except Exception:  # The email went; a history failure must not suggest otherwise.
        return f"Email sent from {connection.google_email}. It could not be added to the activity history."
    return f"Email sent from {connection.google_email}."
