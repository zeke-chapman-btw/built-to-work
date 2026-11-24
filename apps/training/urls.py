from django.urls import path

from . import views

app_name = "training"
urlpatterns = [
    path("my/training/", views.mine, name="mine"),
    path("my/training/<slug:slug>/", views.guide, name="guide"),
    path("training/<slug:slug>/steps.json", views.steps, name="steps"),
    path("training/<slug:slug>/done/", views.done, name="done"),
    path("settings/training/", views.manage, name="manage"),
    path("settings/training/new/", views.edit, name="new"),
    path("settings/training/<slug:slug>/", views.edit, name="edit"),
]
