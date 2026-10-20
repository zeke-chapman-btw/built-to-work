from .navigation import visible_workspaces

def workspace_navigation(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_staff:
        return {"navigation": []}
    from apps.operations.notifications import shell_context
    return {"navigation": visible_workspaces(user, request.path), **shell_context(user)}
