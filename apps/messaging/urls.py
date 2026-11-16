from django.urls import path

from . import connections, views

app_name = "messaging"
urlpatterns = [
    path("connections/", connections.connections, name="connections"),
    path("connections/texting/", connections.edit_text, name="connections-text"),
    path("connections/email/", connections.edit_email, name="connections-email"),
    path("connections/texting/test/", connections.test_text, name="connections-text-test"),
    path("connections/email/test/", connections.test_email, name="connections-email-test"),
    path("connections/sending/", connections.toggle_sending, name="connections-sending"),
    path("gmail/connect/", views.gmail_connect, name="gmail-connect"),
    path("gmail/callback/", views.gmail_callback, name="gmail-callback"),
    path("gmail/disconnect/", views.gmail_disconnect, name="gmail-disconnect"),
    path("participants/<int:participant_id>/email/", views.email_participant, name="email-participant"),
]
