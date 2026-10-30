from django.urls import path
from . import portal_views as v, portal_auth as a, portal_staff as staff
app_name = 'customer_portal'
urlpatterns = [
    path('', v.workspace, name='home'),
    path('auth/login/', a.sign_in, name='login'),
    path('auth/logout/', a.sign_out, name='logout'),
    path('auth/recovery/', a.recovery, name='recovery'),
    path('auth/recovery/<str:token>/', a.reset_confirm, name='reset-confirm'),
    path('auth/invitation/<str:token>/', a.accept, name='accept'),
    path('switch/', v.switch_company, name='switch'),
    path('workspace/<str:tab>/', v.workspace, name='workspace'),
    path('needs/new/', v.need_editor, name='need-create'),
    path('needs/<uuid:record_id>/', v.need_detail, name='need'),
    path('needs/<uuid:record_id>/edit/', v.need_editor, name='need-edit'),
    path('needs/<uuid:record_id>/<str:action>/', v.need_action, name='need-action'),
    path('campaigns/new/', v.campaign_editor, name='campaign-create'),
    path('campaigns/<uuid:record_id>/', v.campaign_detail, name='campaign'),
    path('campaigns/<uuid:record_id>/edit/', v.campaign_editor, name='campaign-edit'),
    path('campaigns/<uuid:record_id>/<str:action>/', v.campaign_action, name='campaign-action'),
    path('members/invite/', v.member_invite, name='invite'),
    path('members/<uuid:record_id>/edit/', v.member_editor, name='member-edit'),
    path('members/<uuid:record_id>/<str:action>/', v.invitation_action, name='invitation-action'),
    path('staff/<uuid:org_id>/invitations/', staff.members, name='staff-members'),
    path('staff/<uuid:org_id>/invitations/<uuid:record_id>/<str:action>/', staff.invitation_action, name='staff-invitation'),
    path('staff/<uuid:org_id>/<str:kind>/<uuid:record_id>/feedback/', staff.feedback, name='staff-feedback'),
]
