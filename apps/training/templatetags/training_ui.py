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


@register.inclusion_tag("training/page_help.html", takes_context=True)
def page_help(context):
    request = context.get("request")
    from django.conf import settings
    found = None
    if getattr(settings, "PAGE_HELP_ENABLED", True) and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        try:
            from apps.training.page_help import guide_for
            found = guide_for(request)
        except Exception:
            found = None  # never break a page over a help link
    return {"found": found}
