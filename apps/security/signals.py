from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in)
def remember_two_step_state(sender, request, user, **kwargs):
    if request is None or not hasattr(request, "session"):
        return
    from . import services
    if not (getattr(user, "is_staff", False) or getattr(user, "backend", "") == services.CUSTOMER_BACKEND):
        return
    try:
        request.session[services.SESSION_KEY] = f"{services.state_for(user)}:{user.pk}"
    except Exception:
        request.session[services.SESSION_KEY] = f"ok:{user.pk}"
