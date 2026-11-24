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
from .models import AUDIENCES, STAFF_AUDIENCES, Guide, GuideProgress, GuideStep


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
        people=_people_progress(guides)))


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
