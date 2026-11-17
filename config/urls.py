from django.contrib import admin
from django.urls import include, path
from config.url_moves import relocate
from apps.core.views import admin_login, health, home
from apps.events import public_views

admin.site.site_header = "Built to Work admin"
admin.site.site_title = "Built to Work admin"
admin.site.index_title = "Admin"
admin.site.site_url = "/home/"

_forwards = []


def _app(module, prefix):
    patterns, forwards, app_name = relocate(module, prefix)
    _forwards.extend(forwards)
    return path("", include((patterns, app_name), namespace=app_name))

urlpatterns = [
    _app("apps.crm.urls", "crm/"),
    _app("apps.recruiting.urls", "recruiting/"),
    _app("apps.trailer_control.urls", ""),
    _app("apps.customers.portal_urls", "customer/"),
    _app("apps.customers.urls", ""),
    _app("apps.accounts.urls", "accounts/"),
    _app("apps.event_reports.urls", "reports/"),
    _app("apps.reporting.urls", "internal/reports/"),
    _app("apps.internal_ui.urls", "internal/"),
    path("admin/login/", admin_login, name="admin-login-redirect"),
    path("admin/", admin.site.urls),
    _app("apps.participants.urls", "participants/"),
    _app("apps.stations.urls", "stations/"),
    _app("apps.events.urls", "events/"),
    _app("apps.staffing.urls", "staff/"),
    _app("apps.operations.urls", "operations/"),
    _app("apps.workdays.urls", "workdays/"),
    path("e/<slug:slug>/", public_views.public_event_registration, name="public-event-registration"),
    _app("apps.assessments.urls", "quizzes/"),
    _app("apps.games.urls", "games/"),
    _app("apps.simulators.urls", "api/simulator/"),
    _app("apps.sops.urls", "sops/"),
    _app("apps.messaging.urls", "messaging/"),
    _app("apps.reminders.urls", "settings/reminders/"),
    _app("apps.staffing.my_urls", "my/"),
    path("", home, name="home"),
    path("health/", health, name="health"),
]
# Old addresses forward to the new ones (after everything else, so live pages always win).
urlpatterns += _forwards
