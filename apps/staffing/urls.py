from django.urls import path
from . import views
app_name="staffing"
urlpatterns=[
    path("",views.schedule,name="schedule"),
    path("directory/",views.directory,name="directory"),
    path("directory/add/",views.profile_edit,name="profile-add"),
    path("directory/<uuid:profile_id>/",views.profile,name="profile"),
    path("directory/<uuid:profile_id>/edit/",views.profile_edit,name="profile-edit"),
    path("directory/<uuid:profile_id>/account/",views.account_link,name="account-link"),
    path("requests/",views.request_queue,name="queue"),
    path("requests/<uuid:request_id>/<str:decision>/",views.request_decision,name="request-decision"),
    path("events/<uuid:event_id>/",views.staff_event,name="staff-event"),
    path("events/<uuid:event_id>/staffing/",views.event_staffing,name="event"),
    path("events/<uuid:event_id>/staffing/settings/",views.plan_edit,name="plan-edit"),
    path("events/<uuid:event_id>/staffing/assign/",views.assignment_edit,name="assignment-add"),
    path("events/<uuid:event_id>/staffing/<uuid:assignment_id>/edit/",views.assignment_edit,name="assignment-edit"),
    path("assignments/<uuid:assignment_id>/remove/",views.assignment_remove,name="assignment-remove"),
]
