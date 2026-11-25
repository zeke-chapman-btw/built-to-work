"""Training: each person's guides (My stuff → Training), the step-by-step walk-through, and the admin page."""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .access import audiences_for, can_see, done_ids, guides_for, summary
from .models import AUDIENCES, STAFF_AUDIENCES, Announcement, Guide, GuideProgress, GuideStep, HandbookSection


def _staff(request):
    if not (request.user.is_active and request.user.is_staff):
        raise PermissionDenied


def _admin(request):
    if not (request.user.is_active and request.user.is_staff and request.user.is_superuser):
        raise PermissionDenied("Training setup is for administrators.")


def steps_payload(guide):
    return [dict(title=s.title, body=s.body, page=s.page, target=s.target,
                 image=static("training/" + s.image) if s.image else "") for s in guide.steps.all()]


# ------------------------------------------------------------------ My stuff → Training

@login_required(login_url="/admin/login/")
@require_GET
def mine(request):
    _staff(request)
    from apps.internal_ui.views import _staff_context
    from apps.staffing.my_views import my_tabs
    audiences = audiences_for(request.user)
    guides = guides_for(audiences)
    done = done_ids(request.user)
    names = dict(AUDIENCES)
    groups = []
    for key in ("event_staff", "recruiter", "manager", "admin"):
        if key not in audiences:
            continue
        rows = [g for g in guides if key in g.audiences and not any(g in grp["guides"] for grp in groups)]
        if rows:
            groups.append({"label": "For " + names[key].lower(), "guides": rows})
    for g in guides:
        g.is_done = g.pk in done
    return render(request, "training/mine.html", _staff_context(
        request, page_title="My stuff", my_tabs=my_tabs(request, "training"), groups=groups,
        progress=summary(request.user)))


def _guide_page(request, guide, template, extra=None):
    done = GuideProgress.objects.filter(user=request.user, guide=guide).first() if request.user.is_authenticated else None
    data = {"guide": guide, "steps": guide.steps.all(), "done": done, "steps_url": reverse("training:steps", args=[guide.slug]),
            "done_url": reverse("training:done", args=[guide.slug])}
    data.update(extra or {})
    return render(request, template, data)


@login_required(login_url="/admin/login/")
@require_GET
def guide(request, slug):
    _staff(request)
    item = get_object_or_404(Guide, slug=slug)
    # Any staff guide can be opened from Help; only your own role's guides are on your training list.
    if not (item.published or request.user.is_superuser) or not (can_see(request.user, item) or set(item.audiences) & set(STAFF_AUDIENCES)):
        raise PermissionDenied
    from apps.internal_ui.views import _staff_context
    from apps.staffing.my_views import my_tabs
    return _guide_page(request, item, "training/guide.html", _staff_context(
        request, page_title="My stuff", my_tabs=my_tabs(request, "training")))


@login_required
@require_GET
def steps(request, slug):
    item = get_object_or_404(Guide, slug=slug)
    portal = bool(getattr(request, "portal_account", None)) or request.GET.get("portal") in ("participant", "customer")
    if not (can_see(request.user, item) or portal and set(item.audiences) & {"participant", "customer"}):
        raise PermissionDenied
    return JsonResponse({"slug": item.slug, "title": item.title, "steps": steps_payload(item),
                         "done_url": reverse("training:done", args=[item.slug]),
                         "guide_url": reverse("training:guide", args=[item.slug]) if request.user.is_staff else ""})


@login_required
@require_POST
def done(request, slug):
    item = get_object_or_404(Guide, slug=slug)
    if not can_see(request.user, item) and not set(item.audiences) & {"participant", "customer"}:
        raise PermissionDenied
    if request.POST.get("undo") == "1":
        GuideProgress.objects.filter(user=request.user, guide=item).delete()
    else:
        GuideProgress.objects.get_or_create(user=request.user, guide=item, defaults={"completed_at": timezone.now()})
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": True})
    back = request.POST.get("next") or ""
    if back.startswith("/") and not back.startswith("//"):
        return redirect(back)
    return redirect("training:guide", slug=item.slug) if request.user.is_staff else redirect("/")


# ------------------------------------------------------------------ Settings → Training (admins)

def _people_progress(guides):
    from apps.accounts.roles import roles_for
    required = [g for g in guides if g.required and g.published]
    done = {}
    for row in GuideProgress.objects.filter(guide__in=required).values("user_id", "guide_id"):
        done.setdefault(row["user_id"], set()).add(row["guide_id"])
    people = []
    for user in get_user_model().objects.filter(is_active=True, is_staff=True).order_by("first_name", "last_name", "username"):
        roles = roles_for(user)
        from .access import audiences_for
        mine = audiences_for(user) & set(STAFF_AUDIENCES)
        theirs = [g for g in required if mine & set(g.audiences)]
        if not theirs:
            continue
        finished = len([g for g in theirs if g.pk in done.get(user.pk, set())])
        people.append({"name": user.get_full_name() or user.get_username(), "total": len(theirs), "done": finished,
                       "percent": round(100 * finished / len(theirs)),
                       "roles": ", ".join(dict(AUDIENCES)[r] for r in ("admin", "manager", "recruiter", "event_staff") if r in mine)})
    return people


