from . import verification_views, staff_directory_views
from django.urls import path
from . import views, testing_views, registration_views, historical_views, qualification_views, document_views
app_name = "participants"
urlpatterns = [
    path("staff/directory/", staff_directory_views.staff_directory, name="staff-directory"),
    path("staff/directory/<str:participant_id>/", staff_directory_views.staff_profile, name="staff-profile"),
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
    path("staff/historical/", historical_views.historical_index, name="historical-index"),
    path("staff/historical/export/", historical_views.historical_export, name="historical-export"),
    path("staff/historical/<uuid:batch_id>/map/", historical_views.historical_map, name="historical-map"),
    path("staff/historical/<uuid:batch_id>/", historical_views.historical_batch, name="historical-batch"),
    path("staff/historical/review/<int:row_id>/", historical_views.historical_review, name="historical-review"),
    path("staff/qualifications/", qualification_views.qualification_directory, name="staff-qualifications"),
    path("staff/qualifications/<int:participant_id>/", qualification_views.participant_qualifications, name="staff-participant-qualifications"),
    path("staff/qualifications/<int:participant_id>/<int:qualification_id>/edit/", qualification_views.participant_qualifications, name="staff-participant-qualification-edit"),
]

urlpatterns += [
    path("staff/documents/", document_views.document_directory, name="staff-document-directory"),
    path("staff/documents/categories/", document_views.document_categories, name="staff-document-categories"),
    path("staff/documents/<int:participant_id>/", document_views.participant_documents, name="staff-participant-documents"),
    path("staff/documents/file/<uuid:document_uuid>/", document_views.document_download, name="staff-document-download"),
    path("staff/documents/file/<uuid:document_uuid>/edit/", document_views.document_edit, name="staff-document-edit"),
    path("staff/documents/file/<uuid:document_uuid>/archive/", document_views.document_archive, name="staff-document-archive"),
    path("staff/documents/file/<uuid:document_uuid>/replace/", document_views.document_replace, name="staff-document-replace"),
    path("staff/documents/file/<uuid:document_uuid>/qualification/<int:qualification_id>/link/", document_views.qualification_document_link, name="staff-document-qualification-link"),
    path("staff/documents/file/<uuid:document_uuid>/qualification/<int:qualification_id>/unlink/", document_views.qualification_document_unlink, name="staff-document-qualification-unlink"),
    path("staff/approvals/", verification_views.qualification_approvals, name="staff-qualification-approvals"),
    path("staff/approvals/<str:kind>/<int:object_id>/", verification_views.qualification_approval_detail, name="staff-qualification-approval-detail"),
]
