from django.urls import path

from .views import ingest_capture_view

app_name = "simulators"
urlpatterns = [path("captures/", ingest_capture_view, name="capture_ingest")]