@login_required(login_url="/admin/login/")
@require_GET
def manage(request):
    _admin(request)
    from apps.internal_ui.views import _staff_context
    guides = list(Guide.objects.prefetch_related("steps").order_by("position", "title"))
    counts = {}
    for row in GuideProgress.objects.values("guide_id"):
        counts[row["guide_id"]] = counts.get(row["guide_id"], 0) + 1
    for g in guides:
        g.done_count = counts.get(g.pk, 0)
    audience = request.GET.get("for", "")
    shown = [g for g in guides if not audience or audience in g.audiences]
    return render(request, "training/manage.html", _staff_context(
        request, page_title="Settings", guides=shown, audience=audience, audiences=AUDIENCES,
        people=_people_progress(guides), sections=HandbookSection.objects.all()))


class GuideForm(forms.ModelForm):
    audiences = forms.MultipleChoiceField(choices=AUDIENCES, widget=forms.CheckboxSelectMultiple, label="Who it's for")

    class Meta:
        model = Guide
        fields = ("title", "summary", "audiences", "minutes", "position", "required", "published", "video_url")
        labels = {"minutes": "About how many minutes", "position": "Order", "required": "On people's training list",
                  "published": "Visible", "video_url": "Video link"}


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def edit(request, slug=None):
    _admin(request)
    from apps.internal_ui.views import _staff_context
    item = get_object_or_404(Guide, slug=slug) if slug else Guide(audiences=["event_staff"])
    form = GuideForm(request.POST or None, instance=item)
    if request.method == "POST" and request.POST.get("delete") == "1" and item.pk:
        item.delete()
        messages.success(request, "Guide deleted.")
        return redirect("training:manage")
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        if not saved.slug:
            base = slugify(saved.title)[:70] or "guide"
            slug_try, n = base, 2
            while Guide.objects.filter(slug=slug_try).exists():
                slug_try, n = f"{base}-{n}", n + 1
            saved.slug = slug_try
        saved.save()
        # Steps: rows of step_title / step_body / step_page / step_target / step_image / step_delete
        titles = request.POST.getlist("step_title")
        bodies = request.POST.getlist("step_body")
        pages = request.POST.getlist("step_page")
        targets = request.POST.getlist("step_target")
        images = request.POST.getlist("step_image")
        removed = set(request.POST.getlist("step_delete"))
        saved.steps.all().delete()
        position = 0
        for i, title in enumerate(titles):
            if str(i) in removed or not title.strip():
                continue
            GuideStep.objects.create(guide=saved, position=position, title=title.strip()[:160],
                                     body=(bodies[i] if i < len(bodies) else "").strip(),
                                     page=(pages[i] if i < len(pages) else "").strip()[:200],
                                     target=(targets[i] if i < len(targets) else "").strip()[:200],
                                     image=(images[i] if i < len(images) else "").strip()[:200])
            position += 1
        messages.success(request, "Guide saved.")
        return redirect("training:edit", slug=saved.slug)
    rows = list(item.steps.all()) if item.pk else []
    return render(request, "training/edit.html", _staff_context(
        request, page_title="Settings", form=form, guide=item, step_rows=rows))



# ------------------------------------------------------------------ Sign-in messages

@login_required
@require_POST
def announcement_answer(request, pk):
    """Answer a sign-in message: open its link, say "got it", or be reminded at the next sign-in."""
    from .announcements import due_for
    from .models import Announcement, AnnouncementSeen
    item = get_object_or_404(Announcement, pk=pk)
    action = request.POST.get("answer")
    back = request.POST.get("next") or "/"
    back = back if back.startswith("/") and not back.startswith("//") else "/"
    if action == "later":
        request.session.pop("announcements_due", None)
        return redirect(back)
    AnnouncementSeen.objects.get_or_create(announcement=item, user=request.user)
    if due_for(request.user) is None:
        request.session.pop("announcements_due", None)
    url = item.button_url or ""
    if action == "go" and (url.startswith("/") and not url.startswith("//") or url.startswith("https://")):
        return redirect(url)
    return redirect(back)


