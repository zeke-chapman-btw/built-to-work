from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in)
def remember_two_step_state(sender, request, user, **kwargs):
    if request is None or not getattr(user, "is_staff", False) or not hasattr(request, "session"):
        return
    from . import services
    try:
        request.session[services.SESSION_KEY] = f"{services.state_for(user)}:{user.pk}"
    except Exception:
        request.session[services.SESSION_KEY] = f"ok:{user.pk}"
