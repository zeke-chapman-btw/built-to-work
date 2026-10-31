from django.urls import path
from . import views
from apps.participants import portal_preview
from apps.customers import portal_preview as customer_preview

app_name = "internal"
urlpatterns = [
    path("", views.home, name="home"),
    path("quick-actions/", views.quick_actions_edit, name="quick-actions-edit"),
    path("settings/", views.settings, name="settings"),
    path("settings/portal-preview/", portal_preview.index, name="portal-preview"),
    path("settings/portal-preview/customer/", customer_preview.enter, name="portal-preview-customer-enter"),
    path("settings/portal-preview/customer/return/", customer_preview.exit, name="portal-preview-customer-exit"),
    path("settings/portal-preview/participant/", portal_preview.enter, name="portal-preview-enter"),
    path("settings/portal-preview/return/", portal_preview.exit, name="portal-preview-exit"),
    path("reports/", views.reports, name="reports"),
    path("workspaces/<slug:workspace_id>/", views.workspace, name="workspace"),
]
