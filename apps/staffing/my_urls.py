from django.urls import path
from django.views.generic import RedirectView
from . import my_views

app_name = "my"
urlpatterns = [
    path("", RedirectView.as_view(pattern_name="my:schedule"), name="index"),
    path("schedule/", my_views.schedule, name="schedule"),
]
