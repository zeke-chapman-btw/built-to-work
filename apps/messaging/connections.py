"""Settings → Connections: shared texting and email sign-ins, automatic sending, staff Gmail."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMessage
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.models import AuditLog
from apps.events import sms_providers
from apps.internal_ui.record_editors import editor_response, editor_success

from . import google, store
from .models import ConnectionSetting, GmailConnection


def _admin(request):
    user = request.user
    if not (user.is_authenticated and user.is_active and user.is_staff and user.is_superuser):
        raise PermissionDenied("Connections are managed by an administrator.")


def _home():
    return reverse("messaging:connections")


def _status(row, configured):
    if not configured:
        return ("Not set up", "")
    if row and row.last_test_ok is False:
        return ("Sign-in failing", "r")
    if row and row.last_test_ok:
        return ("Connected", "g")
    return ("Saved · not tested", "y")


def _queue():
    from apps.events.models import OperationalNotificationDelivery, TicketDelivery
    since = timezone.now() - timedelta(days=1)
    counts = {"waiting": 0, "sent": 0, "failed": 0}
    for model in (TicketDelivery, OperationalNotificationDelivery):
        counts["waiting"] += model.objects.filter(status="queued").count()
        counts["sent"] += model.objects.filter(status="sent", sent_at__gte=since).count()
        counts["failed"] += model.objects.filter(status__in=("failed", "uncertain"), created_at__gte=since).count()
    return counts


@login_required(login_url="/admin/login/")
def connections(request):
    _admin(request)
    rows = {row.key: row for row in ConnectionSetting.objects.select_related("updated_by")}
    ez_user, _pw, ez_source = sms_providers.ez_credentials()
    email = store.email_settings()
    since = store.automatic_sending_since()
    history = (AuditLog.objects.filter(source="connections").select_related("actor").order_by("-timestamp")[:20])
    context = {
        "page_title": "Settings",
        "text": {"row": rows.get("eztexting"), "username": ez_user, "source": ez_source,
                 "status": _status(rows.get("eztexting"), bool(ez_user))},
        "email": {"row": rows.get("email"), "config": email, "bounces": _bounce_count(),
                  "status": _status(rows.get("email"), bool(email["source"]) and bool(email["from_address"]))},
        "sending": {"row": rows.get("sending"), "since": since, "queue": _queue()},
        "gmail": {"configured": google.configured(),
                  "people": GmailConnection.objects.select_related("user").order_by("user__first_name", "user__username")},
        "history": history,
        "mailchimp": _mailchimp_summary(rows.get("mailchimp")),
        "replies": _replies_summary(rows.get("replies")),
        "email_replies": _email_replies_summary(),
        "company_signature": _company_signature(),
        "statement_notices": _statement_summary(),
        "culture_index": _culture_index_summary(),
    }
    context["apps"] = _tiles(context)
    return render(request, "messaging/connections.html", context)


def _on(flag, on="On", off="Off"):
    return (on, "g") if flag else (off, "")


def _stripe_status():
    try:
        from apps.billing.models import StripeSettings
        row = StripeSettings.current()
    except Exception:
        return ("Not set up", "")
    if not row.secret_key_encrypted:
        return ("Not set up", "")
    return {True: ("Connected", "g"), False: ("Sign-in failing", "r")}.get(row.last_test_ok, ("Saved · not tested", "y"))


def _tiles(c):
    """The app tiles at the top of Connections. Each opens its own panel (or page) to change it."""
    gmail_count = sum(1 for p in c["gmail"]["people"] if p.active)
    groups = [
        ("Apps", "Outside services Built to Work signs in to.", [
            ("texting", "EZ Texting", "Sends and receives texts", c["text"]["status"], "chat", "blue"),
            ("email", "Automatic email", "noreply@ sends tickets and alerts", c["email"]["status"], "mail", "teal"),
            ("gmail-people", "Staff Gmail", "Staff email from their own address",
             (f"{gmail_count} connected", "g") if gmail_count else (("Ready", "y") if c["gmail"]["configured"] else ("Google project needed", "y")),
             "at", "red"),
            ("mailchimp", "Mailchimp", "Marketing email audience", c["mailchimp"]["status"], "megaphone", "amber"),
            ("culture-index", "Culture Index", "Survey link and results", c["culture_index"]["status"], "compass", "green"),
            ("stripe", "Stripe", "Customer billing and bank payments", _stripe_status(), "card", "indigo"),
        ]),
        ("Settings", "How the app uses those apps.", [
            ("sending", "Automatic messages", "Master switch for ticket texts, emails and alerts", _on(c["sending"]["since"]), "bolt", "violet"),
            ("replies", "Text replies", "Uses EZ Texting · replies come into the Inbox", _on(c["replies"]["forwarding"]), "reply", "blue"),
            ("email-replies", "Email replies", "Uses Staff Gmail · replies copied to activity", _on(c["email_replies"]["enabled"]), "inbox", "red"),
            ("signature", "Company signature", "Added to automatic emails", _on(c["company_signature"], "Set", "Not set"), "pen", "slate"),
            ("statements", "Pay statement emails", "Uses automatic email · tells staff a statement is ready", _on(c["statement_notices"]["on"]), "receipt", "slate"),
            ("templates", "Message wording", "Saved texts and emails staff pick from", ("Edit", ""), "pen", "slate"),
        ]),
    ]
    out = []
    for label, note, items in groups:
        out.append({"label": label, "note": note, "items": [
            {"key": key, "name": name, "purpose": purpose, "status": status, "icon": icon, "tone": tone,
             "url": {"stripe": reverse("billing:settings"), "templates": reverse("messaging:templates")}.get(key, f"#{key}"),
             "page": key in ("stripe", "templates")}
            for key, name, purpose, status, icon, tone in items]})
    return out


def _bounce_count():
    from .models import EmailBounce
    return EmailBounce.objects.filter(cleared_at__isnull=True).count()


def _statement_summary():
    from .models import ConnectionSetting, StatementNotice
    row = ConnectionSetting.objects.filter(key="statement_notices").first()
    return {"on": bool(row and row.values.get("enabled")),
            "sent": StatementNotice.objects.filter(status="sent").count(),
            "last": StatementNotice.objects.order_by("-created_at").first()}


@login_required(login_url="/admin/login/")
@require_POST
def statement_notices_toggle(request):
    _admin(request)
    from .statements import set_enabled
    on = request.POST.get("on") == "1"
    set_enabled(actor=request.user, on=on)
    messages.success(request, "Staff will get an email when a new earnings statement is ready." if on
                     else "Pay statement emails turned off.")
    return redirect(_home() + "#statements")


def _company_signature():
    from .signatures import company
    return company()


def _email_replies_summary():
    from .email_replies import site_enabled
    from .models import GmailConnection
    connected = list(GmailConnection.objects.exclude(refresh_token_encrypted=""))
    return {"enabled": site_enabled(), "connected": len(connected),
            "reading": sum(1 for c in connected if c.can_read and c.track_replies and not c.needs_reconnect)}


@login_required(login_url="/admin/login/")
@require_POST
def email_replies_toggle(request):
    _admin(request)
    from .email_replies import set_enabled
    on = request.POST.get("on") == "1"
    set_enabled(actor=request.user, enabled=on)
    messages.success(request, "Email replies turned on. Staff reconnect their Gmail once to start." if on
                     else "Email replies turned off. No Gmail mailboxes will be checked.")
    return redirect(_home() + "#email-replies")


def _replies_summary(row):
    from . import texting
    from .text_views import staff_choices
    values = row.values if row else {}
    owner = texting.default_owner()
    return {"row": row, "forwarding": bool(values.get("forwarding")), "owner": owner,
            "owner_id": str(owner.pk) if owner else "", "staff": staff_choices(include_blank=True)}


@login_required(login_url="/admin/login/")
@require_POST
def replies_setup(request):
    """Turn on reply forwarding from EZ Texting, and/or choose who gets unmatched replies."""
    _admin(request)
    from django.contrib.auth import get_user_model
    from . import texting
    if "owner" in request.POST:
        owner_id = request.POST.get("owner") or ""
        owner = get_user_model().objects.filter(pk=owner_id, is_active=True, is_staff=True).first() if owner_id else None
        texting.set_owner(actor=request.user, owner=owner)
        messages.success(request, "Saved who gets replies that don't belong to anyone.")
    if request.POST.get("forward") == "on":
        try:
            texting.turn_on_replies(actor=request.user, callback_url=lambda token: request.build_absolute_uri(
                reverse("messaging:eztexting-webhook", args=[token])))
        except texting.TextError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Replies are on. Texts people send back will now show up in the Inbox.")
    return redirect(_home() + "#replies")


def _mailchimp_summary(row):
    from .models import MarketingConsent
    from . import mailchimp
    consents = MarketingConsent.objects
    connected = mailchimp.connected()
    status = ("Not set up", "") if not connected else (
        ("Problem", "r") if consents.exclude(sync_error="").exists() or (row and row.values.get("webhook_ok") is False)
        else ("Connected", "g"))
    return {"row": row, "connected": connected, "status": status,
            "opted_in": consents.filter(status="on").count(),
            "in_mailchimp": consents.exclude(synced_email="").count(),
            "waiting": consents.filter(needs_sync=True).count() if connected else 0,
            "errors": consents.exclude(sync_error="").count(),
            "unsubscribed": consents.filter(status__in=("unsubscribed", "bounced")).count()}


class TextForm(forms.Form):
    username = forms.CharField(label="EZ Texting username", max_length=150)
    password = forms.CharField(label="Password", required=False, widget=forms.PasswordInput(render_value=False),
                               help_text="Leave blank to keep the saved password.")


class EmailForm(forms.Form):
    from_address = forms.CharField(label="Send from", max_length=200, help_text='For example: Built to Work <noreply@built2work.com>')
    reply_to = forms.EmailField(label="Replies go to", required=False, help_text="Optional: a watched inbox for anyone who hits Reply.")
    host = forms.CharField(label="Mail server", max_length=200, initial="smtp-relay.gmail.com")
    port = forms.IntegerField(label="Port", initial=587, min_value=1, max_value=65535)
    username = forms.CharField(label="Sign-in (Google account)", max_length=200)
    password = forms.CharField(label="App password", required=False, widget=forms.PasswordInput(render_value=False),
                               help_text="Leave blank to keep the saved password.")


def _editor(request, *, form, title, context_line, save_label):
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html", context={
        "form": form, "page_title": title, "editor_context": context_line, "save_label": save_label,
        "back": _home(), "fields_template": "internal/ui/form_fields.html"})


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def edit_text(request):
    _admin(request)
    row = ConnectionSetting.objects.filter(key="eztexting").first()
    initial = {"username": (row.values.get("username") if row else "") or sms_providers.ez_credentials()[0]}
    form = TextForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        password = form.cleaned_data["password"]
        if not password and not (row and row.secret_encrypted):
            form.add_error("password", "Enter the password.")
        else:
            store.save(key="eztexting", values={"username": form.cleaned_data["username"].strip()}, actor=request.user,
                       new_secret=password or None)
            messages.success(request, "Texting sign-in saved. Send a test text to check it.")
            return editor_success(request, _home())
    return _editor(request, form=form, title="Texting sign-in", save_label="Save",
                   context_line="The EZ Texting login the app uses to send texts. The password is stored encrypted and never shown again.")


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def edit_email(request):
    _admin(request)
    row = ConnectionSetting.objects.filter(key="email").first()
    current = store.email_settings()
    values = row.values if row else {}
    initial = {"from_address": values.get("from_address") or current["from_address"],
               "reply_to": values.get("reply_to") or (current["reply_to"][0] if current["reply_to"] else ""),
               "host": values.get("host") or current.get("host") or "smtp-relay.gmail.com",
               "port": values.get("port") or current.get("port") or 587,
               "username": values.get("username") or current.get("username", "")}
    form = EmailForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        password = data["password"]
        if not password and not (row and row.secret_encrypted):
            form.add_error("password", "Enter the app password.")
        else:
            store.save(key="email", actor=request.user, new_secret=password or None, values={
                "from_address": data["from_address"].strip(), "reply_to": data["reply_to"], "host": data["host"].strip(),
                "port": data["port"], "username": data["username"].strip()})
            messages.success(request, "Email sign-in saved. Send a test email to check it.")
            return editor_success(request, _home())
    return _editor(request, form=form, title="Automatic email", save_label="Save",
                   context_line="The address and Google sign-in the app uses for tickets, invitations and password resets.")


@login_required(login_url="/admin/login/")
@require_POST
def test_text(request):
    _admin(request)
    number = request.POST.get("number", "")
    try:
        reference = sms_providers.send_sms(number, "Built to Work test text. No reply needed.", idempotency_key="connections-test")
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as exc:
        store.record_test("eztexting", False, str(exc))
        messages.error(request, f"Test text failed: {exc}")
    else:
        store.record_test("eztexting", True, f"Accepted by EZ Texting ({reference})")
        messages.success(request, "Test text accepted by EZ Texting. Check the phone to confirm it arrived.")
    return redirect(_home())


@login_required(login_url="/admin/login/")
@require_POST
def test_email(request):
    _admin(request)
    address = request.POST.get("address", "").strip()
    config = store.email_settings()
    connection = store.smtp_connection()
    if connection is None or not config["from_address"]:
        messages.error(request, "Save the email sign-in first.")
        return redirect(_home())
    try:
        forms.EmailField().clean(address)
        sent = EmailMessage("Built to Work test email", "This is a test from the Built to Work app. No action needed.",
                            config["from_address"], [address], connection=connection,
                            reply_to=config["reply_to"] or None).send(fail_silently=False)
        if sent != 1:
            raise ConnectionError("The mail server did not accept it")
    except forms.ValidationError:
        messages.error(request, "Enter a valid email address for the test.")
        return redirect(_home())
    except Exception as exc:  # SMTP errors vary; report plainly without secrets.
        store.record_test("email", False, type(exc).__name__)
        messages.error(request, f"Test email failed ({type(exc).__name__}). Check the sign-in and the Google relay setting.")
    else:
        store.record_test("email", True, "Accepted by the mail server")
        messages.success(request, f"Test email accepted by the mail server. Check {address}.")
    return redirect(_home())


@login_required(login_url="/admin/login/")
@require_POST
def toggle_sending(request):
    _admin(request)
    turn_on = request.POST.get("enabled") == "on"
    values = {"enabled": turn_on, "since": timezone.now().isoformat() if turn_on else ""}
    store.save(key="sending", values=values, actor=request.user)
    messages.success(request, "Automatic messages are on. New ticket texts and emails will go out within a minute."
                     if turn_on else "Automatic messages are off. New messages will wait until they're turned back on.")
    return redirect(_home())


class CultureIndexForm(forms.Form):
    survey_link = forms.URLField(label="Survey link", max_length=500,
                                 help_text="The one link everyone takes. Staff send it from a participant's profile.")
    portal = forms.BooleanField(label="Show the survey in the participant portal", required=False)
    api_key = forms.CharField(label="API key", max_length=200, required=False, widget=forms.PasswordInput(render_value=False),
                              help_text="From Culture Index. Leave blank to keep the saved key. Stored encrypted and never shown again.")
    remove_key = forms.BooleanField(label="Remove the saved API key", required=False)


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def edit_culture_index(request):
    from . import cultureindex
    _admin(request)
    row = cultureindex.row()
    values = dict(row.values) if row else {}
    form = CultureIndexForm(request.POST or None, initial={"survey_link": cultureindex.survey_link(),
                                                          "portal": values.get("portal", True)})
    if not (row and row.secret_encrypted):
        del form.fields["remove_key"]
    if request.method == "POST" and form.is_valid():
        key = form.cleaned_data["api_key"].strip()
        problem = ""
        if key:
            try:
                cultureindex.call(values.get("list_path") or cultureindex.DEFAULT_PATH, key=key)
            except cultureindex.CultureIndexError as exc:
                problem = str(exc)
        if not form.errors:
            values.update(survey_link=form.cleaned_data["survey_link"].strip(), portal=form.cleaned_data["portal"])
            if key:
                values.update(last_error=problem)
            new_secret = key or ("" if form.cleaned_data.get("remove_key") else None)
            store.save(key=cultureindex.KEY, values=values, actor=request.user, new_secret=new_secret)
            if problem:
                messages.warning(request, f"Culture Index key saved, but the check didn't work: {problem} "
                                          "Use \"Copy results now\" to try again.")
            else:
                messages.success(request, "Culture Index saved." + (" The key works; results will copy in tonight." if key else ""))
            return editor_success(request, _home() + "#culture-index")
    return _editor(request, form=form, title="Culture Index", save_label="Save",
                   context_line="Everyone takes the same survey link. Once the API key is saved, finished surveys are copied in each night and matched by email.")


@login_required(login_url="/admin/login/")
@require_POST
def culture_index_sync(request):
    from . import cultureindex
    _admin(request)
    try:
        outcome = cultureindex.sync()
    except cultureindex.CultureIndexError as exc:
        row = cultureindex.row()
        if row:
            row.values = dict(row.values, last_error=str(exc))
            row.save(update_fields=["values"])
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Checked {outcome['seen']} survey(s) in Culture Index: {outcome['matched']} belong to our participants "
                                  f"({outcome['added']} new). The rest were skipped.")
    return redirect(_home() + "#culture-index")


def _culture_index_summary():
    from . import cultureindex
    from .models import CultureIndexResult
    row = cultureindex.row()
    values = row.values if row else {}
    connected = bool(row and row.secret_encrypted)
    if not connected:
        status = ("Link ready · no API key", "y")
    elif values.get("last_error") or row.last_test_ok is False:
        status = ("Key not working", "r")
    else:
        status = ("Connected", "g")
    return {"row": row, "link": cultureindex.survey_link(), "portal": values.get("portal", True), "connected": connected,
            "status": status, "last_sync": cultureindex._when(values.get("last_sync")), "error": values.get("last_error", ""),
            "results": CultureIndexResult.objects.count(),
            "recent": CultureIndexResult.objects.select_related("participant").order_by("-taken_at", "-pk")[:10],
            "matched": CultureIndexResult.objects.filter(participant__isnull=False).count()}
