"""Settings → Reminders: the list, the pop-up editor, roles and the master switch."""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.roles import manager
from apps.internal_ui.record_editors import editor_response, editor_success

from . import engine, sources
from .models import ReminderNotice, ReminderPreference, ReminderRole, ReminderRule, ReminderSetup

AREAS = ("Tasks", "Events", "Participants", "Recruiting", "Customers", "Billing", "Payroll", "Admin", "Other")
BUILT_IN = {
    "tasks": ("Each person can change theirs in My stuff → Reminders.", "operations:reminder-preferences"),
    "events": ("Company-wide timing; open an event to change it for that event.", "operations:reminder-company"),
    "tickets": ("Sent automatically when someone signs up for an event, once Automatic messages are on.", "messaging:connections"),
    "statements": ("Turned on or off in Settings → Connections → Pay statement emails.", "messaging:connections"),
}


def _manager(request):
    if not manager(request.user):
        raise PermissionDenied("Reminders are managed by managers and administrators.")


def staff_users():
    return get_user_model().objects.filter(is_active=True, is_staff=True).order_by("first_name", "last_name", "username")


def _name(user):
    return user.get_full_name() or user.get_username()


def timing(rule):
    """Plain words: "2 and 1 days before, at 6:00 PM"."""
    if rule.built_in:
        return rule.description
    days = rule.day_list()
    unit = "business days" if rule.business_days else "days"
    at = rule.send_time.strftime("%I:%M %p").lstrip("0")
    source = sources.SOURCES.get(rule.source)
    anchor = source.anchor_label if source else "the date"
    if rule.mode == "changed":
        text = "As soon as it changes"
    elif rule.mode == "new":
        text = "As soon as it comes in"
        if rule.repeat_every:
            text += f", then {_every(rule.repeat_every)} at {at} until handled"
    elif rule.mode == "before":
        if days == [0] or not days:
            text = f"On {anchor}, at {at}"
        else:
            listed = " and ".join(str(d) for d in days)
            text = f"{listed} {unit if max(days) != 1 or len(days) > 1 else unit[:-1]} before {anchor}, at {at}"
    else:
        d = min(days) if days else 0
        text = (f"{d} {unit if d != 1 else unit[:-1]} after {anchor}" if d else
                anchor[0].upper() + anchor[1:] if anchor.startswith("each") else f"On {anchor}") + f", at {at}"
        if rule.repeat_every:
            text += f", then {_every(rule.repeat_every)} until handled"
    if rule.weekdays_only:
        text += " (weekdays)"
    return text


def _every(n):
    return "daily" if n == 1 else "weekly" if n == 7 else f"every {n} days"


@login_required(login_url="/admin/login/")
def reminder_list(request):
    _manager(request)
    roles = {r.key: r for r in ReminderRole.objects.prefetch_related("holders")}
    groups, rules = {}, list(ReminderRule.objects.all())
    for rule in rules:
        who = engine.describe_recipients(rule, roles)
        groups.setdefault(rule.area if rule.area in AREAS else "Other", []).append(
            {"rule": rule, "timing": timing(rule), "who": who, "built_in": BUILT_IN.get(rule.built_in, ("", ""))[0]})
    role_rows = []
    for role in roles.values():
        holders = [_name(u) for u in role.holders.all() if u.is_active]
        auto = None if holders or not role.waiting_for else engine.waiting_user(role.waiting_for)
        role_rows.append({"role": role, "holders": holders, "auto": _name(auto) if auto else "",
                          "used": sum(1 for r in rules if role.key in (r.recipients or {}).get("roles", []))})
    return render(request, "reminders/list.html", {
        "page_title": "Settings", "setup": ReminderSetup.current(), "roles": role_rows,
        "groups": [(area, groups[area]) for area in AREAS if area in groups],
        "sources": [(s.area, s.key, s.label) for s in sources.SOURCES.values()],
    })


@login_required(login_url="/admin/login/")
@require_POST
def switch(request):
    _manager(request)
    setup = ReminderSetup.current()
    turn_on = request.POST.get("on") == "1"
    if turn_on and not setup.is_on:
        setup.turned_on_at = timezone.now()
    setup.is_on, setup.updated_by = turn_on, request.user
    setup.save()
    messages.success(request, "Reminders are on." if turn_on else "Reminders are paused. Nothing new will go out.")
    return redirect("reminders:list")


