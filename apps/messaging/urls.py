from django.urls import path

from . import views

app_name = "messaging"
urlpatterns = [
    path("gmail/connect/", views.gmail_connect, name="gmail-connect"),
    path("gmail/callback/", views.gmail_callback, name="gmail-callback"),
    path("gmail/disconnect/", views.gmail_disconnect, name="gmail-disconnect"),
    path("participants/<int:participant_id>/email/", views.email_participant, name="email-participant"),
]
