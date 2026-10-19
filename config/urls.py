from django.contrib import admin
from django.urls import include, path
from apps.core.views import health, home
from apps.events import public_views

urlpatterns = [
    path("internal/", include("apps.internal_ui.urls")),
    path("admin/", admin.site.urls),
    path("participants/", include("apps.participants.urls")),
    path("stations/", include("apps.stations.urls")),
    path("events/", include("apps.events.urls")),
    path("staff/", include("apps.staffing.urls")),
    path("e/<slug:slug>/", public_views.public_event_registration, name="public-event-registration"),
    path("quizzes/", include("apps.assessments.urls")),
    path("games/", include("apps.games.urls")),
    path("api/simulator/", include("apps.simulators.urls")),
    path("", home, name="home"),
    path("health/", health, name="health"),
]
