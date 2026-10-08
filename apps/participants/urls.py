from django.urls import path
from . import views, testing_views, registration_views
app_name = "participants"
urlpatterns = [
    path("register/", registration_views.registration_start, name="registration-start"),
    path("register/event/<uuid:event_id>/", registration_views.registration_start, name="registration-event-start"),
    path("register/form/", registration_views.registration_form, name="registration-form"),
    path("staff/registration-forms/", registration_views.form_builder, name="registration-forms"),
    path("staff/registration-forms/questions/add/", registration_views.form_question_add, name="registration-question-add"),
    path("staff/registration-forms/questions/<int:question_id>/", registration_views.form_question_edit, name="registration-question-edit"),
    path("staff/registration-forms/preview/", registration_views.form_preview, name="registration-form-preview"),
    path("staff/registration-forms/publish/", registration_views.form_publish, name="registration-form-publish"),

    path("staff/testing/", testing_views.test_participant_admin, name="testing"),
    path("request-account/", views.account_request, name="account-request"),
    path("verify/<str:token>/", views.verify_request, name="verify-request"),
    path("set-password/<str:token>/", views.set_password, name="set-password"),
    path("login/", views.participant_login, name="login"),
    path("logout/", views.participant_logout, name="logout"),
    path("profile/", views.profile, name="profile"),
    path("profile/email/", views.request_email_change, name="request-email-change"),
    path("profile/email/verify/<str:token>/", views.verify_email_change, name="verify-email-change"),
    path("password-reset/", views.password_reset_request, name="password-reset"),
    path("password-reset/confirm/<str:token>/", views.password_reset_confirm, name="password-reset-confirm"),
    path("staff/account-requests/", views.staff_requests, name="staff-requests"),
    path("staff/account-requests/<uuid:request_id>/", views.staff_resolve_request, name="staff-resolve-request"),
]
