from django.urls import path

from . import help_requests, views

app_name = "training"
urlpatterns = [
    path("my/training/", views.mine, name="mine"),
    path("training/messages/<int:pk>/answer/", views.announcement_answer, name="announcement-answer"),
    path("settings/sign-in-messages/", views.announcements, name="announcements"),
    path("settings/sign-in-messages/new/", views.announcement_edit, name="announcement-new"),
    path("settings/sign-in-messages/<int:pk>/", views.announcement_edit, name="announcement-edit"),
    path("my/training/<slug:slug>/", views.guide, name="guide"),
    path("training/<slug:slug>/steps.json", views.steps, name="steps"),
    path("training/<slug:slug>/done/", views.done, name="done"),
    path("settings/training/", views.manage, name="manage"),
    path("help/handbook/", views.handbook, name="handbook"),
    path("help/ask/", help_requests.ask, name="ask"),
    path("help/questions/", help_requests.inbox, name="help-requests"),
    path("help/questions/<int:pk>/handled/", help_requests.handled, name="help-request-handled"),
    path("settings/training/handbook/new/", views.section_edit, name="section-new"),
    path("settings/training/handbook/<slug:slug>/", views.section_edit, name="section-edit"),
    path("settings/training/new/", views.edit, name="new"),
    path("settings/training/<slug:slug>/", views.edit, name="edit"),
]