class RuleForm(forms.ModelForm):
    related = forms.MultipleChoiceField(label="People connected to it", required=False, widget=forms.CheckboxSelectMultiple)
    roles = forms.MultipleChoiceField(label="Roles", required=False, widget=forms.CheckboxSelectMultiple,
                                      help_text="Change who holds a role on the Reminders page.")
    people = forms.MultipleChoiceField(label="Specific people", required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = ReminderRule
        fields = ("enabled", "name", "area", "mode", "days", "business_days", "repeat_every", "send_time", "weekdays_only",
                  "bell", "email", "text", "backup", "subject", "message")
        labels = {"enabled": "On", "name": "Name", "area": "Group", "mode": "When it goes out", "days": "How many days",
                  "business_days": "Count business days only (skip weekends)", "repeat_every": "Repeat every (days)",
                  "send_time": "Time of day (Eastern)", "weekdays_only": "Weekdays only",
                  "bell": "Bell (in the app)", "email": "Email", "text": "Text",
                  "backup": "If no one above can get it, send to", "subject": "Title", "message": "Message"}
        help_texts = {"days": "One or more numbers, like 7, 2 for 7 days and 2 days.",
                      "repeat_every": "0 = only once. 1 = daily until it's handled. 7 = weekly.",
                      "subject": "Email subject and the line shown in the app.",
                      "message": "Texts over 160 characters go as more than one text."}
        widgets = {"send_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
                   "message": forms.Textarea(attrs={"rows": 4}),
                   "area": forms.Select(choices=[(a, a) for a in AREAS])}

    def __init__(self, *args, source=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source = source
        rule = self.instance
        self.fields["mode"].choices = [(m, label) for m, label in ReminderRule.Mode.choices if m in source.modes]
        self.fields["related"].choices = list(source.related.items())
        self.fields["roles"].choices = [(r.key, r.name) for r in ReminderRole.objects.all()]
        self.fields["people"].choices = [(str(u.pk), _name(u)) for u in staff_users()]
        spec = rule.recipients or {}
        self.initial.setdefault("related", spec.get("related", []))
        self.initial.setdefault("roles", spec.get("roles", []))
        self.initial.setdefault("people", [str(p) for p in spec.get("people", [])])
        if not source.related:
            del self.fields["related"]
        fill_ins = ", ".join("{" + k + "} = " + v for k, v in {**source.fields, "line": "a one-line summary"}.items())
        self.fields["message"].help_text += " Fill-ins: " + fill_ins + "."
        if len(self.fields["mode"].choices) == 1:
            self.fields["mode"].widget = forms.HiddenInput()
        saved = rule.conditions or {}
        for key, label, kind, extra in getattr(source, "filters", ()):
            name = "cond_" + key
            if kind == "choices":
                self.fields[name] = forms.MultipleChoiceField(label=label, choices=extra, required=False,
                                                              widget=forms.CheckboxSelectMultiple, help_text="Leave all unticked for any.")
            elif kind == "words":
                self.fields[name] = forms.CharField(label=label, required=False, max_length=300, help_text=extra)
            else:
                self.fields[name] = forms.BooleanField(label=label, required=False)
            self.initial.setdefault(name, saved.get(key, [] if kind == "choices" else False if kind == "flag" else ""))

    def condition_fields(self):
        return [self[name] for name in self.fields if name.startswith("cond_")]

    def clean_days(self):
        value = self.cleaned_data.get("days", "")
        parts = [p.strip() for p in value.split(",") if p.strip()]
        if any(not p.isdigit() or int(p) > 90 for p in parts):
            raise forms.ValidationError("Use whole numbers from 0 to 90, separated by commas.")
        return ", ".join(parts)

    def clean(self):
        data = super().clean()
        if not any(data.get(c) for c in ("bell", "email", "text")):
            raise forms.ValidationError("Pick at least one: Bell, Email or Text.")
        if not (data.get("related") or data.get("roles") or data.get("people")):
            raise forms.ValidationError("Pick who gets it: people connected to it, a role, or specific people.")
        if data.get("text") and not data.get("message", "").strip():
            self.add_error("message", "Texts need a message.")
        return data

    def save(self, commit=True):
        rule = super().save(commit=False)
        rule.recipients = {k: list(self.cleaned_data.get(k) or []) for k in ("related", "roles", "people")
                           if self.cleaned_data.get(k)}
        rule.conditions = {name[5:]: value for name, value in self.cleaned_data.items()
                           if name.startswith("cond_") and value not in ("", [], False, None)}
        if commit:
            rule.save()
        return rule


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def rule_edit(request, pk=None):
    _manager(request)
    back = reverse("reminders:list")
    if pk:
        rule = get_object_or_404(ReminderRule, pk=pk)
    else:
        key = request.GET.get("source") or request.POST.get("source") or ""
        if key not in sources.SOURCES:
            raise Http404
        s = sources.SOURCES[key]
        rule = ReminderRule(source=key, area=s.area, mode=s.modes[0], name=s.label, backup="admins",
                            recipients={"related": list(s.related)[:1]} if s.related else {})
    if rule.built_in:
        return _built_in(request, rule, back)
    source = sources.SOURCES.get(rule.source)
    if not source:
        raise Http404
    if request.method == "POST" and rule.pk and request.POST.get("remove") == "1":
        rule.delete()
        messages.success(request, "Reminder deleted.")
        return editor_success(request, back)
    form = RuleForm(request.POST or None, instance=rule, source=source)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.updated_by = request.user
        if not item.pk:
            item.created_by = request.user
            base = slugify(item.name)[:50] or "reminder"
            item.key, n = base, 2
            while ReminderRule.objects.filter(key=item.key).exists():
                item.key, n = f"{base}-{n}", n + 1
            item.position = 900
        item.save()
        messages.success(request, f"“{item.name}” saved.")
        return editor_success(request, back)
    preview = engine.preview(rule) if rule.pk or request.method == "GET" else {"count": 0, "items": []}
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
                           context={"form": form, "page_title": rule.name if rule.pk else "New reminder",
                                    "editor_context": f"Watches: {source.area} · {source.label}",
                                    "save_label": "Save reminder", "back": back, "back_label": "Reminders",
                                    "fields_template": "reminders/rule_fields.html", "rule": rule, "source": source,
                                    "preview": preview})


def _built_in(request, rule, back):
    note, url_name = BUILT_IN.get(rule.built_in, ("", ""))
    return editor_response(request, template="reminders/built_in_page.html", panel="reminders/built_in_panel.html",
                           context={"form": forms.Form(), "page_title": rule.name, "editor_context": rule.area, "rule": rule, "note": note,
                                    "link": reverse(url_name) if url_name else "", "back": back})


class RoleForm(forms.ModelForm):
    holders = forms.ModelMultipleChoiceField(queryset=None, required=False, label="Who holds this role",
                                             widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = ReminderRole
        fields = ("name", "description", "holders", "waiting_for")
        labels = {"name": "Role name", "description": "What it covers", "waiting_for": "Not in the app yet"}
        help_texts = {"waiting_for": "Type their full name. Their reminders go to administrators until they're in the "
                                     "app, then switch to them automatically."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["holders"].queryset = staff_users()
        self.fields["holders"].label_from_instance = _name


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def role_edit(request, pk=None):
    _manager(request)
    back = reverse("reminders:list")
    role = get_object_or_404(ReminderRole, pk=pk) if pk else None
    form = RoleForm(request.POST or None, instance=role)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        if not item.pk:
            base = slugify(item.name)[:36] or "role"
            item.key, n = base, 2
            while ReminderRole.objects.filter(key=item.key).exists():
                item.key, n = f"{base}-{n}", n + 1
            item.position = 50
        item.save()
        form.save_m2m()
        messages.success(request, f"“{item.name}” saved.")
        return editor_success(request, back)
    return editor_response(request, template="messaging/connection_edit_page.html", panel="messaging/connection_edit_panel.html",
                           context={"form": form, "page_title": role.name if role else "New role",
                                    "editor_context": "Reminders sent to this role go to whoever holds it.",
                                    "save_label": "Save role", "back": back, "back_label": "Reminders",
                                    "fields_template": "internal/ui/form_fields.html"})


# ------------------------------------------------------------- Inbox tab

def unread_count(user):
    return ReminderNotice.objects.filter(user=user, read_at__isnull=True).count()


@login_required(login_url="/admin/login/")
@require_POST
def notice_read(request, pk=None):
    rows = ReminderNotice.objects.filter(user=request.user, read_at__isnull=True)
    if pk:
        rows = rows.filter(pk=pk)
    rows.update(read_at=timezone.now())
    target = request.POST.get("next") or ""
    return redirect(target if target.startswith("/") else reverse("messaging:texts") + "?tab=reminders")


# ------------------------------------------------------- my notifications

def _reaches(rule, user, roles):
    spec = rule.recipients or {}
    if str(user.pk) in [str(p) for p in spec.get("people", [])]:
        return True
    for key in spec.get("roles", []):
        role = roles.get(key)
        if role and (user in role.holders.all() or (not role.holders.exists() and engine.waiting_user(role.waiting_for) == user)):
            return True
    return bool(spec.get("related")) and rule.source in sources.SOURCES and any(
        k not in ("customer_team", "customer_contact", "participant") for k in spec["related"])


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def mine(request):
    """Each staff member picks how each reminder reaches them."""
    if not request.user.is_staff:
        raise PermissionDenied
    roles = {r.key: r for r in ReminderRole.objects.prefetch_related("holders")}
    rules = [r for r in ReminderRule.objects.filter(enabled=True, built_in="").exclude(source="")
             if _reaches(r, request.user, roles)]
    saved = {p.rule_id: p for p in ReminderPreference.objects.filter(user=request.user)}
    if request.method == "POST":
        for rule in rules:
            picked = {c: request.POST.get(f"r{rule.pk}_{c}") == "1" for c in ("bell", "email", "text")}
            if picked == {c: getattr(rule, c) for c in picked} and rule.pk not in saved:
                continue
            ReminderPreference.objects.update_or_create(user=request.user, rule=rule, defaults=picked)
        messages.success(request, "Your notification choices are saved.")
        return redirect("reminders:mine")
    rows = []
    for rule in rules:
        pref = saved.get(rule.pk)
        rows.append({"rule": rule, "timing": timing(rule),
                     "on": {c: getattr(pref or rule, c) for c in ("bell", "email", "text")}})
    profile = getattr(request.user, "staff_profile", None)
    return render(request, "reminders/mine.html", {"page_title": "My notifications", "rows": rows,
                                                   "has_phone": bool(profile and profile.phone),
                                                   "has_email": bool((profile and profile.email) or request.user.email)})
