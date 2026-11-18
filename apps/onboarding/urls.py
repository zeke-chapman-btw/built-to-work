from django.urls import path

from . import documents, views

app_name = "onboarding"
urlpatterns = [
    path("customers/onboarding/", views.onboarding_list, name="list"),
    path("customers/onboarding/start/", views.start, name="start"),
    path("customers/onboarding/<uuid:pk>/send/", views.send, name="send"),
    path("customers/onboarding/<uuid:pk>/cancel/", views.cancel, name="cancel"),
    path("customers/onboarding/<uuid:pk>/create/", views.create, name="create"),
    path("customers/onboarding/<uuid:pk>/w9/", views.w9, name="w9"),
    path("customers/onboarding/<uuid:pk>/<slug:step>/", views.staff_step, name="step"),
    path("customers/<uuid:org_id>/documents/new/", documents.edit, name="document-new"),
    path("customers/<uuid:org_id>/documents/<int:pk>/", documents.edit, name="document-edit"),
    path("customers/<uuid:org_id>/documents/<int:pk>/file/", documents.download, name="document-file"),
    path("customers/setup/", views.settings_page, name="settings"),
    path("customers/setup/questions/new/", views.question_edit, name="question-new"),
    path("customers/setup/questions/<int:pk>/", views.question_edit, name="question-edit"),
    path("customers/setup/agreement/", views.agreement_edit, name="agreement"),
    path("onboard/<str:token>/", views.public, name="public"),
    path("onboard/<str:token>/<slug:step>/", views.public, name="public-step"),
]
