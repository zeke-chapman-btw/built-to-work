from django.urls import path

from apps.internal_ui import help_views

from . import views

app_name = "security"
urlpatterns = [
    path("accounts/two-step/", views.verify, name="verify"),
    path("accounts/two-step/setup/", views.setup, name="setup"),
    path("accounts/two-step/off/", views.turn_off, name="off"),
    path("accounts/two-step/codes/", views.new_codes, name="codes"),
    path("settings/security/", views.settings_page, name="settings"),
    path("settings/security/require/<uuid:user_id>/", views.require_person, name="require-person"),
    path("settings/security/copy-setup/", views.setup_copy, name="setup-copy"),
    path("settings/security/legal/<slug:slug>/", views.legal_edit, name="legal-edit"),
    path("help/", help_views.guide, name="help"),
    path("privacy/", views.legal, {"slug": "privacy"}, name="privacy"),
    path("terms/", views.legal, {"slug": "terms"}, name="terms"),
]
