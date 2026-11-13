from django.http import JsonResponse


def home(request):
    """The site address opens the one shared sign-in, which sends each person to their own area."""
    from django.shortcuts import redirect
    return redirect("accounts:login")


def admin_login(request):
    """Everyone signs in on the shared sign-in page; the old admin sign-in forwards there."""
    from urllib.parse import urlencode
    from django.shortcuts import redirect
    from django.urls import reverse
    nxt = request.GET.get("next", "")
    return redirect(reverse("accounts:login") + ("?" + urlencode({"next": nxt}) if nxt and nxt not in ("/admin/", "/admin") else ""))


def health(request):
    return JsonResponse({"status": "ok"})