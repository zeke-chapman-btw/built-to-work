from django.urls import path
from . import views

app_name = "sops"
urlpatterns = [
    path("", views.index, name="index"),
    path("library/", views.manage, name="manage"),
    path("library/new/", views.edit, name="add"),
    path("library/<uuid:sop_id>/edit/", views.edit, name="edit"),
    path("library/<uuid:sop_id>/<str:verb>/", views.action, name="action"),
    path("<uuid:sop_id>/", views.detail, name="detail"),
    path("<uuid:sop_id>/file/", views.pdf, name="file"),
]
