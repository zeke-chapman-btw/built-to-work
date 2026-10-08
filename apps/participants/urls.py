from django.urls import path
from . import views, testing_views
app_name = "participants"
urlpatterns = [
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
