from django.urls import path

from django.urls import path
from . import views

app_name = "customers"
urlpatterns = [
    path("internal/customers/", views.directory, name="directory"),
    path("internal/campaigns/", views.campaign_directory, name="campaign-directory"),
    path("internal/customers/new/", views.organization_editor, name="new"),
    path("internal/customers/<uuid:org_id>/edit/", views.organization_editor, name="edit"),
    path("internal/customers/<uuid:org_id>/company-action/<str:action>/", views.organization_action, name="organization-action"),
    path("internal/customers/<uuid:org_id>/relationships/", views.relationship_editor, name="relationship"),
    path("internal/customers/<uuid:org_id>/plan/", views.plan_editor, name="plan"),
    path("internal/customers/<uuid:org_id>/records/<str:kind>/new/", views.child_editor, name="child-new"),
    path("internal/customers/<uuid:org_id>/records/<str:kind>/<uuid:record_id>/", views.child_editor, name="child-edit"),
    path("internal/customers/<uuid:org_id>/users/new/", views.membership_editor, name="member-new"),
    path("internal/customers/<uuid:org_id>/users/<uuid:record_id>/edit/", views.membership_editor, name="member-edit"),
    path("internal/customers/<uuid:org_id>/identities/<uuid:record_id>/", views.identity_editor, name="identity-edit"),
    path("internal/customers/<uuid:org_id>/primary-lead/", views.primary_editor, name="primary"),
    path("internal/customers/<uuid:org_id>/needs/new/", views.need_editor, name="need-new"),
    path("internal/customers/<uuid:org_id>/needs/<uuid:record_id>/", views.need_detail, name="need"),
    path("internal/customers/<uuid:org_id>/needs/<uuid:record_id>/edit/", views.need_editor, name="need-edit"),
    path("internal/customers/<uuid:org_id>/needs/<uuid:record_id>/action/<str:action>/", views.need_action, name="need-action"),
    path("internal/customers/<uuid:org_id>/campaigns/new/", views.campaign_editor, name="campaign-new"),
    path("internal/customers/<uuid:org_id>/campaigns/<uuid:record_id>/", views.campaign_detail, name="campaign"),
    path("internal/customers/<uuid:org_id>/campaigns/<uuid:record_id>/edit/", views.campaign_editor, name="campaign-edit"),
    path("internal/customers/<uuid:org_id>/campaigns/<uuid:record_id>/action/<str:action>/", views.campaign_action, name="campaign-action"),
    path("internal/customers/<uuid:org_id>/<str:tab>/", views.workspace, name="workspace"),
]
