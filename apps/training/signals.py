"""After anyone signs in, check for sign-in messages meant for them."""
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

SESSION_KEY = "announcements_due"


@receiver(user_logged_in)
def check_announcements(sender, request, user, **kwargs):
    if request is None:
        return
    try:
        from .announcements import due_for
        if due_for(user):
            request.session[SESSION_KEY] = True
    except Exception:
        pass  # never block a sign-in over this
