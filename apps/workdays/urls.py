from django.urls import path
from . import views
app_name='workdays'
urlpatterns=[
 path('',views.index,name='index'),
 path('mine/',views.mine,name='mine'),
 path('events/<uuid:event_id>/',views.event_roster,name='event'),
 path('events/<uuid:event_id>/check-in/',views.checkin,name='checkin'),
 path('events/<uuid:event_id>/add/',views.workday_edit,name='add'),
 path('records/<uuid:record_id>/',views.record,name='record'),
 path('records/<uuid:record_id>/edit/',views.workday_edit,name='edit'),
 path('records/<uuid:record_id>/<str:action>/',views.record_action,name='action'),
 path('events/<uuid:event_id>/distance/',views.distance_edit,name='distance'),
 path('events/<uuid:event_id>/trips/add/',views.trip_edit,name='trip-add'),
 path('trips/<uuid:trip_id>/edit/',views.trip_edit,name='trip-edit'),
 path('trips/<uuid:trip_id>/<str:action>/',views.trip_action,name='trip-action'),
 path('reports/',views.report,name='reports'),
 path('compensation/',views.settings,name='settings'),
 path('compensation/roles/add/',views.role_edit,name='role-add'),
 path('compensation/roles/<uuid:role_id>/',views.role_edit,name='role-edit'),
 path('compensation/rates/<str:kind>/',views.rate_edit,name='rate-edit'),
]
