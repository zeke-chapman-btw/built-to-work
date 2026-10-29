from django.urls import path
from . import staff_views
app_name='accounts'
urlpatterns=[
 path('staff/',staff_views.index,name='staff-index'),
 path('staff/create/',staff_views.edit,name='staff-create'),
 path('staff/<uuid:user_id>/',staff_views.detail,name='staff-detail'),
 path('staff/<uuid:user_id>/edit/',staff_views.edit,name='staff-edit'),
 path('staff/<uuid:user_id>/<str:action>/',staff_views.action,name='staff-action'),
 path('activate/<str:token>/',staff_views.activate,name='activate'),
]
