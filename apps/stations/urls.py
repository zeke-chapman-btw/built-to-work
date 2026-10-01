from django.urls import path

from . import views

app_name = "stations"
urlpatterns = [
    path("", views.station_list, name="list"),
    path("create/", views.station_create, name="create"),
    path("<uuid:station_id>/", views.station_detail, name="detail"),
    path("<uuid:station_id>/edit/", views.station_edit, name="edit"),
    path("<uuid:station_id>/assign/", views.station_assign, name="assign"),
    path("assignments/<int:assignment_id>/activate/", views.assignment_activate, name="assignment_activate"),
    path("operate/<slug:station_code>/", views.station_operate, name="operate"),
    path("sessions/<uuid:session_id>/", views.session_detail, name="session_detail"),
]
