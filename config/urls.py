from django.contrib import admin
from django.urls import include, path
from apps.core.views import health, home

urlpatterns = [
    path("admin/", admin.site.urls),
    path("participants/", include("apps.participants.urls")),
    path("", home, name="home"),
    path("health/", health, name="health"),
]
