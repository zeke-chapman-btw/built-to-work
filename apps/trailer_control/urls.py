from django.urls import path
from . import views,display_views
app_name="trailer_control"
urlpatterns=[
    path("internal/trailer-control/",views.dashboard,name="index"),
    path("internal/trailer-control/status/",views.status,name="status"),
    path("internal/trailer-control/devices/",views.devices,name="devices"),
    path("internal/trailer-control/sync/",views.sync_center,name="sync"),
    path("internal/trailer-control/publication/",views.publication,name="publication"),
    path("api/trailer-control/devices/<uuid:device_id>/heartbeat/",display_views.device_heartbeat,name="device-heartbeat"),
    path("trailer-displays/<uuid:binding_id>/",display_views.display,name="display"),
    path("trailer-displays/<uuid:binding_id>/data/",display_views.data,name="display-data"),
    path("trailer-displays/<uuid:binding_id>/heartbeat/",display_views.display_heartbeat,name="display-heartbeat"),
    path("trailer-displays/<uuid:binding_id>/media/",display_views.media,name="media"),
]
