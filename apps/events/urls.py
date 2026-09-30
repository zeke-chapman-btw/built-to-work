from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("ticket/<uuid:token>/", views.ticket_present, name="ticket_present"),
    path("", views.event_list, name="event_list"),
    path("create/", views.event_create, name="event_create"),
    path("<uuid:event_id>/", views.event_detail, name="event_detail"),
    path("<uuid:event_id>/edit/", views.event_edit, name="event_edit"),
    path("<uuid:event_id>/registrations/<uuid:registration_id>/", views.registration_detail, name="registration_detail"),
    path("<uuid:event_id>/registrations/<uuid:registration_id>/check-in/", views.registration_check_in, name="registration_check_in"),
    path("<uuid:event_id>/registrations/<uuid:registration_id>/reissue/", views.registration_reissue, name="registration_reissue"),
    path("<uuid:event_id>/registrations/<uuid:registration_id>/void/", views.registration_void, name="registration_void"),
]
