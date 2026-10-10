from django.urls import path
from . import views
app_name = "events"
urlpatterns = [
    path("ticket/<uuid:token>/", views.ticket_present, name="ticket_present"),
    path("ticket/<uuid:token>/image/", views.ticket_image, name="ticket_image"),
    path("ticket-number/<str:ticket_number>/", views.ticket_lookup, name="ticket_lookup"),
    path("tickets/recovery/", views.ticket_recovery, name="ticket_recovery"),
    path("tickets/<uuid:ticket_id>/reprint/", views.ticket_reprint, name="ticket_reprint"),
    path("", views.event_list, name="event_list"),
    path("create/", views.event_create, name="event_create"),
    path("<uuid:event_id>/tickets/bulk/", views.bulk_tickets, name="bulk_tickets"),
    path("<uuid:event_id>/registrations/<uuid:registration_id>/group/", views.registration_group_change, name="registration_group_change"),
    path("<uuid:event_id>/registrations/<uuid:registration_id>/answers/", views.registration_answers_correct, name="registration_answers_correct"),
    path("<uuid:event_id>/stations/", views.event_station_assign, name="station_assign"),
    path("<uuid:event_id>/", views.event_detail, name="event_detail"),
    path("<uuid:event_id>/edit/", views.event_edit, name="event_edit"),
    path("<uuid:event_id>/registrations/<uuid:registration_id>/", views.registration_detail, name="registration_detail"),
    path("<uuid:event_id>/registrations/<uuid:registration_id>/check-in/", views.registration_check_in, name="registration_check_in"),
    path("<uuid:event_id>/registrations/<uuid:registration_id>/reissue/", views.registration_reissue, name="registration_reissue"),
    path("<uuid:event_id>/registrations/<uuid:registration_id>/void/", views.registration_void, name="registration_void"),
]