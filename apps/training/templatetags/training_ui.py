from django import template

register = template.Library()


@register.inclusion_tag("training/announcement_popup.html", takes_context=True)
def sign_in_message(context):
    request = context.get("request")
    data = {"item": None, "request": request}
    from django.conf import settings
    session = getattr(request, "session", None)
    if not (getattr(settings, "SIGN_IN_MESSAGES_ENABLED", True) and session is not None
            and getattr(request, "user", None) and request.user.is_authenticated and session.get("announcements_due")):
        return data
    from apps.training.announcements import due_for
    item = due_for(request.user)
    if item is None:
        request.session.pop("announcements_due", None)
    data["item"] = item
    return data
