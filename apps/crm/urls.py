from django.urls import path
from . import views, portal_views

app_name = "crm"
urlpatterns = [
    path("recent/", views.recent, name="recent"),
    path("new/", views.start, name="start"),
    path("new/<str:goal>/", views.start, name="start-for"),
    path("employer/<uuid:disclosure_id>/new/", portal_views.create, name="employer-new"),
    path("employer/<uuid:disclosure_id>/<uuid:interaction_id>/", portal_views.detail, name="employer-detail"),
    path("employer/<uuid:disclosure_id>/<uuid:interaction_id>/correct/", portal_views.correct, name="employer-correct"),
    path("employer/<uuid:disclosure_id>/<uuid:interaction_id>/<str:action>/", portal_views.archive, name="employer-archive"),
    path("participants/<int:participant_id>/new/", views.create, name="participant-new"),
    path("customers/<uuid:organization_id>/new/", views.create, name="customer-new"),
    path("customers/<uuid:organization_id>/contacts/<uuid:contact_id>/", views.contact, name="contact"),
    path("interactions/<uuid:interaction_id>/", views.detail, name="detail"),
    path("interactions/<uuid:interaction_id>/correct/", views.correct, name="correct"),
    path("interactions/<uuid:interaction_id>/task/", views.followup, name="followup"),
    path("interactions/<uuid:interaction_id>/link-task/", views.existing_task, name="link-task"),
    path("interactions/<uuid:interaction_id>/<str:action>/", views.archive, name="archive"),
]