class AnnouncementForm(forms.ModelForm):
    audiences = forms.MultipleChoiceField(choices=AUDIENCES, widget=forms.CheckboxSelectMultiple, label="Who sees it")

    class Meta:
        model = Announcement
        fields = ("title", "body", "audiences", "button_label", "button_url", "starts_on", "ends_on", "active")
        labels = {"body": "Message", "button_label": "Button text", "button_url": "Button link",
                  "starts_on": "Show from", "ends_on": "Stop showing after", "active": "Turned on"}
        widgets = {"body": forms.Textarea(attrs={"rows": 5}), "starts_on": forms.DateInput(attrs={"type": "date"}),
                   "ends_on": forms.DateInput(attrs={"type": "date"})}

    def clean_button_url(self):
        url = (self.cleaned_data.get("button_url") or "").strip()
        if url and not (url.startswith("/") and not url.startswith("//") or url.startswith("https://")):
            raise forms.ValidationError("Use a page in the app (like /my/training/) or a full https:// link.")
        return url


@login_required(login_url="/admin/login/")
@require_GET
def announcements(request):
    _admin(request)
    from apps.internal_ui.views import _staff_context
    from .models import Announcement
    rows = list(Announcement.objects.all())
    for row in rows:
        row.seen_count = row.seen.count()
    return render(request, "training/announcements.html", _staff_context(request, page_title="Settings", rows=rows))


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def announcement_edit(request, pk=None):
    _admin(request)
    from apps.internal_ui.views import _staff_context
    from .models import Announcement
    item = get_object_or_404(Announcement, pk=pk) if pk else Announcement(audiences=list(STAFF_AUDIENCES))
    if request.method == "POST" and request.POST.get("delete") == "1" and item.pk:
        item.delete()
        messages.success(request, "Message deleted.")
        return redirect("training:announcements")
    if request.method == "POST" and request.POST.get("reset") == "1" and item.pk:
        item.seen.all().delete()
        messages.success(request, "Everyone will see it again at their next sign-in.")
        return redirect("training:announcement-edit", pk=item.pk)
    form = AnnouncementForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        if not saved.pk:
            saved.created_by = request.user
        saved.save()
        messages.success(request, "Saved. People will see it the next time they sign in.")
        return redirect("training:announcements")
    return render(request, "training/announcement_edit.html", _staff_context(
        request, page_title="Settings", form=form, item=item, seen_count=item.seen.count() if item.pk else 0))


# ------------------------------------------------------------------ Workflow handbook

def sections_for(user):
    wanted = audiences_for(user)
    return [s for s in HandbookSection.objects.filter(published=True) if wanted & set(s.audiences or [])]


@login_required(login_url="/accounts/login/")
@require_GET
def handbook(request):
    if not request.user.is_staff:
        raise PermissionDenied
    from apps.internal_ui.views import _staff_context
    from .handbook import render as render_text
    sections = sections_for(request.user)
    titles = dict(Guide.objects.filter(published=True).values_list("slug", "title"))
    allowed = {g.slug for g in guides_for(audiences_for(request.user))}
    for s in sections:
        s.html = render_text(s.body)
        s.guide_links = [(slug, titles[slug]) for slug in s.guides or [] if slug in titles and slug in allowed]
    return render(request, "training/handbook.html", _staff_context(request, page_title="Workflow handbook", sections=sections))


class SectionForm(forms.ModelForm):
    audiences = forms.MultipleChoiceField(choices=[(k, v) for k, v in AUDIENCES if k in STAFF_AUDIENCES],
                                          widget=forms.CheckboxSelectMultiple, label="Who sees it")
    guides = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple, label="Link these guides at the end")

    class Meta:
        model = HandbookSection
        fields = ("title", "summary", "audiences", "body", "guides", "position", "published")
        labels = {"position": "Order", "published": "Visible", "body": "Text"}
        widgets = {"body": forms.Textarea(attrs={"rows": 18})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["guides"].choices = list(Guide.objects.exclude(audiences=["customer"]).exclude(audiences=["participant"])
                                             .order_by("position").values_list("slug", "title"))


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def section_edit(request, slug=None):
    _admin(request)
    from apps.internal_ui.views import _staff_context
    item = get_object_or_404(HandbookSection, slug=slug) if slug else HandbookSection(audiences=list(STAFF_AUDIENCES))
    form = SectionForm(request.POST or None, instance=item)
    if request.method == "POST" and request.POST.get("delete") == "1" and item.pk:
        item.delete()
        messages.success(request, "Section deleted.")
        return redirect("training:manage")
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        if not saved.slug:
            base = slugify(saved.title)[:70] or "section"
            slug_try, n = base, 2
            while HandbookSection.objects.filter(slug=slug_try).exists():
                slug_try, n = f"{base}-{n}", n + 1
            saved.slug = slug_try
        saved.save()
        messages.success(request, "Section saved.")
        return redirect("training:section-edit", slug=saved.slug)
    return render(request, "training/section_edit.html", _staff_context(request, page_title="Settings", form=form, section=item))
