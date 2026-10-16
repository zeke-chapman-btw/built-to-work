from .navigation import visible_workspaces

def workspace_navigation(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_staff:
        return {"navigation": []}
    return {"navigation": visible_workspaces(user, request.path)}
