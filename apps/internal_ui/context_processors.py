from .navigation import visible_workspaces

def workspace_navigation(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_active or not user.is_staff:
        return {"navigation": [], "task_creation_enabled": False}
    from apps.operations.notifications import shell_context
    match = getattr(request, "resolver_match", None)
    creating_task = bool(match and match.namespace == "operations" and match.url_name in (
        "quick-create", "quick-create-event", "quick-create-participant", "task-add", "event-task-add"))
    return {"navigation": visible_workspaces(user, request.path),
            "task_creation_enabled": not creating_task, **shell_context(user)}
