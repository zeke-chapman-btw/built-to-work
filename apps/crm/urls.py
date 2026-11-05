from django.urls import path
from . import views

app_name = "crm"
urlpatterns = [
    path("participants/<int:participant_id>/new/", views.create, name="participant-new"),
    path("customers/<uuid:organization_id>/new/", views.create, name="customer-new"),
    path("customers/<uuid:organization_id>/contacts/<uuid:contact_id>/", views.contact, name="contact"),
    path("interactions/<uuid:interaction_id>/", views.detail, name="detail"),
    path("interactions/<uuid:interaction_id>/correct/", views.correct, name="correct"),
    path("interactions/<uuid:interaction_id>/task/", views.followup, name="followup"),
    path("interactions/<uuid:interaction_id>/<str:action>/", views.archive, name="archive"),
]
