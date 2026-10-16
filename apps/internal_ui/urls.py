from django.urls import path
from . import views

app_name = "internal"
urlpatterns = [
    path("", views.home, name="home"),
    path("quick-actions/", views.quick_actions_edit, name="quick-actions-edit"),
    path("settings/", views.settings, name="settings"),
    path("workspaces/<slug:workspace_id>/", views.workspace, name="workspace"),
]
