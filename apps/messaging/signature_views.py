"""Paste-in email signatures (your own, and the company one for automatic email)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.accounts.roles import active_staff

from . import signatures, store
from .models import EmailSignature


def _editor(request, *, title, intro, current, save, back, company=False):
    if request.method == "POST":
        if request.POST.get("clear") == "1":
            save("")
            messages.success(request, "Signature removed.")
            return redirect(back)
        html = signatures.clean_html(request.POST.get("signature_html", ""))
        save(html)
        messages.success(request, "Signature saved. It will be added to emails from now on." if html else "Signature removed.")
        return redirect(back)
    return render(request, "messaging/signature_page.html", {
        "page_title": title, "intro": intro, "current": current, "back": back, "company": company})


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def my_signature(request):
    if not active_staff(request.user):
        raise PermissionDenied

    def save(html):
        EmailSignature.objects.update_or_create(user=request.user, defaults={"html": html})

    return _editor(request, title="Your email signature",
                   intro="Paste your usual BTW signature below (copy it from Gmail → Settings → Signature, or from the bottom of a recent email). It is added to emails you send to participants from the app.",
                   current=signatures.personal(request.user), save=save, back=reverse("my:account") + "#email")


@login_required(login_url="/admin/login/")
@require_http_methods(["GET", "POST"])
def company_signature(request):
    if not request.user.is_superuser:
        raise PermissionDenied

    def save(html):
        store.save(key="signature", actor=request.user, values={"html": html})

    return _editor(request, title="Company email signature",
                   intro="Added to automatic emails from noreply@ (ticket confirmations, reminders and alerts). Paste it the same way as a personal signature.",
                   current=signatures.company(), save=save, back=reverse("messaging:connections") + "#signature", company=True)
