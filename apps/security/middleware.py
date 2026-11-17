"""Ask staff with two-step sign-in for their code before any staff page."""
from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse

OPEN = ("/accounts/two-step/", "/accounts/logout/", "/static/", "/media/", "/health/", "/favicon", "/pay/", "/onboard/",
        "/billing/stripe/webhook/", "/messaging/eztexting/", "/messaging/gmail/callback/", "/privacy/", "/terms/")


class TwoStepMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_staff and not request.path.startswith(OPEN):
            from . import services
            state = request.session.get(services.SESSION_KEY, "")
            if state != f"ok:{user.pk}":
                if not state.endswith(f":{user.pk}"):
                    state = f"{services.state_for(user)}:{user.pk}"
                    request.session[services.SESSION_KEY] = state
                kind = state.split(":", 1)[0]
                if kind == "pending":
                    return redirect(reverse("security:verify") + "?" + urlencode({"next": request.get_full_path()}))
                if kind == "setup":
                    return redirect(reverse("security:setup") + "?required=1")
        return self.get_response(request)
